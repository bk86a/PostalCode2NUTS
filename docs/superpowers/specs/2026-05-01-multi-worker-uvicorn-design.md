# Multi-worker uvicorn with Redis-backed rate limiting — design

**Status:** approved (brainstorming complete)
**Date:** 2026-05-01
**Issue:** [#68](https://github.com/bk86a/PostalCode2NUTS/issues/68)
**Scope:** allow the service to run with N uvicorn workers behind a shared rate-limit backend, preserving the strict per-IP cap that single-worker deployments rely on. Single-host PyPI/Docker deployers must remain unaffected.

---

## 1. Goals and non-goals

### Goals

- Allow `uvicorn` to run with `N > 1` workers via a single env var (`PC2NUTS_WORKERS`).
- Keep the per-IP rate-limit cap strict at `settings.rate_limit` (currently `120/minute`) across all workers, by routing slowapi storage through a shared backend (Redis) when configured.
- Keep the trusted-token bypass (`exempt_when=is_trusted_request`) working unchanged.
- Preserve the current single-worker, in-memory deploy path byte-for-byte when the new env vars are unset.
- Tolerate transient unavailability of the shared backend without taking the API down.
- Hard-fail at startup if the operator asks for `N > 1` without configuring shared storage, so the cap can never silently loosen.

### Non-goals

- Provisioning the Redis instance itself — operational concern, documented in `README.md` as a prerequisite for the multi-worker mode.
- Re-running the performance baseline and updating `docs/performance.md` — separate post-deploy task once the multi-worker container is in production.
- Edge-layer rate limiting (bunny.net Shield + Edge Rules) — investigated during brainstorming, not on the table without a documented header-conditional bypass mechanism. Left as a future option.
- Switching from `uvicorn --workers` to `gunicorn` — not needed for this workload (no streaming, no long-poll, requests <100 ms), and adds a process-manager dependency.
- Recycling worker processes after N requests, graceful timeouts on individual requests, or other lifecycle features.
- Implementation work — this document is the design only.

---

## 2. Background

### Why this is the next perf win

The performance baseline at commit `526f289` (`docs/performance.md`) measured sustained throughput at ~30 RPS on a single uvicorn worker. The bottleneck is per-request server-side CPU work (~30 ms in `/lookup` logic + Pydantic serialisation; the actual dict lookup is statistically free). Each additional worker should add roughly +30 RPS of headroom, up to the container CPU count. This is the single largest available perf win that requires no application-logic change.

### Why slowapi needs a shared backend

slowapi's default storage is in-memory (`MemoryStorage` from the `limits` library), per-process. With `N` workers, each worker holds its own counters, so the per-IP cap effectively becomes `N × settings.rate_limit`. The project's published cap is `120/minute` per anonymous IP, with trusted-token holders exempt — that contract must hold under multi-worker.

### Three rejected alternatives (recorded for the next time this is revisited)

- **Accept the loosening (option (c) in brainstorming).** Document that the published cap is per-process; aggregate is N× higher. Rejected: violates the published contract.
- **Edge-layer rate limiting via bunny.net (option (b)).** bunny.net Shield offers global per-IP rate limiting and Edge Rules support header-based logic, but combining the two — i.e. "rate-limit by IP, except when an Authorization header is present" — is not a documented configuration, and trusted-token holders would be throttled at the edge. Rejected without a support-ticket confirmation; left as a future option if bunny.net adds the capability.
- **Divide the cap by N at the worker level (`120/N` per worker).** Rejected: with HTTP keepalive (default for almost all clients), all of a client's rapid requests pin to the same worker, so they would hit `120/N` and be throttled even though aggregate budget allows 120. False-positive rate limits.

---

## 3. Configuration surface

Two new env vars, both with defaults preserving current behavior.

| Env var | Type | Default | Effect |
|---|---|---|---|
| `PC2NUTS_WORKERS` | `int ≥ 1` | `1` | Number of uvicorn worker processes. |
| `PC2NUTS_RATE_LIMIT_STORAGE_URI` | `str \| None` | `None` (unset) | When unset, slowapi uses in-memory storage (current behavior). When set (e.g. `redis://host:6379/0`), slowapi routes through the fail-degraded shared backend wrapper. |

Both surface in `app/config.py` (`Settings` model, `settings.json` defaults, env-var override per existing pattern). Naming follows the existing `PC2NUTS_*` convention.

### Startup validation

If `PC2NUTS_WORKERS > 1` and `PC2NUTS_RATE_LIMIT_STORAGE_URI` is unset/empty, the app refuses to start with a clear error message naming both env vars. Implemented as a Pydantic model validator on `Settings`. `Settings()` is instantiated at module-import time in `app/config.py`, which runs once in the uvicorn supervisor process before the workers are forked, so the failure is pre-fork and pre-bind: the supervisor exits non-zero with the validator's message and no workers are started. (Each worker also re-imports the module after fork, but the supervisor has already failed by then in the misconfigured case, so worker-side behavior is moot.)

---

## 4. Components

### 4.1 `app/config.py` — settings additions

Two new fields, default-preserving, plus a model validator. Defaults can also be set in `app/settings.json` for self-hosters who want to bake worker counts into the image.

```python
class Settings(BaseSettings):
    # ... existing fields ...
    workers: int = _defaults.get("workers", 1)
    rate_limit_storage_uri: str | None = _defaults.get("rate_limit_storage_uri", None)

    @model_validator(mode="after")
    def _check_workers_have_shared_storage(self) -> "Settings":
        if self.workers > 1 and not self.rate_limit_storage_uri:
            raise ValueError(
                "PC2NUTS_WORKERS > 1 requires PC2NUTS_RATE_LIMIT_STORAGE_URI to be set "
                "(e.g. 'redis://host:6379/0'). Without shared storage the per-IP rate limit "
                "would silently loosen by a factor of WORKERS."
            )
        return self
```

The exact field name and Pydantic version semantics are existing-codebase concerns to confirm during implementation; the design intent is "model-level validator that fails fast on the unsafe combination."

### 4.2 `app/limiter.py` — new module

Single responsibility: own the slowapi `Limiter` instance and the storage choice. Today this is inline in `app/main.py`; pulling it out keeps `main.py` slimmer and gives the fail-degraded wrapper a natural home.

Public surface:

```python
# app/limiter.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter: Limiter  # constructed at import time from settings
```

Internal logic:

- If `settings.rate_limit_storage_uri` is `None` → `Limiter(key_func=get_remote_address)` (current behavior, byte-for-byte).
- If set → construct a `_FailDegradedStorage` (see 4.3) and pass it to `Limiter` via the `storage_uri` parameter using a custom URI scheme, OR — more directly — construct the `Limiter` with the underlying URI and then monkey-patch the storage attribute. Implementation will pick whichever the slowapi/limits public API supports cleanly; design intent is "Limiter calls the wrapper, wrapper calls Redis with memory fallback."

`app/main.py` changes from `limiter = Limiter(...)` to `from app.limiter import limiter`. The `app.state.limiter = limiter` and `@limiter.limit(...)` decorators stay exactly as they are.

### 4.3 `_FailDegradedStorage` — fail-degraded wrapper

A custom `limits` storage class wrapping a primary storage (Redis) and a sibling `MemoryStorage`. Implements the same interface as `limits.storage.Storage` (or whichever subclass `slowapi`/`limits` requires for moving-window rate limiting — `MovingWindowSupport`). All methods (`incr`, `get`, `get_expiry`, `check`, `reset`, etc.) follow the same pattern:

```python
# Pseudo-code — exact method signatures match the limits.storage.Storage interface
# under the slowapi version pinned in requirements.lock; see §10 open question.
_PRIMARY_STORAGE_EXCEPTIONS = (
    redis.exceptions.RedisError,         # connection refused, timeout, auth failure, …
    OSError,                              # socket-level errors not wrapped by redis-py
)

def incr(self, key, expiry, elastic_expiry=False, amount=1):
    if self._is_unhealthy():
        return self._memory.incr(key, expiry, elastic_expiry, amount)
    try:
        return self._primary.incr(key, expiry, elastic_expiry, amount)
    except _PRIMARY_STORAGE_EXCEPTIONS:
        self._mark_unhealthy()
        return self._memory.incr(key, expiry, elastic_expiry, amount)
```

State:
- `_unhealthy_until: float | None` — monotonic timestamp; when set and not yet elapsed, skip the primary entirely.
- `_unhealthy_window_seconds: int = 30` — class-level constant. Hard-coded; not a config knob unless a concrete need appears.

Health-check / re-probe:
- `_is_unhealthy()` returns True if `_unhealthy_until` is set and not yet elapsed.
- After the window, next call probes the primary again; success → clear the flag, failure → extend the window.

Logging:
- Log at WARNING **once per outage window** (when transitioning healthy → unhealthy), with the exception class and message.
- Log at INFO when transitioning unhealthy → healthy after a successful re-probe.
- Do not log on every routed-to-memory call (that would be ~one log per request during an outage).

Concurrency:
- Each worker has its own `_FailDegradedStorage` instance and its own `_unhealthy_until` clock. That's intentional: workers do not share state, and per-worker outage tracking is correct (a worker that can't reach Redis is independently degraded). The `MemoryStorage` fallback is also per-worker, which is exactly the pre-this-issue behavior, so the cap loosens to N× *only during the outage window*.

