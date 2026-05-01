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

Single responsibility: own the slowapi `Limiter` instance and the storage-mode choice. Today this is inline in `app/main.py`; pulling it out keeps `main.py` slimmer and isolates the (small) configuration logic.

Public surface:

```python
# app/limiter.py
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.config import settings

if settings.rate_limit_storage_uri:
    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri=settings.rate_limit_storage_uri,
        in_memory_fallback_enabled=True,
    )
else:
    limiter = Limiter(key_func=get_remote_address)
```

That's the entire module. Two branches; default branch is byte-for-byte identical to the current `app/main.py:45` line.

`app/main.py` changes from `limiter = Limiter(...)` to `from app.limiter import limiter`. The `app.state.limiter = limiter` and `@limiter.limit(...)` decorators stay exactly as they are.

### 4.3 Fail-degraded behavior — slowapi built-in

slowapi 0.1.9 already implements the fail-degraded design we want via the `in_memory_fallback_enabled` parameter. Behavior, taken directly from `slowapi/extension.py`:

- **Trigger:** when any storage call raises, slowapi sets an internal `_storage_dead = True` flag, logs once at WARNING (`"Rate limit storage unreachable - falling back to in-memory storage"`), and routes subsequent rate-limit checks to a separate per-process `MemoryStorage`-backed `MovingWindowRateLimiter`.
- **Recovery:** uses exponential backoff. The `__should_check_backend()` helper waits `2^N` seconds between consecutive re-probes (where `N` is the consecutive check count, capped at `MAX_BACKEND_CHECKS`). When a probe succeeds (`self._storage.check()`), `_storage_dead` is cleared and slowapi logs at INFO (`"Rate limit storage recovered"`).
- **Logging policy:** the `if not self._storage_dead` guard around the WARNING means it fires once per outage transition, exactly matching our spec intent.
- **Concurrency:** each worker has its own `_storage_dead` flag and its own fallback `MemoryStorage`. Per-worker outage tracking is correct (a worker that can't reach Redis is independently degraded), and the fallback is per-worker, so during an outage the effective cap loosens to `N × settings.rate_limit` per IP — same as our designed §3 behavior.

This means we write **zero custom storage code**. The plan only needs to wire `in_memory_fallback_enabled=True` into the `Limiter` construction and verify the wiring at the unit-test level.

The exponential-backoff recovery cadence (rather than a fixed 30 s window) is strictly better for the operator: gentler on a recovering Redis under load, faster recovery for short blips.

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
       → slowapi Limiter (in_memory_fallback_enabled=True)
            _storage_dead=False:  → Redis (shared across workers) → handler
            _storage_dead=True:   → per-worker MemoryStorage fallback → handler
                                  (cap effectively loosened to N× during outage window;
                                   slowapi re-probes Redis with exponential backoff)
```

Trusted-token requests still skip the limiter entirely; the storage call is not made for them.

---

## 6. Error handling

The fail-degraded path lives entirely inside slowapi (`in_memory_fallback_enabled=True`). Behavior matrix, derived from `slowapi/extension.py`:

| Condition | slowapi behavior | Cap during this state |
|---|---|---|
| Redis healthy | All counters in Redis, shared across workers | Strict `settings.rate_limit` per IP |
| Redis raises (any exception) | `_storage_dead = True`; logs WARNING once; routes subsequent checks to per-worker `MemoryStorage` | `N × settings.rate_limit` per IP per worker |
| Redis recovery probe (every `2^N` seconds, exponential backoff) succeeds | `_storage_dead = False`; logs INFO once; back to Redis | Strict on next request |
| Redis still down at next probe | Probe fails silently; backoff continues; no new log | Continues N× per worker |

slowapi catches `Exception` broadly around its storage interactions, so connection errors, timeouts, and redis-py-specific errors are all handled. Programming errors in our own code paths still bubble up.

The existing `_rate_limit_handler` for `RateLimitExceeded` is unchanged. Trusted-token requests still bypass entirely (the `exempt_when` check fires before the storage call).

### Documented degraded mode

`README.md` adds a short note in the rate-limit section: "When `PC2NUTS_RATE_LIMIT_STORAGE_URI` is set and the configured backend is unreachable, the service falls back to per-process in-memory rate limiting (slowapi `in_memory_fallback_enabled`). During the outage window, the effective per-IP cap is `PC2NUTS_WORKERS × rate_limit`. slowapi re-probes the primary storage with exponential backoff and resumes shared rate limiting on recovery."

---

## 7. Testing

### Unchanged (passes as-is)

- All existing rate-limit tests run against the default in-memory `Limiter` and continue to pass byte-for-byte. The `Limiter` construction path for `rate_limit_storage_uri == None` is exactly the current code.

### New tests

Unit:

- **`test_limiter_default_uses_memory_storage`** — when `rate_limit_storage_uri` is unset, `app.limiter.limiter._storage_uri` is the slowapi default (`None` or `"memory://"`) and `_in_memory_fallback_enabled` is `False`. Confirms the no-config path is byte-for-byte the current behavior.
- **`test_limiter_with_redis_uri_enables_fallback`** — when `rate_limit_storage_uri="redis://localhost:6379/0"` is configured (via `monkeypatch.setenv`), `app.limiter.limiter._storage_uri` is the configured URI and `_in_memory_fallback_enabled` is `True`. Does not contact Redis (Limiter constructor builds the storage object lazily-enough that no connection is opened until first request, but to be safe the test asserts on the constructor-level attributes only).

Startup validator:

- **`test_workers_gt_1_without_storage_uri_fails_startup`** — instantiate `Settings(workers=2, rate_limit_storage_uri=None)` and assert `ValidationError`. Asserts the validator fires.
- **`test_workers_gt_1_with_storage_uri_succeeds`** — `Settings(workers=2, rate_limit_storage_uri="redis://localhost:6379/0")` constructs cleanly without contacting Redis.
- **`test_workers_eq_1_without_storage_uri_succeeds`** — defaults still validate (regression guard).

Integration (out of scope for the implementation PR — left as a separate task if CI gains a Redis service container):

- A future integration test could spin up an ephemeral Redis, run two workers via `uvicorn --workers 2`, fire `>120` requests in a minute from a single client, assert the cap is observed across workers. The slowapi fail-degraded path is library code (already tested upstream), so the implementation PR does not need to re-test it; we only verify that our wiring activates the right slowapi configuration.

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

- **Redis client library version pin.** `redis` (redis-py) — exact version range driven by what `limits 5.8.0` requires. The `limits[redis]` extra is the cleanest path; falls back to a direct pin if needed. Confirmed at implementation time.
- **Pydantic validator semantics.** Confirmed: project is on Pydantic v2 (`pydantic==2.12.5`, `pydantic-settings==2.13.0` per `requirements.lock`), so `@model_validator(mode="after")` is the target.

These are mechanical questions that don't change the architecture; they're called out so the implementation plan addresses them rather than stumbling on them.
