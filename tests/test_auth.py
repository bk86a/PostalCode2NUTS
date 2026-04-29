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
