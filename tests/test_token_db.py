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
