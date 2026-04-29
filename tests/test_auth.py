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
