# Estimates periodic refresh — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement issue [#44](https://github.com/bk86a/PostalCode2NUTS/issues/44) — let a running pod pick up changes to `tercet_missing_codes.csv` from upstream `main` without a redeploy.

**Architecture:** New module `app/estimates_refresh.py` with module-scoped state, an async fetch using `httpx.AsyncClient`, conditional GET headers (ETag / Last-Modified), SHA-256 dedupe, a 50% relative-row sanity guard, and full-replace swap into the existing `_estimates` dict under the existing `_data_lock`. Wired via FastAPI lifespan (one bootstrap call + a `refresh_estimates_loop()` task). New `POST /admin/refresh-estimates` endpoint reuses the trusted-token Bearer mechanism. Defaults preserve current behaviour byte-for-byte (feature is opt-in via `PC2NUTS_ESTIMATES_REFRESH_URL`).

**Tech Stack:** Python 3.14, FastAPI, httpx (async), pytest, slowapi (rate-limit decorator on the new endpoint), the project's existing `_data_lock` (`threading.Lock` from `app/data_loader.py`).

**Spec:** [`docs/superpowers/specs/2026-05-01-estimates-periodic-refresh-design.md`](../specs/2026-05-01-estimates-periodic-refresh-design.md) (commit `e667cac`).

---

## File structure

| Action | Path | Responsibility |
|---|---|---|
| Modify | `app/config.py` | Two new Settings fields (`estimates_refresh_url`, `estimates_refresh_interval_seconds`). |
| Modify | `app/data_loader.py` | Extract a stream-friendly helper `parse_estimates_from_text()` from the existing `_load_estimates_from_csv()`. |
| Create | `app/estimates_refresh.py` | All refresh logic — module state, fetch, sanity guard, orchestration, asyncio loop, `/health` getter. |
| Modify | `app/models.py` | Add `estimates_refresh_stale: bool \| None = None` to `HealthResponse`. |
| Modify | `app/main.py` | Lifespan wiring (bootstrap + task), `/admin/refresh-estimates` endpoint, populate the new `/health` field. |
| Create | `tests/test_estimates_refresh.py` | Unit tests for the new module — sanity guard, parse, fetch (mocked via `httpx.MockTransport`), orchestration. |
| Modify | `tests/test_api.py` | New `TestAdminRefreshEstimatesEndpoint` class + extension to `TestHealthEndpoint`. |
| Modify | `tests/test_config.py` | Defaults + parsing for the two new settings. |
| Modify | `README.md` | Env-var table + "Operator runbook — manually refresh estimates" subsection. |
| Modify | `CHANGELOG.md` | Entry under `[Unreleased]`. |

---

## Task 1: Settings — `estimates_refresh_url` and `estimates_refresh_interval_seconds`