### 4.4 `Dockerfile`

Single change: switch the `CMD` from exec form to shell form so `${PC2NUTS_WORKERS}` expands at container start.

```dockerfile
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${PC2NUTS_WORKERS:-1}"]
```

The default `:-1` keeps single-worker behavior when the env var is unset. The shell-form CMD does mean the container's PID 1 is `sh`, which is acceptable for this service (no signal-handling-sensitive workloads); uvicorn forwards SIGTERM correctly when invoked this way. If signal handling becomes a concern later, switching to `exec uvicorn …` inside the shell command preserves the env expansion while making uvicorn PID 1.

### 4.5 `requirements.lock`

Add a `redis` runtime dependency. Easiest path is `slowapi[redis]` or `limits[redis]` (whichever extra is exposed by the version slowapi pins); fallback is a direct `redis` pin matching the major-version range that `limits` expects (concrete numbers chosen at implementation time — see §10).

The dependency is loaded eagerly at import time by `app/limiter.py` only when `rate_limit_storage_uri` is set; single-host deployers who never set the env var still pay the install-size cost but no runtime cost. Acceptable trade-off.

### 4.6 `app/main.py`

- Remove the inline `limiter = Limiter(key_func=get_remote_address)` line.
- Add `from app.limiter import limiter` near the existing slowapi imports.
- Everything else (`app.state.limiter = limiter`, the `RateLimitExceeded` handler, the `@limiter.limit(...)` decorators on `/lookup` and `/lookup/{country}/{postal}`) stays exactly as it is.

