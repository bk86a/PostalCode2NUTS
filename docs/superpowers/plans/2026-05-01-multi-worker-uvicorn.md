# Multi-worker uvicorn Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** allow the service to run with N uvicorn workers behind a Redis-backed shared rate-limit backend, while leaving single-worker / in-memory deploys byte-for-byte unchanged.

**Architecture:** two new opt-in env vars (`PC2NUTS_WORKERS`, `PC2NUTS_RATE_LIMIT_STORAGE_URI`) drive the change. The slowapi `Limiter` is moved to a small dedicated module that picks between the current default construction and a Redis-configured construction with `in_memory_fallback_enabled=True`. A Pydantic model validator on `Settings` hard-fails startup when `WORKERS > 1` without a storage URI to prevent silent cap loosening. The Dockerfile `CMD` becomes shell-form so `${PC2NUTS_WORKERS}` expands at container start.

**Tech Stack:** Python 3.14, FastAPI 0.129, uvicorn 0.40, slowapi 0.1.9, limits 5.8.0, pydantic 2.12.5, pydantic-settings 2.13.0, redis-py (added via `limits[redis]` extra).

**Spec:** `docs/superpowers/specs/2026-05-01-multi-worker-uvicorn-design.md`

**Issue:** [#68](https://github.com/bk86a/PostalCode2NUTS/issues/68)

---

## File map

| File | Action | Responsibility |
|---|---|---|
| `app/config.py` | Modify | Add `workers` and `rate_limit_storage_uri` fields + cross-field validator |
| `app/limiter.py` | Create | Single-purpose module exposing the configured `Limiter` |
| `app/main.py` | Modify | Replace inline `limiter = Limiter(...)` (line 45) with import from `app.limiter` |
| `app/settings.json` | Modify | Add default for `workers` (`1`) and explicit null for `rate_limit_storage_uri` |
| `Dockerfile` | Modify | Switch `CMD` from exec-form to shell-form so `${PC2NUTS_WORKERS}` expands |
| `requirements.lock` | Modify | Add `redis` runtime dep (via `limits[redis]` extra) |
| `tests/test_limiter.py` | Create | Unit tests for `Limiter` storage-mode selection |
| `tests/test_config.py` | Modify or create | Unit tests for the new `Settings` validator |
| `README.md` | Modify | Document `PC2NUTS_WORKERS`, `PC2NUTS_RATE_LIMIT_STORAGE_URI`, degraded-mode behaviour |
| `CHANGELOG.md` | Modify | Unreleased section entry |

---

## Pre-flight

Before starting, verify the worktree is on the `feat/multi-worker-uvicorn` branch and the spec is committed:

```bash
git rev-parse --abbrev-ref HEAD
# Expected: feat/multi-worker-uvicorn

git log --oneline main..HEAD
# Expected: at least one commit referencing the spec
```

If the branch is wrong, stop and switch (`git switch feat/multi-worker-uvicorn`).

Verify the test suite is green at HEAD before touching anything:

```bash
pytest -x -q
```

If any tests fail at HEAD, stop and report — don't start making changes against a broken baseline.

---

## Task 1: Add `Settings` fields and validator

**Files:**
- Modify: `app/config.py`
- Modify: `app/settings.json`
- Modify or create: `tests/test_config.py`

This task introduces the two new config fields and the cross-field validator that prevents the dangerous combination (`workers > 1` with no storage URI). Tests come first.

- [ ] **Step 1: Confirm whether `tests/test_config.py` exists**

```bash
ls tests/test_config.py 2>&1
```

If the file does not exist, create it in Step 2 with the imports in place. If it exists, append to it.

- [ ] **Step 2: Write the failing tests**

If creating `tests/test_config.py`, full contents:

```python
"""Tests for app.config.Settings."""

import pytest
from pydantic import ValidationError

from app.config import Settings


class TestWorkersValidator:
    def test_workers_eq_1_without_storage_uri_succeeds(self):
        """Default config must keep validating — single-worker, no storage URI."""
        s = Settings(workers=1, rate_limit_storage_uri=None)
        assert s.workers == 1
        assert s.rate_limit_storage_uri is None

    def test_workers_gt_1_with_storage_uri_succeeds(self):
        """Multi-worker is permitted when a storage URI is configured."""
        s = Settings(workers=4, rate_limit_storage_uri="redis://localhost:6379/0")
        assert s.workers == 4
        assert s.rate_limit_storage_uri == "redis://localhost:6379/0"

    def test_workers_gt_1_without_storage_uri_fails_startup(self):
        """The unsafe combination must raise — silent cap loosening is the
        failure mode this validator exists to prevent."""
        with pytest.raises(ValidationError) as excinfo:
            Settings(workers=2, rate_limit_storage_uri=None)
        msg = str(excinfo.value)
        assert "PC2NUTS_WORKERS" in msg
        assert "PC2NUTS_RATE_LIMIT_STORAGE_URI" in msg

    def test_workers_gt_1_with_empty_storage_uri_fails_startup(self):
        """Empty string should be treated the same as None — both mean unset."""
        with pytest.raises(ValidationError):
            Settings(workers=2, rate_limit_storage_uri="")
```

If the file already exists, append the `TestWorkersValidator` class.

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_config.py -v
```

Expected: all four tests FAIL because `workers` and `rate_limit_storage_uri` aren't fields on `Settings` yet (most likely an `AttributeError` or unexpected-kwarg error from Pydantic).

- [ ] **Step 4: Add the fields and validator to `app/config.py`**

In `app/config.py`, change the imports at the top:

```python
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings
```

Inside the `Settings` class body, add the two new fields next to the existing `rate_limit` and `rate_limit_headers` fields (around line 25-26). The `_defaults.get(...)` pattern matches the existing fields:

```python
    workers: int = Field(default=_defaults.get("workers", 1), ge=1)
    rate_limit_storage_uri: str | None = _defaults.get("rate_limit_storage_uri", None)
```

Then add the validator method inside the class (place it after the existing `model_config = {...}` line and before the `@property` block):

```python
    @model_validator(mode="after")
    def _check_workers_have_shared_storage(self) -> "Settings":
        if self.workers > 1 and not self.rate_limit_storage_uri:
            raise ValueError(
                "PC2NUTS_WORKERS > 1 requires PC2NUTS_RATE_LIMIT_STORAGE_URI to be set "
                "(e.g. 'redis://host:6379/0'). Without shared storage the per-IP rate "
                "limit would silently loosen by a factor of WORKERS."
            )
        return self
```

- [ ] **Step 5: Add defaults to `app/settings.json`**

Edit `app/settings.json`. Inside the JSON object, add two keys after `"rate_limit_headers"`:

```json
  "rate_limit": "120/minute",
  "rate_limit_headers": true,
  "workers": 1,
  "rate_limit_storage_uri": null,
  "cache_max_age": 3600
```

The trailing comma after `"rate_limit_headers": true,` already exists. The trailing comma after `"rate_limit_storage_uri": null,` is new — verify it parses by reading the file back and asserting validity:

```bash
python3 -c "import json; json.load(open('app/settings.json'))" && echo "valid JSON"
```

Expected: `valid JSON`

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: all four tests PASS.

- [ ] **Step 7: Run the full test suite to verify no regressions**

```bash
pytest -x -q
```

Expected: green. Note: `Settings()` is instantiated at module import time in `app/config.py:88`. The default config (`workers=1`, `rate_limit_storage_uri=None`) must continue to validate, which Step 6 confirmed.

- [ ] **Step 8: Commit**

```bash
git add app/config.py app/settings.json tests/test_config.py
git commit -m "feat(config): add workers and rate_limit_storage_uri settings (#68)

New Pydantic model validator hard-fails startup if PC2NUTS_WORKERS > 1
without PC2NUTS_RATE_LIMIT_STORAGE_URI configured, so the per-IP rate
limit can never silently loosen under multi-worker.

Defaults preserve current behaviour: workers=1, storage URI unset."
```

---

## Task 2: Add `redis` runtime dependency

**Files:**
- Modify: `requirements.lock`

The `limits[redis]` extra pulls in the `redis-py` client at the version `limits 5.8.0` expects. We add it before the limiter tests because constructing a `Limiter(storage_uri="redis://…")` eagerly imports the `redis` module — Task 3 below would fail without it. We confirmed with `limits/storage/redis.py:96` that `RedisStorage.__init__` resolves `self.dependency = self.dependencies["redis"].module` at construction time; if the module isn't importable, slowapi raises `ConfigurationError` immediately. Construction does NOT open a TCP connection (`register_script` is local-only), so the test does not require a running Redis.

- [ ] **Step 1: Inspect the current `requirements.lock` format**

```bash
head -5 requirements.lock
grep -E "^(slowapi|limits|pydantic)" requirements.lock
```

Confirm the file is a flat list of `package==version` pins (one per line). The relevant context lines are `slowapi==0.1.9` and `limits==5.8.0`.

- [ ] **Step 2: Determine the redis-py version `limits[redis]` requires**

```bash
python3 -c "
import importlib.metadata as md
reqs = md.requires('limits') or []
print('\n'.join(r for r in reqs if 'redis' in r.lower()))
"
pip index versions redis 2>&1 | head -5
```

Expected: a line like `redis (>=3,<6) ; extra == 'redis'` or similar. Note the version constraint and pick the highest currently-released version inside that constraint.

- [ ] **Step 3: Verify the chosen version installs cleanly**

```bash
pip install --dry-run "redis==<chosen-version>" 2>&1 | tail -5
```

Expected: no version-conflict errors against the rest of `requirements.lock`.

- [ ] **Step 4: Add the pin to `requirements.lock`**

Edit `requirements.lock`. Insert the pin in alphabetical order (the file is already sorted; find where `r` belongs — typically between `pyyaml` and `requests` or similar). Add the single line:

```
redis==<chosen-version>
```

- [ ] **Step 5: Verify the requirements file as a whole still resolves**

```bash
pip install --dry-run -r requirements.lock 2>&1 | tail -10
```

Expected: no version-conflict errors.

- [ ] **Step 6: Install the dep so subsequent tasks' tests can run**

```bash
pip install -q "redis==<chosen-version>"
python3 -c "import redis; print('redis', redis.__version__)"
```

Expected: prints the installed redis version cleanly. (If the environment is PEP 668 / externally-managed and pip refuses, install into the project venv or use `--break-system-packages` per the local environment's policy. CI installs via `pip install -r requirements.lock` so the published artefact and CI both behave correctly.)

- [ ] **Step 7: Run the existing test suite to verify no regressions**

```bash
pytest -x -q
```

Expected: green. Adding the dep shouldn't change behaviour because no test imports `redis` directly yet.

- [ ] **Step 8: Commit**

```bash
git add requirements.lock
git commit -m "deps: add redis runtime dependency for multi-worker rate limiting (#68)

Pulls in redis-py at the version limits 5.8.0 expects, used only when
PC2NUTS_RATE_LIMIT_STORAGE_URI is set. Single-host deployers who never
configure shared storage pay the install-size cost but no runtime cost
(redis is imported eagerly by limits.storage.RedisStorage at Limiter
construction, but only when the storage URI is configured)."
```

---

## Task 3: Extract `Limiter` into `app/limiter.py`

**Files:**
- Create: `app/limiter.py`
- Modify: `app/main.py`
- Create: `tests/test_limiter.py`

This task pulls the inline `Limiter(key_func=get_remote_address)` line at `app/main.py:45` into a dedicated module that selects between the default and Redis-configured construction. The default branch is byte-for-byte the current code.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_limiter.py`:

```python
"""Tests for app.limiter — verify the Limiter is wired with the right storage
mode based on settings.rate_limit_storage_uri."""

import importlib

import pytest


def _reload_limiter():
    """Reload app.config and app.limiter so the module-level Limiter picks up
    the current env. Returns the freshly-imported app.limiter module."""
    import app.config
    import app.limiter
    importlib.reload(app.config)
    importlib.reload(app.limiter)
    return app.limiter


class TestLimiterStorageSelection:
    def test_limiter_default_uses_memory_storage(self, monkeypatch):
        """When no storage URI is set, the Limiter is constructed with the
        slowapi default (in-process memory) and fallback is disabled. This is
        the byte-for-byte current behaviour."""
        monkeypatch.delenv("PC2NUTS_RATE_LIMIT_STORAGE_URI", raising=False)
        monkeypatch.delenv("PC2NUTS_WORKERS", raising=False)

        mod = _reload_limiter()

        # slowapi stores the configured URI on _storage_uri; default is None.
        assert mod.limiter._storage_uri is None
        # in_memory_fallback_enabled is only meaningful when a URI is set.
        assert mod.limiter._in_memory_fallback_enabled is False

    def test_limiter_with_redis_uri_enables_fallback(self, monkeypatch):
        """When a storage URI is set, the Limiter is constructed with that URI
        AND in_memory_fallback_enabled=True. No network call should happen at
        construction time — slowapi builds the storage object lazily."""
        monkeypatch.setenv("PC2NUTS_RATE_LIMIT_STORAGE_URI", "redis://localhost:6379/0")
        monkeypatch.setenv("PC2NUTS_WORKERS", "2")

        mod = _reload_limiter()

        assert mod.limiter._storage_uri == "redis://localhost:6379/0"
        assert mod.limiter._in_memory_fallback_enabled is True

    @pytest.fixture(autouse=True)
    def _restore_default_after_each_test(self, monkeypatch):
        """After each test, force a reload back to defaults so other tests
        in the suite see the unmodified module."""
        yield
        monkeypatch.delenv("PC2NUTS_RATE_LIMIT_STORAGE_URI", raising=False)
        monkeypatch.delenv("PC2NUTS_WORKERS", raising=False)
        _reload_limiter()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_limiter.py -v
```

Expected: all tests FAIL with `ModuleNotFoundError: No module named 'app.limiter'`.

- [ ] **Step 3: Create `app/limiter.py`**

Full contents:

```python
"""Module-level slowapi Limiter, wired according to settings.

When PC2NUTS_RATE_LIMIT_STORAGE_URI is unset, the Limiter falls back to
slowapi's in-process MemoryStorage default — byte-for-byte the same as
the pre-#68 inline construction.

When the URI is set (e.g. 'redis://host:6379/0'), the Limiter routes
counters through the configured backend, with in_memory_fallback_enabled
giving us per-process MemoryStorage during transient backend outages.
slowapi handles the fail-degraded behaviour internally with exponential-
backoff re-probing — see app/main.py:_rate_limit_handler for the 429
response, and the spec at docs/superpowers/specs/2026-05-01-multi-worker-uvicorn-design.md
for the design rationale.
"""

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

- [ ] **Step 4: Update `app/main.py` to import the limiter**

In `app/main.py`, locate the existing slowapi imports and the inline Limiter construction:

Current state (lines 16-18 and line 45):
```python
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
...
limiter = Limiter(key_func=get_remote_address)
```

Change to:

```python
from slowapi.errors import RateLimitExceeded
...
from app.limiter import limiter
```

Concretely:
- Remove `from slowapi import Limiter` (line 16). The `Limiter` symbol is no longer used in `main.py`.
- Remove `from slowapi.util import get_remote_address` (line 18). Same reason.
- Keep `from slowapi.errors import RateLimitExceeded` (line 17) — still used by `_rate_limit_handler`.
- Replace the standalone `limiter = Limiter(key_func=get_remote_address)` line (line 45) with:
  ```python
  from app.limiter import limiter
  ```
  Place this with the other `from app...` imports in the import block (around line 22-23, alongside `from app.auth import ...` and `from app.config import settings`).

After the changes, the slowapi-related imports in `app/main.py` should be just:

```python
from slowapi.errors import RateLimitExceeded
```

And `limiter` should be sourced via:

```python
from app.limiter import limiter
```

The decorators `@limiter.limit(...)` and the `app.state.limiter = limiter` line stay exactly as they are.

- [ ] **Step 5: Run the new tests to verify they pass**

```bash
pytest tests/test_limiter.py -v
```

Expected: both tests PASS.

- [ ] **Step 6: Run the full test suite to verify no regressions**

```bash
pytest -x -q
```

Expected: green. Particular attention to `tests/test_api.py::TestRateLimitTokens::test_valid_token_bypasses_rate_limit` (around test_api.py:226) — this exercises the `@limiter.limit(...)` decorator path and must still pass exactly as before.

If any test fails, the most likely cause is a circular import (`app.limiter` imports `app.config.settings`, which is fine; `app.main` imports `app.limiter`, which is also fine; but `app.config` must NOT import `app.limiter`). Verify by reading the import block of `app/config.py` — it should only import from `pydantic`, `pydantic_settings`, and stdlib.

- [ ] **Step 7: Commit**

```bash
git add app/limiter.py app/main.py tests/test_limiter.py
git commit -m "feat(limiter): extract slowapi Limiter into dedicated module (#68)

When PC2NUTS_RATE_LIMIT_STORAGE_URI is set, construct the Limiter with
that storage URI and in_memory_fallback_enabled=True so transient
backend outages fall back to per-process MemoryStorage. When unset,
construction is byte-for-byte the previous inline call.

slowapi's built-in fallback handles outage detection, once-per-outage
WARNING logging, and exponential-backoff recovery probes."
```

---

## Task 4: Wire `PC2NUTS_WORKERS` into the Dockerfile

**Files:**
- Modify: `Dockerfile`

Single-line change to the `CMD`. The shell-form `CMD ["sh", "-c", "..."]` lets `${PC2NUTS_WORKERS:-1}` expand at container start using the env-var-with-default shell idiom. Default `:-1` preserves single-worker behaviour when the env var is unset.

- [ ] **Step 1: Read the current Dockerfile to confirm the CMD line**

```bash
grep -n "CMD" Dockerfile
```

Expected (current): `24:CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`

- [ ] **Step 2: Replace the `CMD` line**

Edit `Dockerfile`, line 24. Replace:

```
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

with:

```
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${PC2NUTS_WORKERS:-1}"]
```

The `exec` keeps `uvicorn` as the foreground process (replaces `sh` instead of leaving it as parent), so SIGTERM from the container runtime reaches uvicorn directly. Critical for graceful shutdown.

- [ ] **Step 3: Sanity-check the Dockerfile builds**

```bash
docker build -t pc2nuts:test-multi-worker . 2>&1 | tail -10
```

Expected: `Successfully tagged pc2nuts:test-multi-worker` or equivalent (depending on Docker version). If the build fails for unrelated reasons (e.g. base image version drift), record the error and stop — do NOT modify other parts of the Dockerfile to make the build pass.

If `docker` is not available in the agent environment, skip this step and rely on CI (the existing `docker` job in `.github/workflows/ci.yml` will exercise the build on PR).

- [ ] **Step 4: Smoke-test the container with default (single-worker)**

If Step 3 succeeded:

```bash
docker run --rm -d --name pc2nuts-test -p 8000:8000 pc2nuts:test-multi-worker
sleep 8
curl -fsS http://localhost:8000/health | head -c 300
docker stop pc2nuts-test
```

Expected: `/health` returns 200 with the usual JSON body. The `--workers 1` is implicit (no env var → default).

If the curl returns non-200 or hangs, capture logs:

```bash
docker logs pc2nuts-test 2>&1 | tail -40
```

The most likely failure modes are:
- `${PC2NUTS_WORKERS:-1}` not expanding (would be visible in logs as `--workers ${PC2NUTS_WORKERS:-1}` literal). If so, double-check that the `CMD` is shell-form (`sh -c`).
- Existing data-load failures unrelated to this change (TERCET download). Not our problem here.

- [ ] **Step 5: Smoke-test the container with `PC2NUTS_WORKERS=2` and no storage URI (must fail)**

```bash
docker run --rm -d --name pc2nuts-test-bad -e PC2NUTS_WORKERS=2 -p 8001:8000 pc2nuts:test-multi-worker
sleep 5
docker logs pc2nuts-test-bad 2>&1 | tail -20
docker stop pc2nuts-test-bad 2>/dev/null || true
docker rm pc2nuts-test-bad 2>/dev/null || true
```

Expected: container exits early; logs contain the validator's error message naming `PC2NUTS_WORKERS` and `PC2NUTS_RATE_LIMIT_STORAGE_URI`. This confirms the startup guard fires inside the container, not just in unit tests.

If Docker is not available, skip — CI will exercise the build.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile
git commit -m "feat(docker): support PC2NUTS_WORKERS env var (#68)

Switches CMD from exec-form to shell-form with 'exec uvicorn …' so
\${PC2NUTS_WORKERS:-1} expands at container start while uvicorn remains
the foreground PID-1 process for proper SIGTERM handling.

Default of 1 preserves current single-worker behaviour. Multi-worker
mode also requires PC2NUTS_RATE_LIMIT_STORAGE_URI; the Settings
validator (added in feat(config)) refuses to start otherwise."
```

---

## Task 5: Document the new env vars in README

**Files:**
- Modify: `README.md`

Add a "Multi-worker deployment" subsection near the existing rate-limit / configuration documentation. Reference the slowapi fail-degraded behaviour so operators know what to expect during a Redis outage.

- [ ] **Step 1: Locate the right insertion point**

```bash
grep -n "rate_limit\|rate limit\|RATE_LIMIT" README.md | head -20
grep -n "## Configuration\|### Configuration\|## Environment" README.md | head -10
```

Look for the existing rate-limit / env-var documentation block. The new content goes immediately after that block.

- [ ] **Step 2: Read the surrounding context**

Read the 30 lines around the rate-limit doc to match the existing style (table format, env-var naming convention, voice). Note whether the README uses backticks for env vars, what tone is used (terse vs prose), etc.

- [ ] **Step 3: Insert the new section**

Add the following block immediately after the existing rate-limit documentation, matching the README's existing formatting. If the README uses a table for env vars, add two rows; if it uses a definitions list, add two items. The content (adapt to the existing style):

```markdown
### Multi-worker deployment

By default the service runs a single uvicorn worker process. Throughput is
CPU-bound at ~30 RPS per worker (see [docs/performance.md](docs/performance.md)).
For higher RPS, set `PC2NUTS_WORKERS` to the number of worker processes you
want — the rough rule of thumb is one worker per CPU core, capped by the
available memory (~150-200 MB resident set per worker).

| Env var | Default | Effect |
|---|---|---|
| `PC2NUTS_WORKERS` | `1` | Number of uvicorn worker processes. |
| `PC2NUTS_RATE_LIMIT_STORAGE_URI` | (unset) | When unset, slowapi uses per-process in-memory storage (default). When set (e.g. `redis://host:6379/0`), counters are shared across workers so the published `rate_limit` cap stays accurate. |

When `PC2NUTS_WORKERS > 1`, `PC2NUTS_RATE_LIMIT_STORAGE_URI` MUST be set
to a reachable shared backend; the service refuses to start otherwise.
This guards against the per-IP rate limit silently loosening to
`PC2NUTS_WORKERS × rate_limit` per IP under multi-worker.

**Degraded mode.** If the configured storage backend becomes unreachable
at runtime, slowapi (`in_memory_fallback_enabled=True`) falls back to
per-process in-memory rate limiting and re-probes the primary storage
with exponential backoff. During the outage window the effective per-IP
cap is `PC2NUTS_WORKERS × rate_limit`. Recovery is automatic; one
WARNING log line is emitted at the start of the outage and one INFO line
on recovery.
```

- [ ] **Step 4: Verify the README still renders sensibly**

```bash
# Syntax-check by piping through a markdown parser if one is available
python3 -c "
with open('README.md') as f:
    content = f.read()
# Crude check: equal counts of backticks (no unmatched code spans)
import re
inline = len(re.findall(r'(?<!`)\`(?!\`)', content))
assert inline % 2 == 0, f'Odd number of inline backticks: {inline}'
print('Backticks balanced')
"
```

Expected: `Backticks balanced`. If unbalanced, find the unmatched backtick in the new block and fix it.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(README): document PC2NUTS_WORKERS and rate-limit storage URI (#68)

New 'Multi-worker deployment' subsection covers both env vars, the
startup-validation guard for the unsafe combination, and the slowapi
fail-degraded behaviour during a backend outage."
```

---

## Task 6: Add CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Read the top of CHANGELOG.md to find the Unreleased section**

```bash
head -30 CHANGELOG.md
```

If the file has a `## [Unreleased]` heading at the top, append under it. If not, add one at the top, above the most recent released-version heading.

- [ ] **Step 2: Add the entry**

Add under the Unreleased section, in the existing style (most likely a `### Added` or `### Changed` subgroup):

```markdown
### Added
- **Multi-worker deployment** (#68): set `PC2NUTS_WORKERS` to launch N uvicorn worker processes. Multi-worker mode requires `PC2NUTS_RATE_LIMIT_STORAGE_URI` (e.g. a Redis URL) so the published per-IP rate limit stays accurate across workers; the service refuses to start otherwise. Transient backend unavailability is tolerated via slowapi's `in_memory_fallback_enabled` — falls back to per-process in-memory rate limiting and re-probes with exponential backoff, with one WARNING log per outage and one INFO log on recovery.
```

If the existing Unreleased entries use a different bullet style (terse one-liners, no bold lead-in), match that style instead.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): multi-worker deployment entry (#68)"
```

---

## Task 7: Final verification

**Files:** none modified.

- [ ] **Step 1: Run the full test suite**

```bash
pytest -x -q
```

Expected: green.

- [ ] **Step 2: Run the linter**

```bash
ruff check . && ruff format --check .
```

Expected: clean. If the linter flags `app/limiter.py` for missing import or similar, fix the actual issue (don't suppress the lint).

- [ ] **Step 3: Verify the import-check job logic locally**

```bash
python3 -c "from app.main import app; print('app imports cleanly:', app.title)"
```

Expected: `app imports cleanly: PostalCode2NUTS`. Catches circular-import surprises that pytest might mask.

- [ ] **Step 4: Verify the security check still passes**

```bash
bandit -q -r app/ 2>&1 | tail -10
```

Expected: no new findings on `app/limiter.py`. If bandit complains about something legitimately security-sensitive, fix it; if it's a false positive on the slowapi/redis URI handling, document the suppression with a comment naming the rule.

- [ ] **Step 5: Eyeball the diff against `main`**

```bash
git diff main...HEAD --stat
git diff main...HEAD docs/superpowers/specs/ docs/superpowers/plans/
```

Expected: the file list matches the file map at the top of this plan. The spec and plan diffs should be readable and consistent. The implementation files (`app/config.py`, `app/limiter.py`, `app/main.py`, `Dockerfile`, `requirements.lock`, `app/settings.json`, two test files, README, CHANGELOG) should all be present.

- [ ] **Step 6: Push the branch and open a PR**

```bash
git push -u origin feat/multi-worker-uvicorn
```

Then open the PR with `gh pr create`, mapping each acceptance criterion from issue #68 to the relevant component. Body template:

```markdown
## Summary

Implements [#68](https://github.com/bk86a/PostalCode2NUTS/issues/68): multi-worker uvicorn behind a shared rate-limit backend.

- New env vars: `PC2NUTS_WORKERS` (default `1`), `PC2NUTS_RATE_LIMIT_STORAGE_URI` (default unset).
- Startup hard-fails if `WORKERS > 1` without a storage URI configured.
- slowapi `in_memory_fallback_enabled=True` handles transient backend outages with per-process MemoryStorage fallback and exponential-backoff re-probing.
- Default behaviour (single worker, in-memory) is byte-for-byte unchanged.

Spec: `docs/superpowers/specs/2026-05-01-multi-worker-uvicorn-design.md`
Plan: `docs/superpowers/plans/2026-05-01-multi-worker-uvicorn.md`

## Acceptance criteria from #68

- [x] `Dockerfile` updated to launch N workers via `PC2NUTS_WORKERS`
- [x] Rate-limit behaviour with N workers documented in `README.md`
- [ ] Memory usage measured against the container's allocated memory — operational follow-up post-deploy
- [ ] Performance baseline re-run; `docs/performance.md` updated — operational follow-up post-deploy

## Test plan

- [ ] Existing rate-limit tests still pass (single-worker default branch is byte-for-byte unchanged)
- [ ] New `tests/test_config.py::TestWorkersValidator` proves the validator fires
- [ ] New `tests/test_limiter.py::TestLimiterStorageSelection` proves the storage URI is honoured
- [ ] Manual: deploy with `PC2NUTS_WORKERS=2` + a real Redis, confirm the cap holds across workers (operational)
- [ ] Manual: kill Redis with traffic flowing, confirm WARNING logs once and INFO logs on recovery (operational)
```

---

## Self-review

Before marking the plan complete, the implementer (or dispatching agent) should run through this checklist:

- **Spec coverage.** Every section of the spec maps to a task:
  - §3 Configuration surface → Task 1
  - §4.1 Settings additions → Task 1
  - §4.2 `app/limiter.py` → Task 3
  - §4.3 slowapi fail-degraded behaviour → Task 3 (the `in_memory_fallback_enabled=True` flag)
  - §4.4 Dockerfile → Task 4
  - §4.5 requirements.lock → Task 2
  - §4.6 `app/main.py` → Task 3
  - §6 Error handling — covered by slowapi internals; test scope per §7
  - §7 Testing → Tasks 1 and 3 each include their own tests
  - §8 Documentation → Tasks 5 and 6
  - §9 Acceptance-criteria mapping → Task 7 PR body
- **Placeholder scan.** Every "implement" step contains the actual code. The only `<chosen-version>` placeholders are in Task 2, where the version genuinely depends on the live `pip` resolution; the steps tell the implementer how to derive the concrete value.
- **Type/name consistency.** `Settings.workers` (int, default 1), `Settings.rate_limit_storage_uri` (str|None, default None), `app.limiter.limiter` (slowapi.Limiter), `PC2NUTS_WORKERS` and `PC2NUTS_RATE_LIMIT_STORAGE_URI` env-var names — all match across tasks.
