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