---

## 5. Data flow

### Single-worker deploy (default, unchanged)

```
Request → uvicorn (1 worker) → FastAPI → @limiter.limit → MemoryStorage (in-process dict) → handler
```

Identical to today. Trusted-token requests skip the limiter via `exempt_when`.

### Multi-worker deploy (new)

```
Request → uvicorn (N workers, OS distributes) → FastAPI → @limiter.limit
       → _FailDegradedStorage
            healthy:    → Redis (shared across workers) → handler
            unhealthy:  → MemoryStorage (per-worker, cap effectively loosened to N× during outage window)
                       → handler
```

Trusted-token requests still skip the limiter entirely; the storage call is not made for them.

---

## 6. Error handling

The fail-degraded storage is the only new failure surface. Behavior matrix:

| Condition | Behavior | Cap during this state |
|---|---|---|
| Redis healthy | All counters in Redis, shared across workers | Strict `settings.rate_limit` per IP |
| Redis raises (connection error, timeout, etc.) | Wrapper catches, marks unhealthy for 30 s, routes to per-worker memory; logs WARNING once | `N × settings.rate_limit` per IP per worker |
| Redis recovers within 30 s | Next request after the window probes Redis; success clears the flag | Strict on next request |
| Redis still down after 30 s | Re-probe fails, window extends another 30 s; no new log | Continues N× per worker |

Storage exceptions caught: connection errors, timeouts, redis-specific errors. **Not caught:** programming errors (e.g. type errors from passing wrong arguments) — those bubble up so they're caught in tests.

The existing `_rate_limit_handler` for `RateLimitExceeded` is unchanged. Trusted-token requests still bypass entirely.

### Documented degraded mode

`README.md` adds a short note in the rate-limit section: "When `PC2NUTS_RATE_LIMIT_STORAGE_URI` is set and the configured backend is unreachable, the service falls back to per-process in-memory rate limiting for 30 s before re-probing. During this window, the effective per-IP cap is `PC2NUTS_WORKERS × rate_limit`."

---

## 7. Testing

### Unchanged (passes as-is)

- All existing rate-limit tests run against the default in-memory `Limiter` and continue to pass byte-for-byte. The `Limiter` construction path for `rate_limit_storage_uri == None` is exactly the current code.

### New tests

Unit:

