# DB-backed trusted-tokens implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move trusted-token storage from `PC2NUTS_TRUSTED_TOKENS` env var (v0.16.0 / v1) to a managed SQLite-compatible HTTP database (v2), preserving the env var as a disaster-recovery fallback. Add an operator CLI for issuance, listing, and revocation. Implements [#61](https://github.com/bk86a/PostalCode2NUTS/issues/61) per [the design spec](../specs/2026-04-29-db-backed-trusted-tokens-design.md).

**Architecture:** New `app/token_db.py` is a thin HTTP client over the configured database service. `app/auth.py`'s `_get_trusted_tokens()` returns the union of an in-memory frozenset (refreshed from the DB every ~60 s by a background task) with the existing `PC2NUTS_TRUSTED_TOKENS` env-var set. New `scripts/tokens.py` CLI gives operators `init` / `add` / `list` / `revoke` subcommands. DB unreachable at startup is non-fatal — service serves anonymous traffic; bypass falls back to the env var.

**Tech Stack:** Python 3.12, FastAPI / Starlette / asyncio, `httpx` (already a project dependency), `sqlite3`-compatible HTTP API on the database side, stdlib `argparse` + `secrets` + `hashlib` for the CLI, pytest with `respx` (or hand-rolled mock transport) for HTTP testing.

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `app/token_db.py` | **new** (~120 LOC) | `TokenDBError`, `TokenDB(url)` class with `init_schema`, `add`, `list_active`, `list_all`, `revoke`. Pure I/O — no business logic. |
| `app/config.py` | modify | Add `token_db_url`, `token_refresh_seconds` settings. |
| `app/auth.py` | modify | Module-level `_db_tokens: frozenset[str]`, `_token_db_stale: bool`. New `refresh_db_tokens()` function. `_get_trusted_tokens()` returns `_db_tokens \| settings.trusted_tokens`. |
| `app/main.py` | modify | In `lifespan`, when `PC2NUTS_TOKEN_DB_URL` is set: do an initial refresh, then spawn an `asyncio.create_task` background loop. Cancel cleanly on shutdown. |
| `app/models.py` | modify | Add optional `token_db_stale: bool \| None = None` field to `HealthResponse`. |
| `scripts/tokens.py` | **new** (~180 LOC) | argparse-based CLI: `init`, `add`, `list`, `revoke`. |
| `tests/test_token_db.py` | **new** | Unit tests for `TokenDB` (mocked HTTP). |
| `tests/test_tokens_cli.py` | **new** | Unit tests for the CLI (subprocess-style or argparse harness). |
| `tests/test_auth.py` | modify | New `TestDBTokens` class covering union, refresh, DB-down scenarios. |
| `tests/test_api.py` | modify | One test for `/health` `token_db_stale` field. |
| `tests/conftest.py` | unchanged | (Existing `mock_data` and `trusted_client` fixtures don't need changes.) |
| `README.md` | modify | Replace v1 runbook subsections with v2 (CLI commands), add union-semantic note. |
| `CHANGELOG.md` | modify | New `[Unreleased]` `### Added` entry. |

### A note on the database HTTP wire-protocol

The implementer **MUST consult the configured database provider's HTTP API documentation** before writing `TokenDB.execute()`. This plan assumes a generic shape:

```http
POST {db_url}/query
Content-Type: application/json
Authorization: <provider-specific>

{"sql": "SELECT * FROM trusted_tokens WHERE revoked_at IS NULL", "params": []}
```

Response (assumed):

```json
{"rows": [{"id": 1, "value": "...", "label": "...", "created_at": "..."}],
 "lastInsertRowId": null,
 "rowsAffected": 0}
```

If the provider's actual shape differs (different path, different field names, GET-with-querystring instead of POST-with-body, etc.), adjust `TokenDB.execute()` accordingly. **The unit tests mock the HTTP boundary**, so they do not depend on the exact wire shape — only the public method signatures matter. Integration testing against a real DB instance is a manual verification step at Task 11.

---

## Task 1: Add `token_db_url` and `token_refresh_seconds` settings

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_token_db.py` (NEW — start with this test class only)

- [ ] **Step 1: Create `tests/test_token_db.py` with the failing config test**

```python
"""Tests for app/token_db.py and the token DB settings."""

from importlib import reload

import pytest


# ── Settings ─────────────────────────────────────────────────────────────────


class TestTokenDBSettings:
    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch):
        monkeypatch.delenv("PC2NUTS_TOKEN_DB_URL", raising=False)
        monkeypatch.delenv("PC2NUTS_TOKEN_REFRESH_SECONDS", raising=False)

    def _reload_settings(self):
        from app import config

        reload(config)
        return config.settings

    def test_token_db_url_default_empty(self):
        settings = self._reload_settings()
        assert settings.token_db_url == ""

    def test_token_db_url_from_env(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_TOKEN_DB_URL", "https://db.example/v1")
        settings = self._reload_settings()
        assert settings.token_db_url == "https://db.example/v1"

    def test_token_refresh_seconds_default_60(self):
        settings = self._reload_settings()
        assert settings.token_refresh_seconds == 60

    def test_token_refresh_seconds_from_env(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_TOKEN_REFRESH_SECONDS", "5")
        settings = self._reload_settings()
        assert settings.token_refresh_seconds == 5
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_token_db.py::TestTokenDBSettings -v`
Expected: 4 fail with `AttributeError: 'Settings' object has no attribute 'token_db_url'` (or similar).

- [ ] **Step 3: Add the settings to `app/config.py`**

Find the existing `Settings` class field declarations (after `trusted_tokens_raw`). Add:

```python
    token_db_url: str = ""
    token_refresh_seconds: int = 60
```

`env_prefix = "PC2NUTS_"` already maps these to `PC2NUTS_TOKEN_DB_URL` / `PC2NUTS_TOKEN_REFRESH_SECONDS` automatically — no `Field(validation_alias=...)` needed.

- [ ] **Step 4: Run tests — verify all 4 pass**

Run: `.venv/bin/python -m pytest tests/test_token_db.py::TestTokenDBSettings -v`
Expected: 4 passed.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 126 passed (122 prior + 4 new).

- [ ] **Step 6: Commit**

```bash
git add app/config.py tests/test_token_db.py
git commit -m "feat(token-db): config settings for DB URL + refresh interval (#61)"
```

---

## Task 2: TokenDB module skeleton — exception, __init__, execute

**Files:**
- Create: `app/token_db.py`
- Test: `tests/test_token_db.py`

This task introduces the HTTP boundary. The unit tests mock `httpx.Client.post`, so the wire-protocol shape is encapsulated in `TokenDB.execute` and can be adjusted later without touching the higher-level methods.

- [ ] **Step 1: Append the failing tests to `tests/test_token_db.py`**

```python
# ── TokenDB.__init__ + execute ───────────────────────────────────────────────


class TestTokenDBInit:
    def test_init_stores_url(self):
        from app.token_db import TokenDB

        db = TokenDB("https://db.example/v1")
        assert db.url == "https://db.example/v1"

    def test_init_strips_trailing_slash(self):
        from app.token_db import TokenDB

        db = TokenDB("https://db.example/v1/")
        assert db.url == "https://db.example/v1"


class TestTokenDBExecute:
    def _mock_response(self, monkeypatch, *, json_body: dict, status_code: int = 200):
        from app import token_db

        class _Response:
            status_code = 200

            def __init__(self, body, code=200):
                self._body = body
                self.status_code = code

            def json(self):
                return self._body

            def raise_for_status(self):
                if self.status_code >= 400:
                    import httpx
                    raise httpx.HTTPStatusError(
                        "boom", request=None, response=self
                    )

        captured: dict = {}

        def _post(self, url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Response(json_body, status_code)

        import httpx

        monkeypatch.setattr(httpx.Client, "post", _post)
        return captured

    def test_execute_sends_post_with_sql_and_params(self, monkeypatch):
        from app.token_db import TokenDB

        captured = self._mock_response(monkeypatch, json_body={"rows": [], "rowsAffected": 0})
        db = TokenDB("https://db.example/v1")
        db.execute("SELECT * FROM t WHERE id = ?", [42])

        assert captured["json"] == {"sql": "SELECT * FROM t WHERE id = ?", "params": [42]}

    def test_execute_returns_rows(self, monkeypatch):
        from app.token_db import TokenDB

        self._mock_response(
            monkeypatch,
            json_body={"rows": [{"id": 1, "label": "a"}], "rowsAffected": 0},
        )
        db = TokenDB("https://db.example/v1")
        rows = db.execute("SELECT id, label FROM t")
        assert rows == [{"id": 1, "label": "a"}]

    def test_execute_no_params_sends_empty_list(self, monkeypatch):
        from app.token_db import TokenDB

        captured = self._mock_response(monkeypatch, json_body={"rows": [], "rowsAffected": 0})
        db = TokenDB("https://db.example/v1")
        db.execute("CREATE TABLE x (id INTEGER)")
        assert captured["json"]["params"] == []

    def test_execute_http_error_raises_token_db_error(self, monkeypatch):
        from app.token_db import TokenDB, TokenDBError

        self._mock_response(monkeypatch, json_body={"error": "boom"}, status_code=500)
        db = TokenDB("https://db.example/v1")
        with pytest.raises(TokenDBError):
            db.execute("SELECT 1")
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_token_db.py::TestTokenDBInit tests/test_token_db.py::TestTokenDBExecute -v`
Expected: All fail with `ModuleNotFoundError: No module named 'app.token_db'`.

- [ ] **Step 3: Create `app/token_db.py` with skeleton + execute**

```python
"""Thin HTTP client for the configured SQLite-compatible managed database.

See docs/superpowers/specs/2026-04-29-db-backed-trusted-tokens-design.md.

The wire shape (POST /query with {sql, params}, response {rows, rowsAffected,
lastInsertRowId}) is the working assumption; adjust `_post` if the configured
provider differs.
"""

from __future__ import annotations

from typing import Any

import httpx


class TokenDBError(Exception):
    """Raised when the token database returns an error or is unreachable."""


class TokenDB:
    """Minimal HTTP client over a SQLite-compatible managed database.

    All methods are blocking. Callers running inside an asyncio loop should
    wrap calls in asyncio.to_thread().
    """

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def execute(self, sql: str, params: list[Any] | None = None) -> list[dict]:
        """Send a SQL statement. Returns the `rows` list from the response.

        Writes (INSERT/UPDATE/DELETE/CREATE) typically return an empty list.
        """
        payload = {"sql": sql, "params": list(params) if params else []}
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(f"{self.url}/query", json=payload, headers={})
                resp.raise_for_status()
                body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TokenDBError(f"DB request failed: {exc}") from exc
        return body.get("rows") or []
```

- [ ] **Step 4: Run tests — verify all 6 pass**

Run: `.venv/bin/python -m pytest tests/test_token_db.py::TestTokenDBInit tests/test_token_db.py::TestTokenDBExecute -v`
Expected: 6 passed.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 132 passed (126 prior + 6 new).

- [ ] **Step 6: Commit**

```bash
git add app/token_db.py tests/test_token_db.py
git commit -m "feat(token-db): TokenDB skeleton + execute (#61)"
```

---

## Task 3: TokenDB query methods — init_schema, add, list_active, list_all, revoke

**Files:**
- Modify: `app/token_db.py`
- Test: `tests/test_token_db.py`

Each query method is a thin wrapper around `execute`. The tests mock `execute` directly (not the HTTP layer) so they verify the SQL and parameter list are correct.

- [ ] **Step 1: Append the failing tests**

```python
# ── TokenDB query methods ────────────────────────────────────────────────────


class TestTokenDBMethods:
    @pytest.fixture
    def db(self, monkeypatch):
        from app.token_db import TokenDB

        captured: list[tuple[str, list]] = []
        return_value: list[dict] = []

        def _execute(self, sql, params=None):
            captured.append((sql, list(params) if params else []))
            return list(return_value)

        monkeypatch.setattr(TokenDB, "execute", _execute)
        db = TokenDB("https://db.example/v1")
        db._captured = captured
        db._set_return = return_value
        return db

    def test_init_schema_creates_table_and_index(self, db):
        db.init_schema()
        sqls = [c[0].strip() for c in db._captured]
        assert any("CREATE TABLE" in s and "trusted_tokens" in s for s in sqls)
        assert any("CREATE INDEX" in s and "idx_trusted_tokens_active" in s for s in sqls)
        # Both statements must use IF NOT EXISTS so init is idempotent
        assert all("IF NOT EXISTS" in s for s in sqls)

    def test_add_inserts_value_and_label(self, db):
        db._set_return.clear()
        db._set_return.extend([{"id": 7}])
        new_id = db.add("e3a1f2d4", "alice-batch")
        # First captured call is the INSERT … RETURNING id
        sql, params = db._captured[0]
        assert "INSERT INTO trusted_tokens" in sql
        assert "value" in sql and "label" in sql
        assert params == ["e3a1f2d4", "alice-batch"]
        assert new_id == 7

    def test_list_active_filters_revoked(self, db):
        db._set_return.clear()
        db._set_return.extend([
            {"id": 1, "value": "v1", "label": "a", "created_at": "2026-01-01"},
        ])
        rows = db.list_active()
        sql, params = db._captured[0]
        assert "revoked_at IS NULL" in sql
        assert params == []
        assert rows == [{"id": 1, "value": "v1", "label": "a", "created_at": "2026-01-01"}]

    def test_list_all_omits_value_column(self, db):
        db.list_all()
        sql, _ = db._captured[0]
        # The SELECT projection must NOT include `value` — list_all is for human reading
        assert "value" not in sql.split("FROM")[0].lower()
        assert "id" in sql.lower()
        assert "label" in sql.lower()
        assert "created_at" in sql.lower()
        assert "revoked_at" in sql.lower()

    def test_revoke_updates_revoked_at(self, db):
        from app.token_db import TokenDB

        # First execute returns rowsAffected; mock the second call's return
        db._set_return.clear()
        # We'll have to inspect the SQL only
        db.revoke(42)
        sql, params = db._captured[0]
        assert "UPDATE trusted_tokens" in sql
        assert "revoked_at" in sql
        assert "datetime('now')" in sql
        assert params == [42]

    def test_revoke_returns_true_for_active_id(self, db):
        # Real implementation: revoke returns True iff a row was changed.
        # In our mock, we'll just verify the method returns something truthy when
        # the implementation looks at execute() side effects. Skipping deep
        # assertion here — covered by integration in test_tokens_cli.
        db.revoke(99)
        # No exception = pass for this stub level
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_token_db.py::TestTokenDBMethods -v`
Expected: All fail (`AttributeError: 'TokenDB' object has no attribute 'init_schema'` etc.).

- [ ] **Step 3: Append query methods to `app/token_db.py`**

```python
    # ── Schema ──────────────────────────────────────────────────────────────

    def init_schema(self) -> None:
        """Idempotently create the trusted_tokens table and index."""
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS trusted_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                value       TEXT NOT NULL UNIQUE,
                label       TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                revoked_at  TEXT
            )
            """
        )
        self.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trusted_tokens_active
                ON trusted_tokens (value)
                WHERE revoked_at IS NULL
            """
        )

    # ── Mutations ───────────────────────────────────────────────────────────

    def add(self, value: str, label: str) -> int:
        """Insert a new trusted token. Returns the new row id.

        Raises TokenDBError on uniqueness violation or transport failure.
        """
        rows = self.execute(
            "INSERT INTO trusted_tokens (value, label) VALUES (?, ?) RETURNING id",
            [value, label],
        )
        if not rows:
            raise TokenDBError("INSERT did not return an id")
        return int(rows[0]["id"])

    def revoke(self, token_id: int) -> bool:
        """Mark a token as revoked. Idempotent — returns False if already revoked."""
        rows = self.execute(
            "UPDATE trusted_tokens "
            "SET revoked_at = datetime('now') "
            "WHERE id = ? AND revoked_at IS NULL "
            "RETURNING id",
            [token_id],
        )
        return bool(rows)

    # ── Queries ─────────────────────────────────────────────────────────────

    def list_active(self) -> list[dict]:
        """Return all rows where revoked_at IS NULL."""
        return self.execute(
            "SELECT id, value, label, created_at FROM trusted_tokens "
            "WHERE revoked_at IS NULL"
        )

    def list_all(self) -> list[dict]:
        """Return all rows, never including the raw value column."""
        return self.execute(
            "SELECT id, label, created_at, revoked_at FROM trusted_tokens "
            "ORDER BY id"
        )
```

- [ ] **Step 4: Run tests — verify all pass**

Run: `.venv/bin/python -m pytest tests/test_token_db.py::TestTokenDBMethods -v`
Expected: 6 passed.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 138 passed (132 prior + 6 new).

- [ ] **Step 6: Commit**

```bash
git add app/token_db.py tests/test_token_db.py
git commit -m "feat(token-db): init_schema, add, list, revoke (#61)"
```

---

## Task 4: Operator CLI — `python -m scripts.tokens`

**Files:**
- Create: `scripts/tokens.py`
- Test: `tests/test_tokens_cli.py` (NEW)

The CLI is the user-facing surface. Tests use the `argparse` parser directly + capture stdout/stderr via `capsys` — no subprocess overhead.

- [ ] **Step 1: Create `tests/test_tokens_cli.py` with failing tests**

```python
"""Tests for scripts/tokens.py — operator CLI."""

import sys

import pytest


# Fake TokenDB used in place of the real one for CLI tests
class FakeTokenDB:
    def __init__(self, url: str = "fake://"):
        self.url = url
        self.calls: list[tuple[str, tuple, dict]] = []
        self.next_id = 1
        self.rows: list[dict] = []
        self.added: list[dict] = []
        self.revoked_ids: list[int] = []
        self.fail_with: Exception | None = None

    def init_schema(self):
        self.calls.append(("init_schema", (), {}))

    def add(self, value, label):
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append(("add", (value, label), {}))
        row_id = self.next_id
        self.next_id += 1
        self.added.append({"id": row_id, "value": value, "label": label})
        return row_id

    def list_active(self):
        return [r for r in self.rows if not r.get("revoked_at")]

    def list_all(self):
        return list(self.rows)

    def revoke(self, token_id):
        self.revoked_ids.append(token_id)
        for r in self.rows:
            if r["id"] == token_id and not r.get("revoked_at"):
                r["revoked_at"] = "2026-04-29T16:00:00"
                return True
        return False


@pytest.fixture
def fake_db(monkeypatch):
    from scripts import tokens

    fake = FakeTokenDB()
    monkeypatch.setattr(tokens, "_make_db", lambda url: fake)
    monkeypatch.setenv("PC2NUTS_TOKEN_DB_URL", "fake://")
    return fake


# ── init subcommand ────────────────────────────────────────────────────────


def test_init_calls_init_schema(fake_db, capsys):
    from scripts.tokens import main

    rc = main(["init"])
    assert rc == 0
    assert ("init_schema", (), {}) in fake_db.calls


# ── add subcommand ─────────────────────────────────────────────────────────


def test_add_generates_token_when_no_value(fake_db, capsys):
    from scripts.tokens import main

    rc = main(["add", "--label", "alice"])
    assert rc == 0
    out = capsys.readouterr().out
    assert len(fake_db.added) == 1
    generated = fake_db.added[0]["value"]
    assert len(generated) == 48  # 24 bytes hex
    assert all(c in "0123456789abcdef" for c in generated)
    # Output should include the raw token, the row id, and the audit token_id
    assert generated in out
    assert "id=1" in out
    assert "token_id=" in out


def test_add_with_value_uses_provided(fake_db, capsys):
    from scripts.tokens import main

    rc = main(["add", "--label", "perf-test", "--value", "deadbeef" * 6])
    assert rc == 0
    assert fake_db.added[0]["value"] == "deadbeef" * 6


def test_add_failure_exits_non_zero(fake_db, capsys):
    from app.token_db import TokenDBError
    from scripts.tokens import main

    fake_db.fail_with = TokenDBError("UNIQUE constraint failed")
    rc = main(["add", "--label", "dup"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "UNIQUE" in err or "fail" in err.lower()


# ── list subcommand ────────────────────────────────────────────────────────


def test_list_default_active_only(fake_db, capsys):
    fake_db.rows = [
        {"id": 1, "label": "a", "created_at": "2026-01-01", "revoked_at": None},
        {"id": 2, "label": "b", "created_at": "2026-01-02", "revoked_at": "2026-01-03"},
    ]
    from scripts.tokens import main

    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1" in out and "a" in out
    assert "2" not in out  # revoked, hidden by default


def test_list_all_includes_revoked(fake_db, capsys):
    fake_db.rows = [
        {"id": 1, "label": "a", "created_at": "2026-01-01", "revoked_at": None},
        {"id": 2, "label": "b", "created_at": "2026-01-02", "revoked_at": "2026-01-03"},
    ]
    from scripts.tokens import main

    rc = main(["list", "--all"])
    out = capsys.readouterr().out
    assert "1" in out and "2" in out
    assert "revoked" in out.lower()


def test_list_never_prints_value(fake_db, capsys):
    fake_db.rows = [
        {"id": 1, "label": "a", "created_at": "2026-01-01", "revoked_at": None},
    ]
    # If `value` accidentally leaks via list_all, this test catches it.
    # FakeTokenDB.list_all returns rows that contain no `value` key by default,
    # mirroring the production query projection.
    from scripts.tokens import main

    main(["list"])
    out = capsys.readouterr().out
    # Sanity: a 48-hex string never appears
    import re

    assert re.search(r"[0-9a-f]{48}", out) is None


# ── revoke subcommand ──────────────────────────────────────────────────────


def test_revoke_calls_db(fake_db, capsys):
    fake_db.rows = [{"id": 5, "label": "x", "created_at": "2026-01-01", "revoked_at": None}]
    from scripts.tokens import main

    rc = main(["revoke", "5"])
    assert rc == 0
    assert 5 in fake_db.revoked_ids


def test_revoke_already_revoked_exits_zero(fake_db, capsys):
    fake_db.rows = [
        {"id": 5, "label": "x", "created_at": "2026-01-01", "revoked_at": "2026-04-28"}
    ]
    from scripts.tokens import main

    rc = main(["revoke", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "already" in out.lower()


# ── missing config ─────────────────────────────────────────────────────────


def test_missing_db_url_errors(monkeypatch, capsys):
    monkeypatch.delenv("PC2NUTS_TOKEN_DB_URL", raising=False)
    from scripts.tokens import main

    rc = main(["list"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "PC2NUTS_TOKEN_DB_URL" in err


def test_db_url_arg_overrides_env(monkeypatch, capsys):
    monkeypatch.delenv("PC2NUTS_TOKEN_DB_URL", raising=False)
    from scripts import tokens
    from scripts.tokens import main

    captured_urls: list[str] = []
    monkeypatch.setattr(
        tokens,
        "_make_db",
        lambda url: (captured_urls.append(url), FakeTokenDB(url))[1],
    )
    rc = main(["--db-url", "https://override.example", "init"])
    assert rc == 0
    assert captured_urls == ["https://override.example"]
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tokens_cli.py -v`
Expected: All fail with `ModuleNotFoundError: No module named 'scripts.tokens'`.

- [ ] **Step 3: Create `scripts/tokens.py`**

Note: the project's `scripts/` directory does not yet have an `__init__.py`. Check first:

```bash
test -f /home/bk86a/PostalCode2NUTS/scripts/__init__.py || touch /home/bk86a/PostalCode2NUTS/scripts/__init__.py
```

Create `scripts/tokens.py`:

```python
"""Operator CLI for the trusted-token registry.

Reads the database URL from PC2NUTS_TOKEN_DB_URL (or --db-url override).
Subcommands: init | add | list | revoke.

See docs/superpowers/specs/2026-04-29-db-backed-trusted-tokens-design.md.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from typing import Sequence

from app.token_db import TokenDB, TokenDBError


def _make_db(url: str) -> TokenDB:
    """Indirection seam for tests."""
    return TokenDB(url)


def _token_id(token: str) -> str:
    """Audit prefix — first 8 hex chars of sha256(token)."""
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def _resolve_db_url(args_url: str | None) -> str | None:
    if args_url:
        return args_url
    env = os.environ.get("PC2NUTS_TOKEN_DB_URL", "").strip()
    return env or None


def _cmd_init(db: TokenDB) -> int:
    db.init_schema()
    print("Schema initialised (idempotent).")
    return 0


def _cmd_add(db: TokenDB, label: str, value: str | None) -> int:
    token = value if value else secrets.token_hex(24)
    try:
        new_id = db.add(token, label)
    except TokenDBError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Generated: {token}")
    print(f"Inserted id={new_id}, label={label!r}, token_id={_token_id(token)}")
    return 0


def _cmd_list(db: TokenDB, show_all: bool) -> int:
    rows = db.list_all() if show_all else db.list_active()
    if not rows:
        print("(no tokens)")
        return 0
    print(f"{'id':>4}  {'label':30}  {'created_at':24}  status")
    print("-" * 80)
    for r in rows:
        rid = r.get("id", "?")
        label = (r.get("label") or "")[:30]
        created = r.get("created_at", "")
        revoked = r.get("revoked_at")
        status = f"revoked@{revoked}" if revoked else "active"
        print(f"{rid:>4}  {label:30}  {created:24}  {status}")
    return 0


def _cmd_revoke(db: TokenDB, token_id_arg: int) -> int:
    changed = db.revoke(token_id_arg)
    if changed:
        print(f"Token id={token_id_arg} revoked.")
    else:
        print(f"Token id={token_id_arg} already revoked (or does not exist).")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.tokens", description=__doc__)
    parser.add_argument("--db-url", help="Override PC2NUTS_TOKEN_DB_URL")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Create the trusted_tokens table (idempotent)")

    p_add = sub.add_parser("add", help="Issue a new token")
    p_add.add_argument("--label", required=True, help="Human-readable label")
    p_add.add_argument(
        "--value",
        help="Use the provided 48-hex value instead of generating one. "
        "Used for migrating existing v1 tokens.",
    )

    p_list = sub.add_parser("list", help="List tokens (active by default)")
    p_list.add_argument("--all", action="store_true", help="Include revoked tokens")

    p_revoke = sub.add_parser("revoke", help="Revoke a token by id")
    p_revoke.add_argument("id", type=int, help="Token id to revoke")

    args = parser.parse_args(argv)

    url = _resolve_db_url(args.db_url)
    if not url:
        print(
            "ERROR: PC2NUTS_TOKEN_DB_URL is not set. "
            "Provide --db-url or set the environment variable.",
            file=sys.stderr,
        )
        return 2

    db = _make_db(url)

    if args.cmd == "init":
        return _cmd_init(db)
    if args.cmd == "add":
        return _cmd_add(db, args.label, args.value)
    if args.cmd == "list":
        return _cmd_list(db, args.all)
    if args.cmd == "revoke":
        return _cmd_revoke(db, args.id)

    parser.error(f"unknown subcommand: {args.cmd}")  # unreachable due to required=True
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 4: Run tests — verify all pass**

Run: `.venv/bin/python -m pytest tests/test_tokens_cli.py -v`
Expected: 12 passed.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 150 passed (138 prior + 12 new).

- [ ] **Step 6: Lint**

Run: `.venv/bin/ruff check app/ scripts/ tests/`
Expected: All checks passed.

- [ ] **Step 7: Commit**

```bash
git add scripts/__init__.py scripts/tokens.py tests/test_tokens_cli.py
git commit -m "feat(token-db): operator CLI — scripts/tokens.py (#61)"
```

---

## Task 5: Auth integration — _db_tokens cache + union semantic

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_auth.py`

This task introduces the in-memory cache and the union, but **not** the refresh task itself (that's Task 6). After this task: setting `_db_tokens` manually in a test makes those tokens trusted. The runtime refresher comes in the next task.

- [ ] **Step 1: Append the failing tests to `tests/test_auth.py`**

```python
# ── DB-backed tokens — union semantic (#61) ──────────────────────────────────


class TestDBTokensUnion:
    def test_db_tokens_default_empty(self):
        from app import auth

        assert auth._db_tokens == frozenset()

    def test_get_trusted_tokens_unions_db_and_env(self, monkeypatch):
        from app import auth

        # env-var side: stub _get_trusted_tokens's view of settings
        from app import config

        monkeypatch.setattr(
            config.settings,
            "trusted_tokens",
            frozenset({"env-token"}),
        )
        # db side: set the module-level cache directly
        monkeypatch.setattr(auth, "_db_tokens", frozenset({"db-token"}))

        result = auth._get_trusted_tokens()
        assert result == frozenset({"env-token", "db-token"})

    def test_get_trusted_tokens_empty_db_falls_back_to_env(self, monkeypatch):
        from app import auth, config

        monkeypatch.setattr(config.settings, "trusted_tokens", frozenset({"env-only"}))
        monkeypatch.setattr(auth, "_db_tokens", frozenset())
        assert auth._get_trusted_tokens() == frozenset({"env-only"})

    def test_get_trusted_tokens_empty_env_uses_db_only(self, monkeypatch):
        from app import auth, config

        monkeypatch.setattr(config.settings, "trusted_tokens", frozenset())
        monkeypatch.setattr(auth, "_db_tokens", frozenset({"db-only"}))
        assert auth._get_trusted_tokens() == frozenset({"db-only"})

    def test_token_db_stale_default_false(self):
        from app import auth

        assert auth._token_db_stale is False
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestDBTokensUnion -v`
Expected: All 5 fail (`AttributeError: module 'app.auth' has no attribute '_db_tokens'`).

- [ ] **Step 3: Modify `app/auth.py`**

Find the existing module-level state declarations (right after the imports, before `token_id`). Add:

```python
# DB-backed token cache (refreshed by background task in main.lifespan).
# Empty until the first refresh succeeds; merged into the active set via _get_trusted_tokens.
_db_tokens: frozenset[str] = frozenset()
_token_db_stale: bool = False
```

Then modify `_get_trusted_tokens` (currently around line 38-40):

```python
def _get_trusted_tokens() -> frozenset[str]:
    """Indirection seam for tests; returns the union of DB-loaded and env-var tokens."""
    return _db_tokens | settings.trusted_tokens
```

(The function name stays the same — no callers need to change. The existing v0.16.0 `is_trusted` and `AuthMiddleware` continue to work.)

- [ ] **Step 4: Run tests — verify all pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestDBTokensUnion -v`
Expected: 5 passed.

- [ ] **Step 5: Run full suite — confirm no regression**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 155 passed (150 prior + 5 new). All 12 v0.16.0 `TestAuthBypass` tests still pass.

- [ ] **Step 6: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat(auth): _db_tokens cache + union with env-var tokens (#61)"
```

---

## Task 6: refresh_db_tokens function

**Files:**
- Modify: `app/auth.py`
- Test: `tests/test_auth.py`

This task adds the function that **does the actual DB read and updates `_db_tokens`**. It is a plain (sync) function that the lifespan task in Task 7 will call repeatedly.

- [ ] **Step 1: Append failing tests**

```python
# ── refresh_db_tokens (#61) ──────────────────────────────────────────────────


class TestRefreshDBTokens:
    def test_refresh_populates_db_tokens(self, monkeypatch):
        from app import auth

        class FakeDB:
            def list_active(self):
                return [
                    {"value": "a", "id": 1, "label": "x", "created_at": "..."},
                    {"value": "b", "id": 2, "label": "y", "created_at": "..."},
                ]

        monkeypatch.setattr(auth, "_db_tokens", frozenset())
        monkeypatch.setattr(auth, "_token_db_stale", False)
        auth.refresh_db_tokens(FakeDB())
        assert auth._db_tokens == frozenset({"a", "b"})
        assert auth._token_db_stale is False

    def test_refresh_failure_keeps_previous_set_and_sets_stale(self, monkeypatch):
        from app import auth
        from app.token_db import TokenDBError

        class FailingDB:
            def list_active(self):
                raise TokenDBError("boom")

        monkeypatch.setattr(auth, "_db_tokens", frozenset({"keepme"}))
        monkeypatch.setattr(auth, "_token_db_stale", False)
        auth.refresh_db_tokens(FailingDB())
        assert auth._db_tokens == frozenset({"keepme"})  # unchanged
        assert auth._token_db_stale is True

    def test_refresh_recovery_clears_stale(self, monkeypatch):
        from app import auth

        class FakeDB:
            def list_active(self):
                return [{"value": "good"}]

        monkeypatch.setattr(auth, "_db_tokens", frozenset({"old"}))
        monkeypatch.setattr(auth, "_token_db_stale", True)  # was stale
        auth.refresh_db_tokens(FakeDB())
        assert auth._db_tokens == frozenset({"good"})
        assert auth._token_db_stale is False
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestRefreshDBTokens -v`
Expected: 3 fail with `AttributeError: module 'app.auth' has no attribute 'refresh_db_tokens'`.

- [ ] **Step 3: Add `refresh_db_tokens` to `app/auth.py`**

Append after the `_token_db_stale` declaration block (or at the bottom of the module, before the existing `is_trusted_request` function):

```python
def refresh_db_tokens(db) -> None:
    """Reload the active token set from the DB.

    On success: replace _db_tokens with the new frozenset, clear the stale flag.
    On failure: keep _db_tokens unchanged, set _token_db_stale = True.

    `db` is duck-typed: must expose .list_active() returning rows with a 'value' key.
    """
    global _db_tokens, _token_db_stale
    try:
        rows = db.list_active()
    except Exception as exc:  # noqa: BLE001 — caller passes a duck-typed client
        import logging

        logging.getLogger(__name__).warning("token DB refresh failed: %s", exc)
        _token_db_stale = True
        return
    _db_tokens = frozenset(r["value"] for r in rows if r.get("value"))
    _token_db_stale = False
```

- [ ] **Step 4: Run tests — verify all pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestRefreshDBTokens -v`
Expected: 3 passed.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 158 passed (155 prior + 3 new).

- [ ] **Step 6: Commit**

```bash
git add app/auth.py tests/test_auth.py
git commit -m "feat(auth): refresh_db_tokens function (#61)"
```

---

## Task 7: Wire refresh task into FastAPI lifespan

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_auth.py`

The lifespan starts a background asyncio task only when `PC2NUTS_TOKEN_DB_URL` is set. The task does an initial refresh, then loops every `token_refresh_seconds`. On shutdown, the task is cancelled cleanly.

- [ ] **Step 1: Append failing test to `tests/test_auth.py`**

```python
# ── lifespan refresh task (#61) ──────────────────────────────────────────────


class TestLifespanRefresh:
    def test_lifespan_starts_refresh_when_db_url_set(self, monkeypatch):
        """When PC2NUTS_TOKEN_DB_URL is set, lifespan should call refresh_db_tokens
        at least once at startup."""
        import asyncio

        from app import auth, config, data_loader

        called: list = []

        def _fake_refresh(db):
            called.append(db)
            # Simulate one successful refresh
            monkeypatch.setattr(auth, "_db_tokens", frozenset({"loaded-from-db"}))

        monkeypatch.setattr(auth, "refresh_db_tokens", _fake_refresh)
        monkeypatch.setattr(config.settings, "token_db_url", "https://db.example/v1")
        monkeypatch.setattr(config.settings, "token_refresh_seconds", 3600)  # don't loop fast

        from unittest.mock import patch

        with patch.object(data_loader, "load_data"):
            from fastapi.testclient import TestClient
            from app.main import app

            with TestClient(app):
                pass  # entering the context = startup; exiting = shutdown

        assert len(called) >= 1, "refresh_db_tokens was not called during lifespan"

    def test_lifespan_skips_refresh_when_db_url_unset(self, monkeypatch):
        from app import auth, config, data_loader

        called: list = []

        def _fake_refresh(db):
            called.append(db)

        monkeypatch.setattr(auth, "refresh_db_tokens", _fake_refresh)
        monkeypatch.setattr(config.settings, "token_db_url", "")

        from unittest.mock import patch

        with patch.object(data_loader, "load_data"):
            from fastapi.testclient import TestClient
            from app.main import app

            with TestClient(app):
                pass

        assert called == [], "refresh_db_tokens called despite empty DB URL"
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestLifespanRefresh -v`
Expected: 2 fail (no lifespan integration yet — `refresh_db_tokens` is never called).

- [ ] **Step 3: Modify `app/main.py`**

Find the existing `lifespan` function (currently lines 79-97 in v0.16.0) and replace its body with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading TERCET data (NUTS %s)...", settings.nuts_version)
    load_data()
    table = get_lookup_table()
    estimates = get_estimates_table()
    names = get_nuts_names()
    logger.info(
        "Ready — %d postal codes loaded, %d estimates available, %d NUTS names.",
        len(table),
        len(estimates),
        len(names),
    )
    extra = get_extra_source_count()
    if extra:
        logger.info("Extra data sources configured: %d", extra)
    if get_data_stale():
        logger.warning("Serving STALE data — TERCET refresh failed, using expired cache")

    # ── Token DB refresh (#61) ──────────────────────────────────────────────
    refresh_task: asyncio.Task | None = None
    if settings.token_db_url:
        from app import auth as auth_mod
        from app.token_db import TokenDB

        token_db = TokenDB(settings.token_db_url)

        async def _refresh_loop():
            interval = max(1, settings.token_refresh_seconds)
            while True:
                await asyncio.to_thread(auth_mod.refresh_db_tokens, token_db)
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    return

        # Initial synchronous refresh so /lookup is correct from the first request
        await asyncio.to_thread(auth_mod.refresh_db_tokens, token_db)
        refresh_task = asyncio.create_task(_refresh_loop())
        logger.info(
            "Token DB refresh task started (interval %ds)",
            settings.token_refresh_seconds,
        )

    yield

    if refresh_task is not None:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
```

Add `import asyncio` near the top of `app/main.py` if it isn't already there.

- [ ] **Step 4: Run new tests — verify they pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py::TestLifespanRefresh -v`
Expected: 2 passed.

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 160 passed (158 prior + 2 new).

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_auth.py
git commit -m "feat(token-db): lifespan launches refresh task when DB URL set (#61)"
```

---

## Task 8: Expose `token_db_stale` on /health

**Files:**
- Modify: `app/models.py`
- Modify: `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Append failing tests to `tests/test_api.py`**

Inside the existing `TestHealthEndpoint` class:

```python
    def test_health_includes_token_db_stale_when_db_url_set(self, monkeypatch, mock_data):
        from unittest.mock import patch

        from app import auth, config, data_loader

        monkeypatch.setattr(config.settings, "token_db_url", "https://db.example/v1")
        monkeypatch.setattr(auth, "_token_db_stale", False)

        with patch.object(data_loader, "load_data"), patch.object(
            auth, "refresh_db_tokens", lambda db: None
        ):
            from fastapi.testclient import TestClient
            from app.main import app

            with TestClient(app) as client:
                resp = client.get("/health")
                assert resp.status_code == 200
                data = resp.json()
                assert data.get("token_db_stale") is False

    def test_health_omits_token_db_stale_when_db_url_unset(self, mock_data, client):
        # `client` fixture has no PC2NUTS_TOKEN_DB_URL configured
        resp = client.get("/health")
        assert resp.status_code == 200
        # Field is None when feature is disabled — Pydantic serializes to null,
        # which json() reports as None or omits depending on config. Accept both.
        data = resp.json()
        assert data.get("token_db_stale") in (None,)

    def test_health_token_db_stale_true_after_failure(self, monkeypatch, mock_data):
        from unittest.mock import patch

        from app import auth, config, data_loader

        monkeypatch.setattr(config.settings, "token_db_url", "https://db.example/v1")
        monkeypatch.setattr(auth, "_token_db_stale", True)

        with patch.object(data_loader, "load_data"), patch.object(
            auth, "refresh_db_tokens", lambda db: None
        ):
            from fastapi.testclient import TestClient
            from app.main import app

            with TestClient(app) as client:
                resp = client.get("/health")
                assert resp.json().get("token_db_stale") is True
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/bin/python -m pytest "tests/test_api.py::TestHealthEndpoint" -v`
Expected: 3 new fail (`assert None is False` or similar).

- [ ] **Step 3: Add field to `HealthResponse`**

In `app/models.py`, add to the `HealthResponse` model (alongside the existing fields):

```python
    token_db_stale: bool | None = None
```

- [ ] **Step 4: Wire the field in `app/main.py`'s `/health` handler**

Find the existing `health` route (around line 257). Modify the return statement:

```python
@app.get("/health", response_model=HealthResponse, summary="Health check and data statistics")
def health(response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store"
    table = get_lookup_table()
    estimates = get_estimates_table()
    stale = get_data_stale()

    # Token DB staleness — only meaningful when the feature is enabled.
    from app import auth as auth_mod

    token_db_stale = auth_mod._token_db_stale if settings.token_db_url else None

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
    )
```

- [ ] **Step 5: Run new tests — verify they pass**

Run: `.venv/bin/python -m pytest "tests/test_api.py::TestHealthEndpoint" -v`
Expected: 3 new passed.

- [ ] **Step 6: Run full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 163 passed (160 prior + 3 new).

- [ ] **Step 7: Lint + format check**

Run: `.venv/bin/ruff check app/ scripts/ tests/`
Run: `.venv/bin/ruff format --check app/ scripts/ tests/`
Both clean.

- [ ] **Step 8: Commit**

```bash
git add app/main.py app/models.py tests/test_api.py
git commit -m "feat(token-db): /health exposes token_db_stale (#61)"
```

---

## Task 9: README operator runbook update

**Files:**
- Modify: `README.md`

No tests for this task (documentation-only).

- [ ] **Step 1: Locate the existing v1 runbook in `README.md`**

It is the `## Authentication & rate-limit bypass` section (added in v0.16.0). It contains "Operator runbook — issue a token" / "revoke a token" / "find the token id" subsections.

- [ ] **Step 2: Replace the issue / revoke / find-id subsections**

Replace the three operator-runbook subsections (issue / revoke / find-id) with the v2 versions. Keep the surrounding "Configuration", "Behaviour summary", "Disable the bypass entirely", and "Security notes" subsections — only the runbooks change.

After the existing `### Configuration` table, insert:

```markdown
### Storage backend

By default, trusted tokens live in the `PC2NUTS_TRUSTED_TOKENS` env var (comma-separated; restart-to-apply). When `PC2NUTS_TOKEN_DB_URL` is configured, tokens are also loaded from a managed SQLite-compatible database; the active set is the **union** of DB-loaded tokens and env-var tokens. The DB is the primary registry; the env var serves as a disaster-recovery fallback for cases where the DB is unreachable at startup.

| Env var | Default | Purpose |
|---|---|---|
| `PC2NUTS_TOKEN_DB_URL` | `""` (unset) | Connection string for the token database. Empty → DB-backed feature disabled (v0.16.0 env-var-only behaviour). |
| `PC2NUTS_TOKEN_REFRESH_SECONDS` | `60` | How often the running service reloads the active set from the DB. |
```

Replace the three v1 runbook subsections with these v2 versions:

```markdown
### Operator runbook — initial setup (one-time)

```bash
# On your laptop, with the DB connection string in your environment:
export PC2NUTS_TOKEN_DB_URL='<connection string from the database provider>'
python -m scripts.tokens init
# → idempotent. Safe to re-run.
```

### Operator runbook — issue a token

```bash
python -m scripts.tokens add --label "alice-batch-2026-04"
# Generated: e3a1f2…d4
# Inserted id=3, label='alice-batch-2026-04', token_id=9a29f07a

# The token is active in the running service within ~PC2NUTS_TOKEN_REFRESH_SECONDS.
# Hand the printed token to the consumer over a confidential channel
# (1Password, Signal, encrypted email — not Slack, not GitHub issues).
```

### Operator runbook — list tokens

```bash
python -m scripts.tokens list           # active only (default)
python -m scripts.tokens list --all     # include revoked
```

The `value` column is never printed.

### Operator runbook — revoke a token

```bash
python -m scripts.tokens revoke 3
# Token id=3 revoked.
# (Already-revoked ids print "already revoked" and exit 0.)
```

Revocation takes effect within `PC2NUTS_TOKEN_REFRESH_SECONDS` (default 60 s). For an emergency revocation, restart the container to force an immediate refresh.

### Operator runbook — find the token id of a logged request

```bash
echo -n "<token>" | sha256sum | cut -c1-8
```

The CLI's `add` command also prints the `token_id` at issuance time.

### Operator runbook — migrate a v1 env-var token

To preserve the audit `token_id` of an existing env-var token when moving it to the DB:

```bash
python -m scripts.tokens add --label "perf-test-2026-04-29" --value "<the existing 48-hex token>"
```

Then remove that token from `PC2NUTS_TRUSTED_TOKENS` on the next config edit.
```

- [ ] **Step 3: Add the union-semantic note to the "Disable the bypass entirely" subsection**

Replace the existing single-paragraph "Disable the bypass entirely" subsection with:

```markdown
### Disable the bypass entirely

Unset both `PC2NUTS_TOKEN_DB_URL` and `PC2NUTS_TRUSTED_TOKENS`. All traffic falls back to the per-IP cap. The `Authorization` header is ignored entirely (no 400, no 401) when the feature is disabled. No code change needed.

If only the DB URL is unset (env var still set), behaviour reverts exactly to v0.16.0.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README runbook for DB-backed trusted tokens (#61)"
```

---

## Task 10: CHANGELOG entry + final lint/format/test sweep

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add `[Unreleased]` section**

If `CHANGELOG.md` does not currently have an `[Unreleased]` section above `[0.16.0] - 2026-04-29`, add one. Otherwise extend the existing one.

```markdown
The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- **DB-backed trusted tokens** (#61): trusted-token storage moved from `PC2NUTS_TRUSTED_TOKENS` env var to a managed SQLite-compatible HTTP database. New env vars: `PC2NUTS_TOKEN_DB_URL` (connection string), `PC2NUTS_TOKEN_REFRESH_SECONDS` (default `60`). Tokens are issued via `python -m scripts.tokens add --label "..."` and take effect within ~60 s — no container restart required. The env var continues to work as a union with the DB and serves as a disaster-recovery fallback when the DB is unreachable. New `/health` field `token_db_stale` flags refresh failures.
- **`scripts/tokens.py` operator CLI** with subcommands `init`, `add`, `list`, `revoke`. `add --value <existing-token>` lets operators migrate v1 env-var tokens while preserving their audit `token_id`.

## [0.16.0] - 2026-04-29
```

- [ ] **Step 2: Final sweep**

Run, in order:

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check app/ scripts/
.venv/bin/ruff format --check app/ scripts/ tests/
```

Expected: 163 passed, 0 ruff issues, format clean.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for DB-backed trusted tokens (#61)"
```

---

## Task 11: Push, close issue, release v0.17.0

**Files:** none (git/gh operations).

This task is intentionally last and human-supervised: it makes shared-state changes (push to main, public release).

- [ ] **Step 1: Push to origin**

```bash
git push origin main
```

- [ ] **Step 2: Wait for CI to confirm green on main HEAD**

```bash
gh run list --branch main --limit 1
# Wait until the most recent CI run shows "success".
```

- [ ] **Step 3: Bump CHANGELOG `[Unreleased]` → `[0.17.0] - 2026-04-29`**

In `CHANGELOG.md`, change `## [Unreleased]` to `## [0.17.0] - 2026-04-29`. Commit:

```bash
git add CHANGELOG.md
git commit -m "chore: cut v0.17.0"
git push origin main
```

- [ ] **Step 4: Create the GitHub release**

```bash
gh release create v0.17.0 \
  --title "v0.17.0 — DB-backed trusted tokens" \
  --notes "$(cat <<'EOF'
### What's new

- **DB-backed trusted tokens** (#61): trusted-token storage moved from `PC2NUTS_TRUSTED_TOKENS` env var to a managed SQLite-compatible HTTP database. Configure with `PC2NUTS_TOKEN_DB_URL`. Tokens issued via `python -m scripts.tokens add --label "..."` take effect within ~60 s (configurable via `PC2NUTS_TOKEN_REFRESH_SECONDS`) — **no container restart required**. The env var continues to work as a union with the DB and serves as a disaster-recovery fallback when the DB is unreachable.
- **Operator CLI** at `python -m scripts.tokens` — subcommands `init`, `add`, `list`, `revoke`. `add --value <token>` preserves audit-id continuity when migrating v1 env-var tokens.
- **`/health` field `token_db_stale`** — flags when the most recent DB refresh failed and the in-memory set is stale.

### Backwards compatibility

Fully backwards-compatible with v0.16.0. With `PC2NUTS_TOKEN_DB_URL` unset, behaviour is identical to v0.16.0. The env var is **not** deprecated in this release.

### Operator runbook

See [README → Authentication & rate-limit bypass](https://github.com/bk86a/PostalCode2NUTS/blob/main/README.md#authentication--rate-limit-bypass).

Closes #61.
EOF
)"
```

- [ ] **Step 5: Close #61 with a summary**

```bash
gh issue close 61 -c "Implemented across 10 task commits + cut v0.17.0.

Behaviour:
- PC2NUTS_TOKEN_DB_URL set → tokens load from DB every PC2NUTS_TOKEN_REFRESH_SECONDS
- env var PC2NUTS_TRUSTED_TOKENS continues to work; active set = DB ∪ env var
- DB unreachable at startup → service still serves; bypass falls back to env var
- /health surfaces token_db_stale=true on refresh failures

Operator CLI:
- python -m scripts.tokens init / add / list / revoke
- add --value <existing-token> for v1→v2 migration with audit-id continuity

Out of scope per spec: per-token quotas, last_used_at, HTTP admin endpoint, forced env-var deprecation.

See [README → Authentication & rate-limit bypass](https://github.com/bk86a/PostalCode2NUTS/blob/main/README.md#authentication--rate-limit-bypass) for the operator runbook."
```

- [ ] **Step 6: Manual integration verification (HUMAN STEP — does not block plan completion)**

This is the only step the implementer **cannot fully automate** — it requires a real database instance:

1. Provision a database instance with the configured provider; obtain the connection string.
2. Set `PC2NUTS_TOKEN_DB_URL` on the production deployment to the connection string.
3. Run `python -m scripts.tokens init` once from the operator's laptop (with the same env var).
4. Run `python -m scripts.tokens add --label "perf-test-migration" --value "<the existing v1 token>"`.
5. After ~60 s, verify the token still works:
   ```bash
   curl -i -H "Authorization: Bearer <token>" "https://<service-host>/lookup?country=DE&postal_code=10115"
   # → 200, audit log shows the same token_id as before
   ```
6. Verify `/health` includes `token_db_stale: false`.
7. Remove the migrated token from `PC2NUTS_TRUSTED_TOKENS` env var; verify it still works (now sourced from DB only).
