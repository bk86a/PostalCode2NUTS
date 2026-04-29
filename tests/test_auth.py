"""Tests for app/auth.py and trusted_tokens config parsing."""

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
        """sha256('hello') first 8 hex chars is '2cf24dba'."""
        from app.auth import token_id

        assert token_id("hello") == "2cf24dba"