- **`test_limiter_storage_selection`** — when `rate_limit_storage_uri` is `None`, `app.limiter.limiter._storage` is a plain `MemoryStorage` (or whatever the slowapi default exposes). When set, it's a `_FailDegradedStorage`.
- **`test_fail_degraded_routes_to_memory_on_raise`** — inject a primary storage stub that raises `ConnectionError` on `incr`; assert the wrapper returns the memory-storage value, the unhealthy flag is set, and a WARNING was logged once.
- **`test_fail_degraded_re_probes_after_window`** — same stub, raise once, sleep past the 30 s window (or use a monotonic-clock seam), assert next call hits the primary again.
- **`test_fail_degraded_logs_once_per_window`** — multiple consecutive raises within one window log only once.

Startup:

- **`test_workers_gt_1_without_storage_uri_fails_startup`** — instantiate `Settings(workers=2, rate_limit_storage_uri=None)` and assert `ValidationError`. Asserts the validator fires.
- **`test_workers_gt_1_with_storage_uri_succeeds`** — `Settings(workers=2, rate_limit_storage_uri="redis://localhost:6379/0")` constructs cleanly without contacting Redis (storage construction is lazy; only `Limiter` instantiation touches the network, and even then it's lazy until first request).

Integration (optional, if a dockerised Redis is acceptable in CI):

- **`test_multi_worker_with_real_redis`** — spin up an ephemeral Redis (via `pytest-docker` or similar), run two workers via `uvicorn --workers 2`, fire `>120` requests in a minute from a single client, assert the cap is observed across workers. Skip if no Redis available; gate behind a marker.

The design does not require the integration test to land in this PR. Unit tests for the wrapper plus the startup-validator test are sufficient evidence of correctness; the integration test is a nice-to-have follow-up if CI gains a Redis service container.

### Test seam for the unhealthy clock

`_FailDegradedStorage` reads the current time via an injectable callable (default `time.monotonic`) so tests don't have to sleep. Constructor takes `clock: Callable[[], float] = time.monotonic`.

---

## 8. Documentation

- **`README.md`** — add a "Multi-worker deployment" subsection under the existing Configuration section: documents `PC2NUTS_WORKERS`, `PC2NUTS_RATE_LIMIT_STORAGE_URI`, the fail-degraded behavior during Redis outages, and the startup-validation guard. Cross-links to issue #68 in the changelog entry.
- **`CHANGELOG.md`** — entry under Unreleased: "Added multi-worker deployment via `PC2NUTS_WORKERS` env var. Multi-worker mode requires `PC2NUTS_RATE_LIMIT_STORAGE_URI` (e.g. a Redis URL) so the per-IP rate limit stays strict across workers; transient backend unavailability is tolerated via per-worker in-memory fallback for 30 s windows."
- **`docs/performance.md`** — no changes in this PR. The re-baseline run (acceptance criterion in #68) is post-deploy operational work.

---

## 9. Acceptance criteria mapping (from issue #68)

| Issue criterion | Where addressed |
|---|---|
| `Dockerfile` updated to launch N workers (configurable via env var) | §4.4 |
| Memory usage measured against the container's allocated memory; document the headroom | Out of scope for this PR — operational follow-up. README documents the per-worker memory ballpark from the issue body. |
| Rate-limit behaviour with N workers documented in `README.md` | §8 — option (a) Redis chosen, behavior documented including degraded mode |
| Performance baseline re-run; `docs/performance.md` updated | Out of scope for this PR — separate operational task post-deploy |

The two "out of scope" items are operational/empirical work that can only happen against the deployed multi-worker container. They are tracked as a follow-up task in the issue rather than blocking the implementation PR.

---

## 10. Open questions deferred to implementation

- **Exact slowapi/limits API surface for plugging in a custom storage class.** slowapi's `Limiter` accepts `storage_uri` (string) and `storage_options` (dict); whether a pre-instantiated `Storage` instance can be passed directly varies by version. If not, the wrapper registers a custom URI scheme (`limits` library supports this via entry points or `register_storage`) and the env var uses that scheme. Implementation pins the chosen approach.
- **Redis client library version pin.** `redis` (redis-py) versus `redis>=5,<7` — driven by what `limits` requires. Confirmed at implementation time against the current `slowapi` pin.
- **Pydantic validator semantics.** `model_validator(mode="after")` on Pydantic v2 vs `root_validator` on v1 — the project is on Pydantic v2 (per `requirements.lock` and recent dependabot bumps), so v2 is the target. Verified during implementation.

These are mechanical questions that don't change the architecture; they're called out so the implementation plan addresses them rather than stumbling on them.
