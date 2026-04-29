# Auth-token bypass implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow trusted callers to bypass the per-IP `60/minute` rate limit by presenting `Authorization: Bearer <token>`. Tokens are managed via the `PC2NUTS_TRUSTED_TOKENS` env var (comma-separated, restart to apply). Implements [#60](https://github.com/bk86a/PostalCode2NUTS/issues/60) per [the design spec](../specs/2026-04-29-auth-token-bypass-design.md).

**Architecture:** Single new module `app/auth.py` containing token parsing, timing-safe validation, audit-id derivation, a `contextvars`-backed predicate, and the auth middleware. `app/main.py` registers the middleware and threads `exempt_when=is_trusted_request` into the existing `@limiter.limit(...)` decorators; `AccessLogMiddleware` gains a `token_id=` field. No changes to `data_loader.py` or settings.json schema beyond a new env-var parsing property in `config.py`.

**Tech Stack:** FastAPI / Starlette middleware, slowapi `exempt_when` predicate, `hmac.compare_digest` for timing-safe comparison, `hashlib.sha256` for the audit id, `contextvars.ContextVar` for per-request state in the parameterless `exempt_when` callable.

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `app/auth.py` | **new** (~110 LOC) | `extract_bearer`, `is_trusted`, `token_id`, `_request_var` ContextVar, `is_trusted_request` predicate, `AuthMiddleware` |
| `app/config.py` | modify | new `trusted_tokens` property: parse `PC2NUTS_TRUSTED_TOKENS` → `frozenset[str]` |
| `app/main.py` | modify | register `AuthMiddleware`; pass `exempt_when=is_trusted_request` to two `@limiter.limit(...)` decorators; extend `AccessLogMiddleware` log line with `token_id=<8hex>` for trusted requests |
| `tests/test_auth.py` | **new** | unit tests for every helper in `app/auth.py` |
| `tests/test_api.py` | modify | endpoint tests for the four behaviour rows in spec §4; audit log assertions |
| `tests/conftest.py` | modify | new `trusted_client` fixture parameterising `settings.trusted_tokens` |
| `README.md` | modify | new "Authentication & rate-limit bypass" section, including the operator runbook from spec §2 |
| `CHANGELOG.md` | modify | new `[Unreleased]` entry under `### Added` |

---

## Task 1: `trusted_tokens` settings property

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_auth.py` (new file — start with this section)

- [ ] **Step 1: Create `tests/test_auth.py` with the failing config test**

```python
"""Tests for app/auth.py and trusted_tokens config parsing."""

import os
from importlib import reload

import pytest


# ── trusted_tokens config property ───────────────────────────────────────────


