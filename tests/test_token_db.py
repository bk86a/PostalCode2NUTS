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

    def test_init_rewrites_libsql_scheme(self):
        from app.token_db import TokenDB

        db = TokenDB("libsql://db.example/")
        assert db.url == "https://db.example"

    def test_init_stores_auth_token(self):
        from app.token_db import TokenDB

        db = TokenDB("https://db.example", auth_token="jwt-xyz")
        assert db.auth_token == "jwt-xyz"

    def test_init_default_auth_token_empty(self):
        from app.token_db import TokenDB

        db = TokenDB("https://db.example")
        assert db.auth_token == ""


class TestTokenDBExecute:
    def _mock_response(self, monkeypatch, *, json_body: dict, status_code: int = 200):
        from app import token_db  # noqa: F401 — ensure module exists

        class _Response:
            def __init__(self, body, code=200):
                self._body = body
                self.status_code = code

            def json(self):
                return self._body

            def raise_for_status(self):
                if self.status_code >= 400:
                    import httpx

                    raise httpx.HTTPStatusError("boom", request=None, response=self)

        captured: dict = {}

        def _post(self, url, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _Response(json_body, status_code)

        import httpx

        monkeypatch.setattr(httpx.Client, "post", _post)
        return captured

    def _ok(self, rows: list[list[dict]] | None = None, cols: list[str] | None = None) -> dict:
        """Build a Hrana v2 success-response envelope."""
        return {
            "results": [
                {
                    "type": "ok",
                    "response": {
                        "type": "execute",
                        "result": {
                            "cols": [{"name": c} for c in (cols or [])],
                            "rows": rows or [],
                        },
                    },
                }
            ]
        }

    def test_execute_posts_to_v2_pipeline(self, monkeypatch):
        from app.token_db import TokenDB

        captured = self._mock_response(monkeypatch, json_body=self._ok())
        db = TokenDB("https://db.example/v1")
        db.execute("SELECT 1")
        assert captured["url"] == "https://db.example/v1/v2/pipeline"

    def test_execute_wraps_sql_and_params_in_hrana(self, monkeypatch):
        from app.token_db import TokenDB

        captured = self._mock_response(monkeypatch, json_body=self._ok())
        db = TokenDB("https://db.example/v1")
        db.execute("SELECT * FROM t WHERE id = ?", [42])

        assert captured["json"] == {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": "SELECT * FROM t WHERE id = ?",
                        "args": [{"type": "integer", "value": "42"}],
                    },
                }
            ]
        }

    def test_execute_passes_auth_token_in_header(self, monkeypatch):
        from app.token_db import TokenDB

        captured = self._mock_response(monkeypatch, json_body=self._ok())
        db = TokenDB("https://db.example/v1", auth_token="jwt-xyz")
        db.execute("SELECT 1")
        assert captured["headers"].get("Authorization") == "Bearer jwt-xyz"

    def test_execute_no_auth_header_when_token_empty(self, monkeypatch):
        from app.token_db import TokenDB

        captured = self._mock_response(monkeypatch, json_body=self._ok())
        db = TokenDB("https://db.example/v1")
        db.execute("SELECT 1")
        assert "Authorization" not in (captured["headers"] or {})

    def test_execute_unwraps_hrana_rows_to_dicts(self, monkeypatch):
        from app.token_db import TokenDB

        body = self._ok(
            cols=["id", "label"],
            rows=[
                [{"type": "integer", "value": "1"}, {"type": "text", "value": "a"}],
                [{"type": "integer", "value": "2"}, {"type": "null"}],
            ],
        )
        self._mock_response(monkeypatch, json_body=body)
        db = TokenDB("https://db.example/v1")
        rows = db.execute("SELECT id, label FROM t")
        assert rows == [{"id": 1, "label": "a"}, {"id": 2, "label": None}]

    def test_execute_no_params_omits_args(self, monkeypatch):
        from app.token_db import TokenDB

        captured = self._mock_response(monkeypatch, json_body=self._ok())
        db = TokenDB("https://db.example/v1")
        db.execute("CREATE TABLE x (id INTEGER)")
        # When no params, the stmt should not have an "args" key (or have an empty list).
        stmt = captured["json"]["requests"][0]["stmt"]
        assert stmt.get("args", []) == []

    def test_execute_http_error_raises_token_db_error(self, monkeypatch):
        from app.token_db import TokenDB, TokenDBError

        self._mock_response(monkeypatch, json_body={"error": "boom"}, status_code=500)
        db = TokenDB("https://db.example/v1")
        with pytest.raises(TokenDBError):
            db.execute("SELECT 1")

    def test_execute_libsql_error_raises_token_db_error(self, monkeypatch):
        from app.token_db import TokenDB, TokenDBError

        body = {
            "results": [{"type": "error", "error": {"message": "UNIQUE constraint failed", "code": "..."}}]
        }
        self._mock_response(monkeypatch, json_body=body)
        db = TokenDB("https://db.example/v1")
        with pytest.raises(TokenDBError) as exc_info:
            db.execute("INSERT ...")
        assert "UNIQUE" in str(exc_info.value)


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
        sql, params = db._captured[0]
        assert "INSERT INTO trusted_tokens" in sql
        assert "value" in sql and "label" in sql
        assert params == ["e3a1f2d4", "alice-batch"]
        assert new_id == 7

    def test_list_active_filters_revoked(self, db):
        db._set_return.clear()
        db._set_return.extend(
            [
                {"id": 1, "value": "v1", "label": "a", "created_at": "2026-01-01"},
            ]
        )
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
        db.revoke(42)
        sql, params = db._captured[0]
        assert "UPDATE trusted_tokens" in sql
        assert "revoked_at" in sql
        assert "datetime('now')" in sql
        assert params == [42]

    def test_revoke_returns_true_for_active_id(self, db):
        # revoke should return True when the UPDATE … RETURNING affects a row,
        # False when it doesn't (already revoked / nonexistent).
        db._set_return.clear()
        db._set_return.extend([{"id": 99}])
        assert db.revoke(99) is True

        db._captured.clear()
        db._set_return.clear()  # empty -> already revoked
        assert db.revoke(100) is False