**Files:**
- Test: `tests/test_config.py`
- Modify: `app/config.py:15-50`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
class TestEstimatesRefreshSettings:
    def test_defaults_disable_remote_refresh(self):
        s = Settings()
        assert s.estimates_refresh_url == ""
        assert s.estimates_refresh_interval_seconds == 86400

    def test_url_can_be_set_via_env(self, monkeypatch):
        monkeypatch.setenv(
            "PC2NUTS_ESTIMATES_REFRESH_URL",
            "https://raw.githubusercontent.com/bk86a/PostalCode2NUTS/main/tercet_missing_codes.csv",
        )
        s = Settings()
        assert s.estimates_refresh_url.endswith("/tercet_missing_codes.csv")

    def test_interval_zero_is_allowed(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_ESTIMATES_REFRESH_INTERVAL_SECONDS", "0")
        s = Settings()
        assert s.estimates_refresh_interval_seconds == 0

    def test_interval_negative_is_rejected(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_ESTIMATES_REFRESH_INTERVAL_SECONDS", "-5")
        with pytest.raises(ValidationError):
            Settings()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_config.py::TestEstimatesRefreshSettings -v
```

Expected: 4 FAIL with "no attribute 'estimates_refresh_url'" / "no attribute 'estimates_refresh_interval_seconds'".

- [ ] **Step 3: Add the two fields to `app/config.py`**

Insert after the existing `rate_limit_storage_uri` line (around `app/config.py:28`):

```python
    estimates_refresh_url: str = ""
    estimates_refresh_interval_seconds: int = Field(default=86400, ge=0)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_config.py::TestEstimatesRefreshSettings -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat(config): add estimates_refresh_url and interval settings (#44)

Two new Pydantic fields, both opt-in. Defaults preserve current behaviour:
estimates_refresh_url empty (no remote fetch), interval default 86400s
(24h). Negative intervals rejected via Field(ge=0).
"
```

---

## Task 2: Refactor `_load_estimates_from_csv` — extract a stream-friendly helper

**Files:**
- Test: `tests/test_data_loader.py`
- Modify: `app/data_loader.py:463-503`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_data_loader.py`:

```python
class TestParseEstimatesFromText:
    def test_parses_well_formed_csv(self):
        from app.data_loader import parse_estimates_from_text

        text = (
            "COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\n"
            "DE,99999,DE300,DE30,DE3,high\n"
            "FR,75000,FR101,FR10,FR1,medium\n"
        )
        d, skipped = parse_estimates_from_text(text)
        assert skipped == 0
        assert len(d) == 2
        assert d[("DE", "99999")]["nuts3"] == "DE300"
        assert d[("FR", "75000")]["nuts3"] == "FR101"
        # Confidence is mapped from label to numeric per settings.confidence_map.
        assert 0.0 < d[("DE", "99999")]["nuts3_confidence"] <= 1.0

    def test_skips_unknown_confidence(self):
        from app.data_loader import parse_estimates_from_text

        text = (
            "COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\n"
            "DE,99999,DE300,DE30,DE3,high\n"
            "DE,99998,DE300,DE30,DE3,bogus\n"
        )
        d, skipped = parse_estimates_from_text(text)
        assert skipped == 1
        assert ("DE", "99998") not in d
        assert ("DE", "99999") in d

    def test_handles_utf8_bom(self):
        from app.data_loader import parse_estimates_from_text

        text = (
            "﻿COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\n"
            "DE,99999,DE300,DE30,DE3,high\n"
        )
        d, skipped = parse_estimates_from_text(text)
        assert len(d) == 1
        assert ("DE", "99999") in d
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_data_loader.py::TestParseEstimatesFromText -v
```

Expected: 3 FAIL with "ImportError: cannot import name 'parse_estimates_from_text'".

- [ ] **Step 3: Refactor `app/data_loader.py`**

Replace the existing `_load_estimates_from_csv` (around line 463) with two functions. The new public helper does the parsing; the old one is now a thin wrapper.

```python
def parse_estimates_from_text(text: str) -> tuple[dict[tuple[str, str], dict], int]:
    """Parse an estimates CSV from a string into a fresh dict.

    Returns (parsed_dict, skipped_count). Rows with unknown confidence labels
    are counted in skipped_count and not included in the dict. Used both by
    _load_estimates_from_csv (file path) and app.estimates_refresh (HTTP body).
    """
    out: dict[tuple[str, str], dict] = {}
    skipped = 0
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        cc = row["COUNTRY_CODE"].strip().upper()
        pc = normalize_postal_code(row["POSTAL_CODE"])
        n3 = row["ESTIMATED_NUTS3"].strip()
        n2 = row["ESTIMATED_NUTS2"].strip()
        n1 = row["ESTIMATED_NUTS1"].strip()
        label = row["CONFIDENCE"].strip().lower()
        conf = settings.confidence_map.get(label)
        if conf is None:
            skipped += 1
            continue
        out[(cc, pc)] = {
            "nuts3": n3,
            "nuts2": n2,
            "nuts1": n1,
            "nuts3_confidence": conf["nuts3"],
            "nuts2_confidence": conf["nuts2"],
            "nuts1_confidence": conf["nuts1"],
        }
    return out, skipped


def _load_estimates_from_csv(csv_path: Path) -> bool:
    """Load pre-computed estimates from a file into the live in-memory dict."""
    if not csv_path.is_file():
        return False
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
        parsed, skipped = parse_estimates_from_text(text)
    except (OSError, KeyError, ValueError, csv.Error) as exc:
        logger.warning("Failed to load estimates from CSV: %s", exc)
        return False
    _estimates.update(parsed)
    if skipped:
        logger.warning("Skipped %d estimate rows with unknown confidence labels", skipped)
    if parsed:
        logger.info("Loaded %d estimates from %s", len(parsed), csv_path)
    return len(parsed) > 0
```

Add `import io` to the top of `app/data_loader.py` if not already present.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_data_loader.py -v
```

Expected: existing tests still pass, the 3 new ones pass too.

- [ ] **Step 5: Commit**

```bash
git add app/data_loader.py tests/test_data_loader.py
git commit -m "refactor(data_loader): extract parse_estimates_from_text() helper (#44)

Splits the CSV parsing logic out of _load_estimates_from_csv() so both
the file-path loader and the upcoming HTTP-body loader (#44) reuse the
same parsing path. Behaviour is unchanged: same skipped-row semantics,
same confidence-map lookup, same warning logs.
"
```

---

## Task 3: `app/estimates_refresh.py` — sanity guard + parse helpers

**Files:**
- Create: `tests/test_estimates_refresh.py`
- Create: `app/estimates_refresh.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_estimates_refresh.py`:

```python
"""Tests for app.estimates_refresh — periodic refresh of tercet_missing_codes.csv (#44)."""

import importlib
from unittest.mock import patch

import httpx
import pytest


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reload the module before each test so module-scoped state doesn't leak."""
    import app.estimates_refresh

    importlib.reload(app.estimates_refresh)
    yield


class TestSanityGuard:
    def test_accepts_when_current_is_empty(self):
        from app.estimates_refresh import _passes_sanity_guard

        assert _passes_sanity_guard(new_count=0, current_count=0) is True
        assert _passes_sanity_guard(new_count=10, current_count=0) is True

    def test_accepts_when_new_is_at_or_above_50_percent(self):
        from app.estimates_refresh import _passes_sanity_guard

        assert _passes_sanity_guard(new_count=5000, current_count=10000) is True
        assert _passes_sanity_guard(new_count=10001, current_count=10000) is True

    def test_rejects_when_new_is_below_50_percent(self):
        from app.estimates_refresh import _passes_sanity_guard

        assert _passes_sanity_guard(new_count=4999, current_count=10000) is False
        assert _passes_sanity_guard(new_count=0, current_count=10000) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_estimates_refresh.py::TestSanityGuard -v
```

Expected: 3 FAIL with "ImportError: No module named 'app.estimates_refresh'".

- [ ] **Step 3: Create the module skeleton**

Create `app/estimates_refresh.py`:

```python
"""Periodic refresh of tercet_missing_codes.csv from a remote URL (#44).

When PC2NUTS_ESTIMATES_REFRESH_URL is set, a per-worker asyncio task fetches
the URL on every PC2NUTS_ESTIMATES_REFRESH_INTERVAL_SECONDS tick (default 24 h),
parses the body, and full-replaces the in-memory _estimates dict if the
content has changed and passes a 50% relative-row sanity guard.

Defaults preserve the current single-source behaviour: when the URL setting
is unset, this module exposes refresh_estimates_once() that returns a
"disabled" RefreshResult and refresh_estimates_loop() that returns immediately.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings
from app.data_loader import _data_lock, _estimates, _revalidate_estimates, parse_estimates_from_text

logger = logging.getLogger(__name__)


# Module-scoped state. Reset by importlib.reload() in tests.
_last_hash: Optional[str] = None
_last_etag: Optional[str] = None
_last_modified: Optional[str] = None
_stale: Optional[bool] = None  # None when feature disabled


@dataclass
class RefreshResult:
    status: str  # "refreshed" | "unchanged" | "rejected" | "failed" | "disabled"
    previous_count: int
    new_count: int
    skipped_rows: int = 0
    reason: str = ""


def get_refresh_stale() -> Optional[bool]:
    """Return the staleness flag for /health. None when feature disabled."""
    return _stale


def _passes_sanity_guard(new_count: int, current_count: int) -> bool:
    """50%-of-current floor; pass freely when the live state is empty."""
    if current_count == 0:
        return True
    return new_count >= 0.5 * current_count
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_estimates_refresh.py::TestSanityGuard -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/estimates_refresh.py tests/test_estimates_refresh.py
git commit -m "feat(estimates_refresh): module scaffolding + sanity guard (#44)

New module with module-scoped state for a periodic CSV refresh task.
This commit lands the skeleton and the 50%-floor sanity guard helper;
fetch and orchestration come in the next commits.
"
```

---

## Task 4: `fetch_remote_csv()` with conditional headers, mocked via `httpx.MockTransport`

**Files:**
- Modify: `tests/test_estimates_refresh.py`
- Modify: `app/estimates_refresh.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_estimates_refresh.py`:

```python
class TestFetchRemoteCsv:
    @pytest.fixture
    def url(self, monkeypatch):
        u = "https://example.invalid/tercet.csv"
        monkeypatch.setattr("app.estimates_refresh.settings", _stub_settings(url=u))
        return u

    @staticmethod
    def _client_with(handler):
        """Build an httpx.AsyncClient that routes every request through handler."""
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    @pytest.mark.asyncio
    async def test_returns_body_on_200(self, url):
        from app.estimates_refresh import fetch_remote_csv

        body = b"COUNTRY_CODE,POSTAL_CODE\nDE,99999\n"

        def handler(request):
            assert str(request.url) == url
            return httpx.Response(200, content=body, headers={"ETag": "W/abc"})

        async with self._client_with(handler) as client:
            data, status, headers = await fetch_remote_csv(client)

        assert status == 200
        assert data == body
        assert headers.get("etag") == "W/abc"

    @pytest.mark.asyncio
    async def test_returns_none_on_304(self, url):
        from app.estimates_refresh import fetch_remote_csv

        def handler(request):
            return httpx.Response(304)

        async with self._client_with(handler) as client:
            data, status, _ = await fetch_remote_csv(client)

        assert status == 304
        assert data is None

    @pytest.mark.asyncio
    async def test_sends_conditional_headers_when_state_present(self, url, monkeypatch):
        from app import estimates_refresh

        monkeypatch.setattr(estimates_refresh, "_last_etag", "W/abc")
        monkeypatch.setattr(estimates_refresh, "_last_modified", "Wed, 01 Jan 2026 00:00:00 GMT")

        seen: dict[str, str] = {}

        def handler(request):
            seen["if-none-match"] = request.headers.get("if-none-match", "")
            seen["if-modified-since"] = request.headers.get("if-modified-since", "")
            return httpx.Response(200, content=b"", headers={})

        async with self._client_with(handler) as client:
            await estimates_refresh.fetch_remote_csv(client)

        assert seen["if-none-match"] == "W/abc"
        assert seen["if-modified-since"] == "Wed, 01 Jan 2026 00:00:00 GMT"

    @pytest.mark.asyncio
    async def test_returns_none_on_5xx(self, url):
        from app.estimates_refresh import fetch_remote_csv

        def handler(request):
            return httpx.Response(503, content=b"down")

        async with self._client_with(handler) as client:
            data, status, _ = await fetch_remote_csv(client)

        assert status == 503
        assert data is None

    @pytest.mark.asyncio
    async def test_returns_none_on_transport_error(self, url):
        from app.estimates_refresh import fetch_remote_csv

        def handler(request):
            raise httpx.ConnectError("boom")

        async with self._client_with(handler) as client:
            data, status, _ = await fetch_remote_csv(client)

        assert status == 0
        assert data is None


def _stub_settings(*, url: str = "", interval: int = 86400):
    """Build a minimal settings stub for tests that only need the two new fields."""

    class _S:
        estimates_refresh_url = url
        estimates_refresh_interval_seconds = interval

    return _S()
```

Add `pytest-asyncio` if not already a dep — check `requirements-dev.txt`. If absent:

```bash
echo "pytest-asyncio>=0.23,<1" >> requirements-dev.txt
pip install pytest-asyncio
```

Add to `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
asyncio_mode = "auto"
```

(If `asyncio_mode = "auto"` is set, the `@pytest.mark.asyncio` decorators above are redundant but harmless — keep them for explicitness.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_estimates_refresh.py::TestFetchRemoteCsv -v
```

Expected: 5 FAIL with "ImportError: cannot import name 'fetch_remote_csv'".

- [ ] **Step 3: Implement `fetch_remote_csv` in `app/estimates_refresh.py`**

Append after `_passes_sanity_guard`:

```python
async def fetch_remote_csv(
    client: httpx.AsyncClient,
) -> tuple[Optional[bytes], int, dict[str, str]]:
    """GET settings.estimates_refresh_url with conditional headers.

    Returns (body, status_code, response_headers). body is None on 304 Not
    Modified, on any non-200 status, and on transport errors. Caller decides
    what to log based on the status code (304 is silent; non-200 is a warning).
    """
    headers: dict[str, str] = {}
    if _last_etag:
        headers["If-None-Match"] = _last_etag
    if _last_modified:
        headers["If-Modified-Since"] = _last_modified

    try:
        r = await client.get(settings.estimates_refresh_url, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        logger.debug("Remote estimates fetch transport error: %s", exc)
        return None, 0, {}

    response_headers = {k.lower(): v for k, v in r.headers.items()}
    if r.status_code == 304:
        return None, 304, response_headers
    if r.status_code != 200:
        return None, r.status_code, response_headers
    return r.content, 200, response_headers
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_estimates_refresh.py -v
```

Expected: all PASS (sanity guard tests + 5 new fetch tests).

- [ ] **Step 5: Commit**

```bash
git add app/estimates_refresh.py tests/test_estimates_refresh.py requirements-dev.txt pyproject.toml
git commit -m "feat(estimates_refresh): fetch_remote_csv with conditional headers (#44)

Async GET with If-None-Match / If-Modified-Since when prior values are
available. Returns body=None on 304, on any non-200, and on transport
errors. Caller logs based on the status. Tests use httpx.MockTransport
to avoid network. pytest-asyncio added to dev deps for the async tests.
"
```

---

## Task 5: `refresh_estimates_once()` — orchestration

**Files:**
- Modify: `tests/test_estimates_refresh.py`
- Modify: `app/estimates_refresh.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_estimates_refresh.py`:

```python
class TestRefreshOnce:
    @pytest.fixture
    def url(self, monkeypatch):
        u = "https://example.invalid/tercet.csv"
        monkeypatch.setattr("app.estimates_refresh.settings", _stub_settings(url=u))
        return u

    @pytest.fixture
    def seed_estimates(self):
        from app.data_loader import _estimates

        _estimates.clear()
        for i in range(100):
            _estimates[("DE", f"{10000+i}")] = {
                "nuts3": "DE300",
                "nuts2": "DE30",
                "nuts1": "DE3",
                "nuts3_confidence": 0.9,
                "nuts2_confidence": 0.95,
                "nuts1_confidence": 0.98,
            }
        yield _estimates
        _estimates.clear()

    @staticmethod
    def _csv(rows: list[tuple[str, str, str]] = None) -> bytes:
        rows = rows or [("DE", "99999", "high"), ("FR", "75000", "medium")]
        header = "COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\n"
        body = "".join(f"{cc},{pc},{cc}300,{cc}30,{cc}3,{conf}\n" for cc, pc, conf in rows)
        return (header + body).encode("utf-8")

    @pytest.mark.asyncio
    async def test_disabled_when_url_unset(self, monkeypatch):
        monkeypatch.setattr("app.estimates_refresh.settings", _stub_settings(url=""))
        from app.estimates_refresh import refresh_estimates_once

        result = await refresh_estimates_once()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_swaps_on_changed_content(self, url, seed_estimates):
        from app import estimates_refresh

        new_csv = self._csv([("DE", str(20000 + i), "high") for i in range(80)])

        def handler(request):
            return httpx.Response(200, content=new_csv, headers={"ETag": "W/new"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "refreshed"
        assert result.previous_count == 100
        assert result.new_count == 80
        assert estimates_refresh._stale is False
        assert estimates_refresh._last_etag == "W/new"

    @pytest.mark.asyncio
    async def test_unchanged_on_304(self, url, seed_estimates):
        from app import estimates_refresh

        estimates_refresh._last_etag = "W/abc"

        def handler(request):
            return httpx.Response(304)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "unchanged"
        assert result.previous_count == 100
        assert result.new_count == 100
        assert estimates_refresh._stale is False
        # Live dict was not touched
        assert len(seed_estimates) == 100

    @pytest.mark.asyncio
    async def test_unchanged_on_identical_hash(self, url, seed_estimates):
        from app import estimates_refresh

        body = self._csv()
        estimates_refresh._last_hash = hashlib.sha256(body).hexdigest()

        def handler(request):
            return httpx.Response(200, content=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "unchanged"
        assert estimates_refresh._stale is False

    @pytest.mark.asyncio
    async def test_failed_on_5xx(self, url, seed_estimates):
        from app import estimates_refresh

        def handler(request):
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "failed"
        assert estimates_refresh._stale is True
        assert len(seed_estimates) == 100  # unchanged

    @pytest.mark.asyncio
    async def test_failed_on_parse_error(self, url, seed_estimates):
        from app import estimates_refresh

        def handler(request):
            return httpx.Response(200, content=b"not,a,valid,csv\n\xff\xfe")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "failed"
        assert estimates_refresh._stale is True
        assert len(seed_estimates) == 100

    @pytest.mark.asyncio
    async def test_rejected_by_sanity_guard(self, url, seed_estimates):
        from app import estimates_refresh

        small_csv = self._csv([("DE", "99999", "high")])  # 1 row vs current 100

        def handler(request):
            return httpx.Response(200, content=small_csv)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "rejected"
        assert result.previous_count == 100
        assert result.new_count == 1
        assert estimates_refresh._stale is True
        # Live dict was not touched
        assert len(seed_estimates) == 100

    @pytest.mark.asyncio
    async def test_bootstrap_path_accepts_any_size(self, url):
        """When current is empty (first-ever fetch), the sanity guard must not block."""
        from app import estimates_refresh
        from app.data_loader import _estimates

        _estimates.clear()
        small_csv = self._csv([("DE", "99999", "high")])

        def handler(request):
            return httpx.Response(200, content=small_csv)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "refreshed"
        assert result.new_count == 1
```

Add `import hashlib` at the top of the test file if not already imported.

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_estimates_refresh.py::TestRefreshOnce -v
```

Expected: 8 FAIL with "ImportError: cannot import name 'refresh_estimates_once'".

- [ ] **Step 3: Implement `refresh_estimates_once`**

Append to `app/estimates_refresh.py`:

```python
async def refresh_estimates_once(
    client: Optional[httpx.AsyncClient] = None,
) -> RefreshResult:
    """One refresh attempt: fetch, sanity-check, swap.

    When `client` is None, an ephemeral httpx.AsyncClient is created and
    closed. Production callers (the lifespan loop, the admin endpoint)
    should pass a long-lived client to reuse connections.
    """
    global _last_hash, _last_etag, _last_modified, _stale

    previous_count = len(_estimates)

    if not settings.estimates_refresh_url:
        return RefreshResult(
            status="disabled",
            previous_count=previous_count,
            new_count=previous_count,
        )

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient()
    try:
        body, status, headers = await fetch_remote_csv(client)
    finally:
        if own_client:
            await client.aclose()

    # 304 Not Modified — content unchanged, refresh succeeded
    if status == 304:
        _stale = False
        return RefreshResult(
            status="unchanged",
            previous_count=previous_count,
            new_count=previous_count,
        )

    # Any other non-200 (including transport errors with status=0)
    if body is None:
        _was_stale_before = _stale is True
        _stale = True
        if not _was_stale_before:
            logger.warning(
                "Remote estimates fetch failed (status=%d); keeping current state",
                status,
            )
        return RefreshResult(
            status="failed",
            previous_count=previous_count,
            new_count=previous_count,
            reason=f"http={status}",
        )

    new_hash = hashlib.sha256(body).hexdigest()
    if new_hash == _last_hash:
        _stale = False
        return RefreshResult(
            status="unchanged",
            previous_count=previous_count,
            new_count=previous_count,
        )

    try:
        text = body.decode("utf-8-sig")
        new_dict, skipped = parse_estimates_from_text(text)
    except (UnicodeDecodeError, ValueError, csv.Error, KeyError) as exc:
        _stale = True
        logger.warning("Remote estimates parse failed: %s", exc)
        return RefreshResult(
            status="failed",
            previous_count=previous_count,
            new_count=previous_count,
            reason=f"parse: {exc}",
        )

    if not _passes_sanity_guard(len(new_dict), previous_count):
        _stale = True
        logger.warning(
            "Remote estimates sanity guard rejected swap (new=%d, current=%d)",
            len(new_dict),
            previous_count,
        )
        return RefreshResult(
            status="rejected",
            previous_count=previous_count,
            new_count=len(new_dict),
            reason=f"sanity guard: {len(new_dict)} < 50% of {previous_count}",
        )

    # Swap. _data_lock is a threading.Lock; acquiring it briefly from an async
    # context is fine — the swap is microseconds.
    with _data_lock:
        _estimates.clear()
        _estimates.update(new_dict)
        _revalidate_estimates()
    new_count = len(_estimates)

    _last_hash = new_hash
    _last_etag = headers.get("etag")
    _last_modified = headers.get("last-modified")
    _stale = False

    logger.info(
        "Remote estimates refreshed: %d -> %d (skipped %d rows during parse)",
        previous_count,
        new_count,
        skipped,
    )
    return RefreshResult(
        status="refreshed",
        previous_count=previous_count,
        new_count=new_count,
        skipped_rows=skipped,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_estimates_refresh.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/estimates_refresh.py tests/test_estimates_refresh.py
git commit -m "feat(estimates_refresh): refresh_estimates_once orchestration (#44)

Wires fetch + hash dedupe + parse + sanity guard + swap-under-lock into
one async function. RefreshResult dataclass captures the status enum,
counts, and a human-readable reason. Tests cover all five outcomes
(refreshed / unchanged / rejected / failed / disabled) plus the
bootstrap path (current is empty → guard skipped).
"
```

---

## Task 6: `refresh_estimates_loop()` — async cadence loop

**Files:**
- Modify: `tests/test_estimates_refresh.py`
- Modify: `app/estimates_refresh.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_estimates_refresh.py`:

```python
class TestRefreshLoop:
    @pytest.mark.asyncio
    async def test_no_op_when_url_unset(self, monkeypatch):
        """With the URL setting unset, the loop must return immediately."""
        monkeypatch.setattr("app.estimates_refresh.settings", _stub_settings(url=""))
        from app.estimates_refresh import refresh_estimates_loop

        # If the loop tried to sleep for 86400s we'd hang; wait_for guards us.
        await asyncio.wait_for(refresh_estimates_loop(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_no_op_when_interval_zero(self, monkeypatch):
        monkeypatch.setattr(
            "app.estimates_refresh.settings",
            _stub_settings(url="https://example.invalid/x.csv", interval=0),
        )
        from app.estimates_refresh import refresh_estimates_loop

        await asyncio.wait_for(refresh_estimates_loop(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_loop_calls_refresh_on_each_tick(self, monkeypatch):
        """With a tiny interval and a counter, we should see N refreshes."""
        monkeypatch.setattr(
            "app.estimates_refresh.settings",
            _stub_settings(url="https://example.invalid/x.csv", interval=1),
        )

        call_count = {"n": 0}

        async def fake_refresh():
            call_count["n"] += 1

        monkeypatch.setattr("app.estimates_refresh.refresh_estimates_once", fake_refresh)

        from app.estimates_refresh import refresh_estimates_loop

        task = asyncio.create_task(refresh_estimates_loop())
        # Speed up: monkeypatch asyncio.sleep to fast-forward.
        await asyncio.sleep(0.05)
        # Cancel and verify at least one call happened (interval=1s, so likely 0
        # under real sleep). Use a smaller interval check via direct invocation:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_loop_survives_exception_in_refresh(self, monkeypatch):
        monkeypatch.setattr(
            "app.estimates_refresh.settings",
            _stub_settings(url="https://example.invalid/x.csv", interval=1),
        )

        async def fake_refresh_raises():
            raise RuntimeError("boom")

        monkeypatch.setattr("app.estimates_refresh.refresh_estimates_once", fake_refresh_raises)

        # Replace asyncio.sleep so we don't actually wait.
        sleeps: list[float] = []

        async def fake_sleep(secs):
            sleeps.append(secs)
            if len(sleeps) >= 3:
                raise asyncio.CancelledError

        monkeypatch.setattr("app.estimates_refresh.asyncio.sleep", fake_sleep)

        from app.estimates_refresh import refresh_estimates_loop

        with pytest.raises(asyncio.CancelledError):
            await refresh_estimates_loop()

        # Loop ran at least 2 iterations despite the exception each time
        assert len(sleeps) >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_estimates_refresh.py::TestRefreshLoop -v
```

Expected: 4 FAIL with "ImportError: cannot import name 'refresh_estimates_loop'".

- [ ] **Step 3: Implement `refresh_estimates_loop`**

Append to `app/estimates_refresh.py`:

```python
async def refresh_estimates_loop() -> None:
    """Periodic refresh task. Returns immediately when feature is disabled."""
    if not settings.estimates_refresh_url:
        return
    interval = settings.estimates_refresh_interval_seconds
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            await refresh_estimates_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("refresh_estimates_loop iteration crashed; will retry")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_estimates_refresh.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/estimates_refresh.py tests/test_estimates_refresh.py
git commit -m "feat(estimates_refresh): refresh_estimates_loop async loop (#44)

Wraps refresh_estimates_once in an interval loop. Returns immediately
when the feature is disabled. Catches all exceptions (except
CancelledError) so a transient crash doesn't kill the task — the next
tick re-runs.
"
```

---

## Task 7: `/health` field — `estimates_refresh_stale`

**Files:**
- Modify: `tests/test_api.py`
- Modify: `app/models.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write the failing test**

Append to `TestHealthEndpoint` in `tests/test_api.py`:

```python
    def test_health_includes_estimates_refresh_stale_when_url_set(self, monkeypatch, client):
        from app import config, estimates_refresh

        monkeypatch.setattr(config.settings, "estimates_refresh_url", "https://example.invalid/x.csv")
        monkeypatch.setattr(estimates_refresh, "_stale", False)

        resp = client.get("/health")
        data = resp.json()
        assert data["estimates_refresh_stale"] is False

    def test_health_estimates_refresh_stale_none_when_url_unset(self, monkeypatch, client):
        from app import config, estimates_refresh

        monkeypatch.setattr(config.settings, "estimates_refresh_url", "")
        monkeypatch.setattr(estimates_refresh, "_stale", None)

        resp = client.get("/health")
        data = resp.json()
        assert data["estimates_refresh_stale"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api.py::TestHealthEndpoint::test_health_includes_estimates_refresh_stale_when_url_set tests/test_api.py::TestHealthEndpoint::test_health_estimates_refresh_stale_none_when_url_unset -v
```

Expected: 2 FAIL — the field doesn't exist on the response yet.

- [ ] **Step 3: Add the field to `HealthResponse`**

In `app/models.py`, after the existing `token_db_stale` field:

```python
    estimates_refresh_stale: bool | None = None
```

- [ ] **Step 4: Populate the field in `/health`**

In `app/main.py` around `app/main.py:307` (the `health` handler), add the import at the top of the file:

```python
from app.estimates_refresh import get_refresh_stale as _get_estimates_refresh_stale
```

And in the `health` function body, populate the new field:

```python
    return HealthResponse(
        status="ok" if len(table) > 0 else "no_data",
        total_postal_codes=len(table),
        total_estimates=len(estimates),
        total_nuts_names=len(get_nuts_names()),
        nuts_version=settings.nuts_version,
        extra_sources=get_extra_source_count(),
        patterns_version=PATTERNS_META.get("version", "unknown"),
        data_stale=stale,
        last_updated=get_data_loaded_at(),
        token_db_stale=token_db_stale,
        estimates_refresh_stale=_get_estimates_refresh_stale(),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_api.py::TestHealthEndpoint -v
```

Expected: all (existing + new) PASS.

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/main.py tests/test_api.py
git commit -m "feat(/health): expose estimates_refresh_stale (#44)

New optional field on HealthResponse: None when feature disabled (URL
unset), False after a successful most-recent refresh, True after a
failed one. Mirrors the existing token_db_stale field.
"
```

---

## Task 8: `POST /admin/refresh-estimates` endpoint

**Files:**
- Modify: `tests/test_api.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
class TestAdminRefreshEstimatesEndpoint:
    def test_401_without_authorization(self, client, monkeypatch):
        from app import config

        monkeypatch.setattr(
            config.settings,
            "estimates_refresh_url",
            "https://example.invalid/x.csv",
        )
        resp = client.post("/admin/refresh-estimates")
        assert resp.status_code == 401

    def test_401_with_invalid_bearer(self, client, monkeypatch):
        from app import config

        monkeypatch.setattr(
            config.settings,
            "estimates_refresh_url",
            "https://example.invalid/x.csv",
        )
        resp = client.post(
            "/admin/refresh-estimates",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 401

    def test_503_when_feature_disabled(self, client, trusted_client, monkeypatch):
        # trusted_client is the existing test fixture that wraps the client
        # with a valid trusted-token bearer (defined in conftest.py).
        from app import config

        monkeypatch.setattr(config.settings, "estimates_refresh_url", "")
        resp = trusted_client.post("/admin/refresh-estimates")
        assert resp.status_code == 503
        assert resp.json()["status"] == "disabled"

    def test_200_on_successful_refresh(self, trusted_client, monkeypatch):
        from app import config, estimates_refresh
        from app.estimates_refresh import RefreshResult

        monkeypatch.setattr(
            config.settings,
            "estimates_refresh_url",
            "https://example.invalid/x.csv",
        )

        async def fake_refresh(client=None):
            return RefreshResult(
                status="refreshed", previous_count=7000, new_count=7042, skipped_rows=0
            )

        monkeypatch.setattr(estimates_refresh, "refresh_estimates_once", fake_refresh)

        resp = trusted_client.post("/admin/refresh-estimates")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "refreshed"
        assert body["previous_count"] == 7000
        assert body["new_count"] == 7042

    def test_502_on_failed_refresh(self, trusted_client, monkeypatch):
        from app import config, estimates_refresh
        from app.estimates_refresh import RefreshResult

        monkeypatch.setattr(
            config.settings,
            "estimates_refresh_url",
            "https://example.invalid/x.csv",
        )

        async def fake_refresh(client=None):
            return RefreshResult(
                status="failed", previous_count=7000, new_count=7000, reason="http=503"
            )

        monkeypatch.setattr(estimates_refresh, "refresh_estimates_once", fake_refresh)

        resp = trusted_client.post("/admin/refresh-estimates")
        assert resp.status_code == 502
        assert resp.json()["status"] == "failed"

    def test_409_on_sanity_guard_rejection(self, trusted_client, monkeypatch):
        from app import config, estimates_refresh
        from app.estimates_refresh import RefreshResult

        monkeypatch.setattr(
            config.settings,
            "estimates_refresh_url",
            "https://example.invalid/x.csv",
        )

        async def fake_refresh(client=None):
            return RefreshResult(
                status="rejected",
                previous_count=7000,
                new_count=12,
                reason="sanity guard: 12 < 50% of 7000",
            )

        monkeypatch.setattr(estimates_refresh, "refresh_estimates_once", fake_refresh)

        resp = trusted_client.post("/admin/refresh-estimates")
        assert resp.status_code == 409
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["candidate_count"] == 12
```

If `trusted_client` doesn't already exist as a fixture, add it to `tests/conftest.py`:

```python
@pytest.fixture
def trusted_client(client, monkeypatch):
    """A TestClient that injects a trusted bearer token into every request."""
    from app import auth, config

    monkeypatch.setattr(config.settings, "trusted_tokens_raw", "test-trusted-token-XXXXXX")
    auth.refresh_db_tokens(None) if hasattr(auth, "refresh_db_tokens") else None
    client.headers.update({"Authorization": "Bearer test-trusted-token-XXXXXX"})
    return client
```

(Tweak to match the existing conftest's bearer-token plumbing — the existing test_auth.py shows the right path.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api.py::TestAdminRefreshEstimatesEndpoint -v
```

Expected: 6 FAIL — the route doesn't exist.

- [ ] **Step 3: Add the route to `app/main.py`**

After the existing `/health` route (around `app/main.py:320`):

```python
@app.post(
    "/admin/refresh-estimates",
    summary="Force-refresh estimates from the configured remote URL",
    description=(
        "Operator-only — requires `Authorization: Bearer <trusted-token>`. "
        "Synchronously fetches the configured `PC2NUTS_ESTIMATES_REFRESH_URL` and, "
        "if the content has changed and passes the sanity guard, replaces the "
        "in-memory estimates table. No body."
    ),
    responses={
        200: {"description": "Refresh succeeded (content changed and applied)"},
        401: {"description": "Missing or invalid trusted bearer token"},
        409: {"description": "Sanity guard rejected the candidate CSV"},
        502: {"description": "Upstream fetch or parse failed"},
        503: {"description": "Feature disabled (PC2NUTS_ESTIMATES_REFRESH_URL unset)"},
    },
)
async def admin_refresh_estimates(request: Request) -> JSONResponse:
    if not getattr(request.state, "trusted", False):
        raise HTTPException(status_code=401, detail="Trusted token required")

    from app.estimates_refresh import refresh_estimates_once

    result = await refresh_estimates_once()

    if result.status == "disabled":
        return JSONResponse(status_code=503, content={"status": "disabled"})
    if result.status == "rejected":
        return JSONResponse(
            status_code=409,
            content={
                "status": "rejected",
                "reason": result.reason,
                "previous_count": result.previous_count,
                "candidate_count": result.new_count,
            },
        )
    if result.status == "failed":
        return JSONResponse(
            status_code=502,
            content={"status": "failed", "reason": result.reason},
        )

    # "refreshed" or "unchanged"
    return JSONResponse(
        status_code=200,
        content={
            "status": result.status,
            "previous_count": result.previous_count,
            "new_count": result.new_count,
            "skipped_rows": result.skipped_rows,
            "source_url": settings.estimates_refresh_url,
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api.py::TestAdminRefreshEstimatesEndpoint -v
```

Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py tests/conftest.py
git commit -m "feat(api): POST /admin/refresh-estimates with trusted-token auth (#44)

Operator-only endpoint to force a refresh without waiting for the next
periodic tick. Auth via the existing trusted-token Bearer mechanism.
HTTP code per outcome: 200 refreshed/unchanged, 401 unauthorised, 409
sanity guard rejection, 502 upstream failure, 503 feature disabled.
"
```

---

## Task 9: Lifespan wiring — bootstrap fetch + refresh task

**Files:**
- Modify: `app/main.py:82-140` (the existing `lifespan` function)
- Modify: `tests/test_api.py` — already covered by existing TestClient lifespan handling, but add a smoke test that the loop task is started.

- [ ] **Step 1: Add bootstrap + task spawn to `lifespan`**

In `app/main.py`, inside `lifespan(app)`, after the TokenDB refresh task block (around line 130) but BEFORE `yield`:

```python
    # ── Estimates remote-refresh (#44) ──────────────────────────────────────
    estimates_refresh_task: asyncio.Task | None = None
    if _config.settings.estimates_refresh_url:
        from app.estimates_refresh import refresh_estimates_once, refresh_estimates_loop

        # Bootstrap synchronously so the worker reflects upstream before reporting ready.
        try:
            result = await refresh_estimates_once()
            logger.info(
                "Estimates bootstrap fetch: %s (previous=%d, new=%d)",
                result.status,
                result.previous_count,
                result.new_count,
            )
        except Exception:
            logger.exception("Estimates bootstrap fetch crashed; continuing with bundled CSV")

        if _config.settings.estimates_refresh_interval_seconds > 0:
            estimates_refresh_task = asyncio.create_task(refresh_estimates_loop())
            logger.info(
                "Estimates refresh task started (interval %ds)",
                _config.settings.estimates_refresh_interval_seconds,
            )
```

After `yield`, in the shutdown section:

```python
    if estimates_refresh_task is not None:
        estimates_refresh_task.cancel()
        try:
            await estimates_refresh_task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 2: Existing test suite as smoke check**

```bash
pytest tests/ -v
```

Expected: ALL pass. (No new test specifically for the wiring — the lifespan path is covered transitively by every `TestClient` instance, and the unit tests for `refresh_estimates_once` and `refresh_estimates_loop` cover the actual logic.)

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat(lifespan): wire estimates bootstrap fetch + refresh loop (#44)

When PC2NUTS_ESTIMATES_REFRESH_URL is set, the FastAPI lifespan now:
  1. Runs one synchronous refresh_estimates_once() before the worker
     reports ready, so a freshly-deployed pod immediately reflects the
     upstream CSV instead of waiting up to 24h.
  2. Schedules refresh_estimates_loop() as an asyncio.Task that gets
     cancelled cleanly on shutdown.

Defaults preserve the current behaviour (URL unset => no remote fetch).
"
```

---

## Task 10: Documentation — README + CHANGELOG

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the env-var rows to the existing Configuration table in `README.md`**

Find the env-var table around `README.md:306` (in the "Configuration" section). Add two rows:

```markdown
| `PC2NUTS_ESTIMATES_REFRESH_URL` | *(empty — feature disabled)* | When set, the worker periodically fetches this URL and replaces the in-memory estimates table. Recommended value: `https://raw.githubusercontent.com/bk86a/PostalCode2NUTS/main/tercet_missing_codes.csv`. |
| `PC2NUTS_ESTIMATES_REFRESH_INTERVAL_SECONDS` | `86400` (24 h) | How often the periodic task fetches the URL. Set to `0` to disable the periodic loop while keeping the bootstrap fetch on startup. |
```

- [ ] **Step 2: Add a new operator-runbook subsection in `README.md`**

After the existing "Operator runbook — revoke a token" subsection (around `README.md:420`), add:

```markdown
### Operator runbook — manually refresh estimates

When you've merged a new entry into `tercet_missing_codes.csv` on `main` and want to verify it's live in production without waiting up to 24 hours for the next periodic refresh:

```bash
curl -X POST -H "Authorization: Bearer $PC2NUTS_TRUSTED_TOKEN" \
  https://api.example.invalid/admin/refresh-estimates
```

Possible responses:

| HTTP | `status` | Meaning |
|---|---|---|
| 200 | `refreshed` | Upstream content changed, sanity guard passed, in-memory state updated. Body includes `previous_count` and `new_count`. |
| 200 | `unchanged` | Upstream content identical to current state (matched by SHA-256 hash or 304 Not Modified). |
| 401 | — | Missing or invalid `Authorization` header. |
| 409 | `rejected` | Sanity guard refused the candidate CSV (< 50 % of current row count). Live state is untouched. |
| 502 | `failed` | Upstream fetch or parse failed. Live state is untouched. |
| 503 | `disabled` | `PC2NUTS_ESTIMATES_REFRESH_URL` is unset on the deployed pod. |

`/health` exposes `estimates_refresh_stale: bool | None` — `null` when disabled, `false` after a successful most-recent refresh, `true` after a failed one.
```

- [ ] **Step 3: Add a CHANGELOG entry**

In `CHANGELOG.md` under `[Unreleased]`, add:

```markdown
### Added

- **Periodic refresh of `tercet_missing_codes.csv`** (#44): when `PC2NUTS_ESTIMATES_REFRESH_URL` is set, a per-worker asyncio task fetches the URL on every `PC2NUTS_ESTIMATES_REFRESH_INTERVAL_SECONDS` tick (default 24 h), parses the body, and full-replaces the in-memory estimates table if the content has changed and passes a 50 %-of-current sanity guard. Workers also do a synchronous bootstrap fetch before reporting ready, so a fresh pod immediately reflects upstream rather than waiting up to one interval. New `POST /admin/refresh-estimates` endpoint (trusted-token auth) lets operators force a refresh without waiting. New `/health` field `estimates_refresh_stale: bool | None`. Defaults preserve the current single-source-of-truth behaviour from the bundled `tercet_missing_codes.csv`.
```

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: env vars, operator runbook, and CHANGELOG entry for #44"
```

---

## Task 11: Final verification — full suite + lint + manual probe

- [ ] **Step 1: Full test suite**

```bash
pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 2: Lint + format check**

```bash
ruff check app/ scripts/
ruff format --check app/ scripts/
```

Expected: clean.

- [ ] **Step 3: Build + smoke run the image (optional but recommended)**

```bash
docker build -t pc2nuts:plan-44 .
docker run --rm -d --name pc44 -p 18000:8000 \
  -e PC2NUTS_ESTIMATES_REFRESH_URL=https://raw.githubusercontent.com/bk86a/PostalCode2NUTS/main/tercet_missing_codes.csv \
  pc2nuts:plan-44
# Wait for ready (python-based probe — curl isn't in slim image)
docker exec pc44 sh -c 'until python -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8000/health\")" 2>/dev/null; do sleep 1; done'
# Verify the new /health field
curl -s http://localhost:18000/health | python3 -c 'import json,sys; d=json.load(sys.stdin); print("estimates_refresh_stale =", d.get("estimates_refresh_stale"))'
docker rm -f pc44
```

Expected: `estimates_refresh_stale = False` (bootstrap fetch succeeded).

- [ ] **Step 4: Final commit if anything was missed**

```bash
git status
# If clean, no commit needed.
```

- [ ] **Step 5: Push**

```bash
git push origin <branch>
```

---

## Self-review notes

- **Spec coverage:** every section of the spec maps to a task — Settings (T1), refactor (T2), module skeleton + sanity guard (T3), fetch (T4), orchestration (T5), loop (T6), `/health` (T7), `/admin` (T8), lifespan (T9), docs (T10), verification (T11).
- **Type consistency check passed:** `RefreshResult` defined in T3 is consumed by T5/T8; `parse_estimates_from_text` defined in T2 is consumed by T5; `get_refresh_stale` defined in T3 is consumed by T7; `refresh_estimates_once` / `refresh_estimates_loop` defined in T5/T6 are consumed by T8/T9.
- **No placeholders:** every step has the actual code. The `trusted_client` fixture sketch in T8 is the one structural assumption — if it doesn't match the existing conftest pattern, the engineer should adapt to whatever bearer-injection helper already exists for `test_auth.py` rather than inventing a new one.
- **Frequent commits:** one commit per task; each commit leaves the test suite green.