class TestTrustedTokensConfig:
    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        # Clear so each test sets only what it needs
        monkeypatch.delenv("PC2NUTS_TRUSTED_TOKENS", raising=False)

    def _reload_settings(self):
        from app import config

        reload(config)
        return config.settings

    def test_unset_returns_empty_frozenset(self):
        settings = self._reload_settings()
        assert settings.trusted_tokens == frozenset()

    def test_empty_string_returns_empty_frozenset(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_TRUSTED_TOKENS", "")
        settings = self._reload_settings()
        assert settings.trusted_tokens == frozenset()

    def test_single_token(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_TRUSTED_TOKENS", "abc123")
        settings = self._reload_settings()
        assert settings.trusted_tokens == frozenset({"abc123"})

    def test_multiple_tokens_comma_separated(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_TRUSTED_TOKENS", "abc,def,ghi")
        settings = self._reload_settings()
        assert settings.trusted_tokens == frozenset({"abc", "def", "ghi"})

    def test_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_TRUSTED_TOKENS", "  abc , def  ,ghi ")
        settings = self._reload_settings()
        assert settings.trusted_tokens == frozenset({"abc", "def", "ghi"})

    def test_empty_entries_dropped(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_TRUSTED_TOKENS", "abc,,def,")
        settings = self._reload_settings()
        assert settings.trusted_tokens == frozenset({"abc", "def"})

    def test_duplicates_deduplicated(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_TRUSTED_TOKENS", "abc,abc,def")
        settings = self._reload_settings()
        assert settings.trusted_tokens == frozenset({"abc", "def"})

    def test_returns_frozenset_not_list(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_TRUSTED_TOKENS", "abc")
        settings = self._reload_settings()
        assert isinstance(settings.trusted_tokens, frozenset)
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestTrustedTokensConfig -v`
Expected: All 8 tests fail with `AttributeError: 'Settings' object has no attribute 'trusted_tokens'`.

- [ ] **Step 3: Add the property to `app/config.py`**

1. Add `Field` to the existing pydantic import at the top of `app/config.py`. The current file has `from pydantic_settings import BaseSettings`. Add a sibling import:

```python
from pydantic import Field
```

2. Inside the `Settings` class, add the env-backed field next to the other field declarations (e.g. just below `extra_sources: str = ""`):

```python
    trusted_tokens_raw: str = Field(default="", validation_alias="PC2NUTS_TRUSTED_TOKENS")
```

`validation_alias` overrides the `env_prefix`-derived name, so the env var read at startup is `PC2NUTS_TRUSTED_TOKENS` (not `PC2NUTS_TRUSTED_TOKENS_RAW`). No change to `model_config` is required — `validation_alias` works without `populate_by_name`.

3. Insert the property after the existing `extra_source_urls` property:

```python
    @property
    def trusted_tokens(self) -> frozenset[str]:
        """Parse PC2NUTS_TRUSTED_TOKENS comma-separated list into a frozenset.

        Whitespace around tokens is stripped; empty entries are dropped.
        Returns an empty frozenset when unset or empty (auth bypass disabled).
        """
        raw = self.trusted_tokens_raw
        if not raw.strip():
            return frozenset()
        return frozenset(t.strip() for t in raw.split(",") if t.strip())
```

- [ ] **Step 4: Run tests — verify all 8 pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestTrustedTokensConfig -v`
Expected: 8 passed.

- [ ] **Step 5: Run full suite — verify no regression**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: All previous tests still pass plus 8 new ones.

- [ ] **Step 6: Commit**

```bash
git add app/config.py tests/test_auth.py
git commit -m "feat(auth): trusted_tokens settings property (#60)"
```

---

## Task 2: `token_id` helper

**Files:**
- Create: `app/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Append the failing test to `tests/test_auth.py`**

```python
# ── token_id helper ──────────────────────────────────────────────────────────


class TestTokenId:
    def test_deterministic(self):
        from app.auth import token_id

        assert token_id("hello") == token_id("hello")

    def test_eight_hex_chars(self):
        from app.auth import token_id

        result = token_id("hello")
        assert len(result) == 8
        assert all(c in "0123456789abcdef" for c in result)

    def test_distinct_inputs_distinct_outputs(self):
        from app.auth import token_id

        assert token_id("alpha") != token_id("beta")

    def test_known_value(self):
        """sha256('hello').hexdigest() = '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824' — first 8: '2cf24dba'."""
        from app.auth import token_id

        assert token_id("hello") == "2cf24dba"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestTokenId -v`
Expected: All 4 fail with `ModuleNotFoundError: No module named 'app.auth'`.

- [ ] **Step 3: Create `app/auth.py` with `token_id` only**

```python
"""Token-based rate-limit bypass for /lookup and /pattern.

See docs/superpowers/specs/2026-04-29-auth-token-bypass-design.md for the design.
"""

import hashlib


def token_id(token: str) -> str:
    """First 8 hex chars of sha256(token). Stable, non-reversible audit id."""
    return hashlib.sha256(token.encode()).hexdigest()[:8]
```

- [ ] **Step 4: Run tests — verify all 4 pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestTokenId -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat(auth): token_id audit helper (#60)"
```

---

## Task 3: `extract_bearer` helper

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Append the failing tests**

```python
# ── extract_bearer helper ────────────────────────────────────────────────────


class TestExtractBearer:
    def _request_with_header(self, value: str | None):
        """Build a minimal Starlette Request with the given Authorization header."""
        from starlette.requests import Request

        headers = []
        if value is not None:
            headers.append((b"authorization", value.encode()))
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "query_string": b"",
        }
        return Request(scope)

    def test_no_header_returns_none(self):
        from app.auth import extract_bearer

        request = self._request_with_header(None)
        assert extract_bearer(request) is None

    def test_bearer_returns_token(self):
        from app.auth import extract_bearer

        request = self._request_with_header("Bearer my-token")
        assert extract_bearer(request) == "my-token"

    def test_lowercase_scheme_accepted(self):
        from app.auth import extract_bearer

        request = self._request_with_header("bearer my-token")
        assert extract_bearer(request) == "my-token"

    def test_basic_scheme_raises_400(self):
        from fastapi import HTTPException

        from app.auth import extract_bearer

        request = self._request_with_header("Basic dXNlcjpwYXNz")
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer(request)
        assert exc_info.value.status_code == 400

    def test_bearer_with_no_value_raises_400(self):
        from fastapi import HTTPException

        from app.auth import extract_bearer

        request = self._request_with_header("Bearer ")
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer(request)
        assert exc_info.value.status_code == 400

    def test_bearer_with_extra_words_raises_400(self):
        from fastapi import HTTPException

        from app.auth import extract_bearer

        request = self._request_with_header("Bearer foo bar")
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer(request)
        assert exc_info.value.status_code == 400

    def test_just_bearer_raises_400(self):
        from fastapi import HTTPException

        from app.auth import extract_bearer

        request = self._request_with_header("Bearer")
        with pytest.raises(HTTPException) as exc_info:
            extract_bearer(request)
        assert exc_info.value.status_code == 400
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestExtractBearer -v`
Expected: All 7 fail with `ImportError: cannot import name 'extract_bearer' from 'app.auth'`.

- [ ] **Step 3: Add `extract_bearer` to `app/auth.py`**

Append at end of file:

```python
from fastapi import HTTPException
from starlette.requests import Request


def extract_bearer(request: Request) -> str | None:
    """Return the bearer token from the Authorization header, or None if absent.

    Raises HTTPException(400) when the header is present but malformed:
    wrong scheme, missing value, or extra whitespace-separated tokens.
    """
    header = request.headers.get("authorization")
    if header is None:
        return None
    parts = header.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise HTTPException(status_code=400, detail="malformed Authorization header")
    return parts[1]
```

- [ ] **Step 4: Run tests — verify all 7 pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestExtractBearer -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat(auth): extract_bearer header parser (#60)"
```

---

## Task 4: `is_trusted` helper (timing-safe)

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Append the failing tests**

```python
# ── is_trusted helper ────────────────────────────────────────────────────────


class TestIsTrusted:
    @pytest.fixture(autouse=True)
    def _empty_tokens(self, monkeypatch):
        # Stub settings.trusted_tokens for each test
        from app import auth, config

        monkeypatch.setattr(config.settings, "__dict__", config.settings.__dict__.copy())
        # Note: trusted_tokens is a property; stub via a new Settings-ish object
        # by patching the module-level reference auth uses.
        self._monkeypatch = monkeypatch
        self._auth = auth

    def _set_tokens(self, *tokens):
        # Patch auth's view of settings.trusted_tokens
        self._monkeypatch.setattr(
            self._auth, "_get_trusted_tokens", lambda: frozenset(tokens)
        )

    def test_match_against_single_token(self):
        self._set_tokens("abc")
        assert self._auth.is_trusted("abc") is True

    def test_match_against_one_of_many(self):
        self._set_tokens("abc", "def", "ghi")
        assert self._auth.is_trusted("def") is True

    def test_unknown_token_returns_false(self):
        self._set_tokens("abc", "def")
        assert self._auth.is_trusted("xyz") is False

    def test_empty_string_returns_false(self):
        self._set_tokens("abc")
        assert self._auth.is_trusted("") is False

    def test_empty_token_set_returns_false(self):
        self._set_tokens()
        assert self._auth.is_trusted("anything") is False
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestIsTrusted -v`
Expected: All 5 fail (`is_trusted` and `_get_trusted_tokens` not defined yet).

- [ ] **Step 3: Add to `app/auth.py`**

Append after `extract_bearer`:

```python
import hmac

from app.config import settings


def _get_trusted_tokens() -> frozenset[str]:
    """Indirection seam for tests; returns settings.trusted_tokens at call time."""
    return settings.trusted_tokens


def is_trusted(candidate: str) -> bool:
    """Constant-time membership test against the configured trusted tokens.

    Uses hmac.compare_digest in a loop. Returns False for empty input or when
    no tokens are configured.
    """
    if not candidate:
        return False
    return any(hmac.compare_digest(candidate, t) for t in _get_trusted_tokens())
```

- [ ] **Step 4: Run tests — verify all 5 pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestIsTrusted -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat(auth): is_trusted timing-safe membership test (#60)"
```

---

## Task 5: Auth middleware (short-circuits 400/401, marks request.state)

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Append the failing tests**

```python
# ── AuthMiddleware ───────────────────────────────────────────────────────────


class TestAuthMiddleware:
    def _build_app(self, *trusted: str):
        """Build a minimal Starlette app with AuthMiddleware mounted."""
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse
        from starlette.routing import Route

        from app import auth

        async def endpoint(request):
            return JSONResponse(
                {
                    "trusted": getattr(request.state, "trusted", False),
                    "token_id": getattr(request.state, "token_id", None),
                }
            )

        app = Starlette(routes=[Route("/x", endpoint)])
        app.add_middleware(auth.AuthMiddleware)
        # Patch the trusted-token getter for the duration of this app
        auth._get_trusted_tokens = lambda: frozenset(trusted)
        return app

    def test_no_header_marks_untrusted(self):
        from starlette.testclient import TestClient

        app = self._build_app("good-token")
        with TestClient(app) as client:
            resp = client.get("/x")
        assert resp.status_code == 200
        assert resp.json() == {"trusted": False, "token_id": None}

    def test_valid_token_marks_trusted_with_token_id(self):
        from starlette.testclient import TestClient

        from app.auth import token_id

        app = self._build_app("good-token")
        with TestClient(app) as client:
            resp = client.get("/x", headers={"Authorization": "Bearer good-token"})
        assert resp.status_code == 200
        assert resp.json() == {"trusted": True, "token_id": token_id("good-token")}

    def test_invalid_token_returns_401(self):
        from starlette.testclient import TestClient

        app = self._build_app("good-token")
        with TestClient(app) as client:
            resp = client.get("/x", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401
        assert "invalid token" in resp.json()["detail"].lower()

    def test_malformed_header_returns_400(self):
        from starlette.testclient import TestClient

        app = self._build_app("good-token")
        with TestClient(app) as client:
            resp = client.get("/x", headers={"Authorization": "Basic abc"})
        assert resp.status_code == 400
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestAuthMiddleware -v`
Expected: All 4 fail (`AttributeError: module 'app.auth' has no attribute 'AuthMiddleware'`).

- [ ] **Step 3: Add `AuthMiddleware` to `app/auth.py`**

Append:

```python
import contextvars

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


_request_var: contextvars.ContextVar[Request | None] = contextvars.ContextVar(
    "pc2nuts_request", default=None
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate Authorization header up-front.

    - No header → request.state.trusted = False, normal flow.
    - Valid bearer token → request.state.trusted = True, request.state.token_id set.
    - Invalid token → 401 short-circuit (no rate-limit consumed).
    - Malformed header → 400 short-circuit.
    - /health is exempt entirely — header is ignored, request flows through.
      This protects monitoring tooling from false 401s if a service mesh adds
      an Authorization header globally.

    Also stores the Request in a ContextVar so the parameterless slowapi
    exempt_when callable can read it (slowapi calls exempt_when()).
    """

    EXEMPT_PATHS = frozenset({"/health"})

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.EXEMPT_PATHS:
            request.state.trusted = False
            request.state.token_id = None
            return await call_next(request)

        try:
            token = extract_bearer(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

        if token is not None:
            if not is_trusted(token):
                return JSONResponse(
                    {"detail": "invalid token"}, status_code=401
                )
            request.state.trusted = True
            request.state.token_id = token_id(token)
        else:
            request.state.trusted = False
            request.state.token_id = None

        ctx_token = _request_var.set(request)
        try:
            return await call_next(request)
        finally:
            _request_var.reset(ctx_token)
```

- [ ] **Step 4: Run tests — verify all 4 pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestAuthMiddleware -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat(auth): AuthMiddleware (400/401 short-circuit + state) (#60)"
```

---

## Task 6: `is_trusted_request` slowapi predicate

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Append the failing tests**

```python
# ── is_trusted_request predicate ─────────────────────────────────────────────


class TestIsTrustedRequest:
    def _request_with_state(self, *, trusted: bool):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "state": {},
        }
        request = Request(scope)
        request.state.trusted = trusted
        return request

    def test_no_request_in_context_returns_false(self):
        from app.auth import _request_var, is_trusted_request

        # Ensure no request set
        assert _request_var.get() is None
        assert is_trusted_request() is False

    def test_trusted_state_returns_true(self):
        from app.auth import _request_var, is_trusted_request

        request = self._request_with_state(trusted=True)
        token = _request_var.set(request)
        try:
            assert is_trusted_request() is True
        finally:
            _request_var.reset(token)

    def test_untrusted_state_returns_false(self):
        from app.auth import _request_var, is_trusted_request

        request = self._request_with_state(trusted=False)
        token = _request_var.set(request)
        try:
            assert is_trusted_request() is False
        finally:
            _request_var.reset(token)
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestIsTrustedRequest -v`
Expected: All 3 fail (`is_trusted_request` not defined).

- [ ] **Step 3: Add the predicate to `app/auth.py`**

Append:

```python
def is_trusted_request() -> bool:
    """Parameterless predicate for slowapi's exempt_when.

    Reads the current Request from the ContextVar set by AuthMiddleware and
    returns True iff request.state.trusted is True. Returns False outside
    a request context (defensive default).
    """
    request = _request_var.get()
    if request is None:
        return False
    return bool(getattr(request.state, "trusted", False))
```

- [ ] **Step 4: Run tests — verify all 3 pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestIsTrustedRequest -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat(auth): is_trusted_request slowapi exempt_when predicate (#60)"
```

---

## Task 7: Wire auth into FastAPI app

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`, `tests/conftest.py`

- [ ] **Step 1: Add a `trusted_client` fixture to `tests/conftest.py`**

Append after the existing `client` fixture:

```python
@pytest.fixture()
def trusted_client(mock_data, monkeypatch):
    """TestClient with one configured trusted token: 'test-token-aaa'."""
    from unittest.mock import patch

    from app import auth, data_loader

    monkeypatch.setattr(auth, "_get_trusted_tokens", lambda: frozenset({"test-token-aaa"}))

    from fastapi.testclient import TestClient

    with patch.object(data_loader, "load_data"):
        from app.main import app

        with TestClient(app) as tc:
            yield tc
```

- [ ] **Step 2: Append failing endpoint tests to `tests/test_api.py`**

Append at end of file:

```python
# ── Auth-token bypass tests (#60) ────────────────────────────────────────────


class TestAuthBypass:
    def test_no_header_normal_flow(self, trusted_client):
        """Without an Authorization header, behaviour is unchanged."""
        resp = trusted_client.get(
            "/lookup", params={"postal_code": "10115", "country": "DE"}
        )
        assert resp.status_code == 200

    def test_valid_token_returns_200(self, trusted_client):
        resp = trusted_client.get(
            "/lookup",
            params={"postal_code": "10115", "country": "DE"},
            headers={"Authorization": "Bearer test-token-aaa"},
        )
        assert resp.status_code == 200

    def test_invalid_token_returns_401(self, trusted_client):
        resp = trusted_client.get(
            "/lookup",
            params={"postal_code": "10115", "country": "DE"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401
        assert "invalid token" in resp.json()["detail"].lower()

    def test_malformed_header_returns_400(self, trusted_client):
        resp = trusted_client.get(
            "/lookup",
            params={"postal_code": "10115", "country": "DE"},
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 400

    def test_health_anonymous_works(self, trusted_client):
        resp = trusted_client.get("/health")
        assert resp.status_code == 200

    def test_health_ignores_invalid_token(self, trusted_client):
        """/health is in AuthMiddleware.EXEMPT_PATHS — header is ignored entirely.
        Protects monitoring tools that may inject auth headers globally."""
        resp = trusted_client.get(
            "/health", headers={"Authorization": "Bearer wrong-token"}
        )
        assert resp.status_code == 200

    def test_health_ignores_malformed_header(self, trusted_client):
        resp = trusted_client.get(
            "/health", headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        assert resp.status_code == 200
```

- [ ] **Step 3: Run the new tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_api.py::TestAuthBypass -v`
Expected: All 6 fail (auth not yet wired into app — no AuthMiddleware mounted).

- [ ] **Step 4: Wire auth into `app/main.py`**

In `app/main.py`:

1. Add import at top with other app imports:

```python
from app.auth import AuthMiddleware, is_trusted_request
```

2. Add the middleware after the existing `AccessLogMiddleware` registration. Order matters — AuthMiddleware must run **before** the slowapi limiter (i.e. it must be added last so it's the outermost wrapper, since Starlette runs middlewares in reverse-add order):

Replace:

```python
app.add_middleware(AccessLogMiddleware)
```

with:

```python
app.add_middleware(AccessLogMiddleware)
app.add_middleware(AuthMiddleware)
```

3. Pass `exempt_when` to both `@limiter.limit(...)` decorators. Replace:

```python
@limiter.limit(settings.rate_limit)
def lookup_postal_code(
```

with:

```python
@limiter.limit(settings.rate_limit, exempt_when=is_trusted_request)
def lookup_postal_code(
```

And replace:

```python
@limiter.limit(settings.rate_limit)
def get_pattern(
```

with:

```python
@limiter.limit(settings.rate_limit, exempt_when=is_trusted_request)
def get_pattern(
```

- [ ] **Step 5: Run new tests — verify all 6 pass**

Run: `.venv/bin/python -m pytest tests/test_api.py::TestAuthBypass -v`
Expected: 6 passed.

- [ ] **Step 6: Run full suite — verify no regressions**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all passing (existing 78 + new 27 = 105 total approximate; exact count depends on shared utilities).

- [ ] **Step 7: Commit**

```bash
git add app/main.py tests/conftest.py tests/test_api.py
git commit -m "feat(auth): wire AuthMiddleware + exempt_when on /lookup and /pattern (#60)"
```

---

## Task 8: Audit log extension

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Append the failing log-format test**

```python
    def test_audit_log_includes_token_id_for_trusted(self, trusted_client, caplog):
        from app.auth import token_id

        with caplog.at_level("INFO", logger="app.access"):
            trusted_client.get(
                "/lookup",
                params={"postal_code": "10115", "country": "DE"},
                headers={"Authorization": "Bearer test-token-aaa"},
            )
        log_text = " ".join(r.message for r in caplog.records if r.name == "app.access")
        assert f"token_id={token_id('test-token-aaa')}" in log_text

    def test_audit_log_omits_token_id_for_anonymous(self, trusted_client, caplog):
        with caplog.at_level("INFO", logger="app.access"):
            trusted_client.get(
                "/lookup", params={"postal_code": "10115", "country": "DE"}
            )
        log_text = " ".join(r.message for r in caplog.records if r.name == "app.access")
        assert "token_id=" not in log_text
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_api.py::TestAuthBypass::test_audit_log_includes_token_id_for_trusted tests/test_api.py::TestAuthBypass::test_audit_log_omits_token_id_for_anonymous -v`
Expected: First fails (missing `token_id=` in log), second passes incidentally.

- [ ] **Step 3: Extend `AccessLogMiddleware` in `app/main.py`**

Replace the existing `AccessLogMiddleware.dispatch` body (around lines 64-76):

```python
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        token_suffix = ""
        tid = getattr(request.state, "token_id", None)
        if tid:
            token_suffix = f" token_id={tid}"
        access_logger.info(
            "%s %s %s %d %.1fms%s",
            request.client.host if request.client else "-",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            token_suffix,
        )
        return response
```

Note: `AuthMiddleware` runs *after* `AccessLogMiddleware` (later add → outer), so the access logger runs *inside* the auth scope and `request.state.token_id` is available. If the order ends up reversed in practice (manifested by the test failing), swap the `add_middleware` order in `main.py` so `AuthMiddleware` is added before `AccessLogMiddleware`.

- [ ] **Step 4: Run new tests — verify both pass**

Run: `.venv/bin/python -m pytest tests/test_api.py::TestAuthBypass -v`
Expected: 8 passed (the original 6 + 2 new log assertions).

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat(auth): audit log token_id field for trusted requests (#60)"
```

---

## Task 9: README documentation

**Files:**
- Modify: `README.md`

No tests for this task (documentation-only).

- [ ] **Step 1: Add the new section to `README.md`**

Locate the `## Configuration` section (around line 303). Insert a new section **after** the Configuration table and **before** `## Five-tier lookup`. Find this anchor line:

```
## Five-tier lookup
```

Insert immediately above it:

````markdown
## Authentication & rate-limit bypass

The service applies a per-IP rate limit (`60/minute` by default) to `/lookup` and `/pattern`. Trusted callers — operator-issued, manually distributed — can bypass this limit by presenting an `Authorization: Bearer <token>` header. `/health` stays anonymous.

### Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PC2NUTS_TRUSTED_TOKENS` | `""` (unset) | Comma-separated list of valid bypass tokens. Empty → bypass disabled (current default). Whitespace and empty entries between commas are tolerated. |

### Operator runbook — issue a token

```bash
# 1. Generate locally (48 hex chars / 192 bits)
openssl rand -hex 24

# 2. Add the printed token to PC2NUTS_TRUSTED_TOKENS in the production
#    deployment's environment configuration. Multiple tokens are comma-separated.
#    Example value with two active tokens:
#       PC2NUTS_TRUSTED_TOKENS=9e7a3f...d2,4b1c8e...77

# 3. Restart the service container to load the new env value.
#    The SQLite postal-code cache survives the restart, so cold-start is ~30 s.

# 4. Verify the new token bypasses the rate limit:
curl -i -H "Authorization: Bearer <new_token>" \
     "https://<service-host>/lookup?country=DE&postal_code=10115"
# → 200, audit log shows token_id=<first 8 hex of sha256(token)>

# 5. Hand the raw token to the consumer over a confidential channel
#    (1Password, Signal, encrypted email — not Slack, not GitHub issues).
```

### Operator runbook — revoke a token

```bash
# 1. Remove the token entry from PC2NUTS_TRUSTED_TOKENS in the env config.
# 2. Restart the container.
# 3. Verify the revoked token is rejected:
curl -i -H "Authorization: Bearer <revoked_token>" \
     "https://<service-host>/lookup?country=DE&postal_code=10115"
# → 401 Unauthorized
```

### Operator runbook — find the token id of a logged request

```bash
echo -n "<token>" | sha256sum | cut -c1-8
```

### Behaviour summary

| Request | Result |
|---|---|
| No `Authorization` header | Per-IP `60/minute` cap, normal `200` / `429` |
| `Authorization: Bearer <valid_token>` | Rate limit fully bypassed; `token_id=<8hex>` appended to access log |
| `Authorization: Bearer <unknown_token>` | `401 Unauthorized` |
| `Authorization: <not Bearer>` or malformed | `400 Bad Request` |

### Disable the bypass entirely

Unset or empty `PC2NUTS_TRUSTED_TOKENS`. All traffic falls back to the per-IP cap. No code change needed.

### Security notes

- Tokens are **bearer credentials** — anyone holding the string can use the API at full rate. Treat them like passwords.
- Always send tokens over HTTPS. Never accept a bearer token over plain HTTP.
- Log lines contain only the 8-char SHA-256 prefix. Token values never appear in logs.
- Token comparison is constant-time (`hmac.compare_digest`).
- Revocation latency is bounded by container restart time (~30 s).

````

- [ ] **Step 2: Add the env var to the existing Configuration table**

Locate the configuration table (starts around line 305 after `All settings are overridable via environment variables prefixed with `PC2NUTS_`:`). Add a new row at an appropriate alphabetical position:

```markdown
| `PC2NUTS_TRUSTED_TOKENS` | `""` (empty — bypass disabled) | Comma-separated list of opaque tokens that bypass the per-IP rate limit when sent via `Authorization: Bearer <token>`. See [Authentication & rate-limit bypass](#authentication--rate-limit-bypass) for the operator runbook. |
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: auth-token bypass operator runbook (#60)"
```

---

## Task 10: CHANGELOG entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add new `[Unreleased]` section to `CHANGELOG.md`**

If there is no `[Unreleased]` section above the current top entry (currently `[0.15.0] - 2026-04-29`), add one. Replace:

```markdown
The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [0.15.0] - 2026-04-29
```

with:

```markdown
The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- **Auth-token bypass** (#60): trusted callers can bypass the per-IP rate limit by presenting `Authorization: Bearer <token>`. Tokens are managed via the new `PC2NUTS_TRUSTED_TOKENS` comma-separated env var. Invalid tokens return `401`; malformed `Authorization` headers return `400`. Audit lines log a non-reversible 8-char SHA-256 prefix only — token values never appear in logs. See README "Authentication & rate-limit bypass" for the operator runbook.

## [0.15.0] - 2026-04-29
```

- [ ] **Step 2: Final check — full suite + lint + format**

Run:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check app/ scripts/
.venv/bin/ruff format --check app/ scripts/
```

Expected: all pass / clean.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for auth-token bypass (#60)"
```

---

## Task 11: Push, close issue, release

**Files:** none (git/gh operations).

- [ ] **Step 1: Push to origin**

```bash
git push origin main
```

- [ ] **Step 2: Close #60 with a summary comment**

```bash
gh issue close 60 -c "Implemented in $(git log --grep '#60' --format=%h | head -1) (and following commits).

Behaviour:
- Authorization: Bearer <valid> → bypass rate limit, audit log with token_id
- Authorization: Bearer <invalid> → 401
- Authorization: <malformed> → 400
- No header → existing per-IP cap unchanged

Operator runbook in README → Authentication & rate-limit bypass."
```

- [ ] **Step 3: Tag and release**

After confirming CI is green on main:

```bash
gh release create v0.16.0 \
  --title "v0.16.0 — Auth-token bypass" \
  --notes "$(cat <<'EOF'
### What's new

- **Auth-token bypass** (#60): trusted callers can bypass the per-IP rate limit by presenting \`Authorization: Bearer <token>\`. Tokens are managed via the new \`PC2NUTS_TRUSTED_TOKENS\` comma-separated env var; restart applies changes. Invalid tokens return \`401\`; malformed \`Authorization\` headers return \`400\`. Audit lines log a non-reversible 8-char SHA-256 prefix; token values never appear in logs.

See [README → Authentication & rate-limit bypass](https://github.com/bk86a/PostalCode2NUTS/blob/main/README.md#authentication--rate-limit-bypass) for the operator runbook (issue / verify / revoke / disable).

Closes #60.
EOF
)"
```

- [ ] **Step 4: Move CHANGELOG `[Unreleased]` to `[0.16.0] - 2026-04-29`**

```bash
sed -i 's/^## \[Unreleased\]$/## [0.16.0] - 2026-04-29/' CHANGELOG.md
git add CHANGELOG.md
git commit -m "chore: cut v0.16.0"
git push origin main
```
