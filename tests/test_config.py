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
        failure mode this validator exists to prevent. SystemExit rather than
        ValidationError: pydantic renders the settings input dict, which holds
        the trusted tokens, into its error message."""
        with pytest.raises(SystemExit) as excinfo:
            Settings(workers=2, rate_limit_storage_uri=None)
        msg = str(excinfo.value)
        assert "PC2NUTS_WORKERS" in msg
        assert "PC2NUTS_RATE_LIMIT_STORAGE_URI" in msg

    def test_workers_gt_1_with_empty_storage_uri_fails_startup(self):
        """Empty string should be treated the same as None — both mean unset."""
        with pytest.raises(SystemExit):
            Settings(workers=2, rate_limit_storage_uri="")

    def test_failure_does_not_echo_configured_secrets(self, monkeypatch):
        """An unrelated misconfiguration must not print live credentials."""
        monkeypatch.setenv("PC2NUTS_TRUSTED_TOKENS", "d" * 48)
        monkeypatch.setenv("PC2NUTS_TOKEN_DB_AUTH_TOKEN", "super-secret-jwt")
        with pytest.raises(SystemExit) as excinfo:
            Settings(workers=2, rate_limit_storage_uri=None)
        msg = str(excinfo.value)
        assert "d" * 48 not in msg
        assert "super-secret-jwt" not in msg


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


class TestNSPLSettings:
    def test_nspl_url_defaults_empty(self):
        assert Settings().nspl_url == ""

    def test_uk_itl_lookup_url_defaults_empty(self):
        assert Settings().uk_itl_lookup_url == ""

    def test_nspl_url_from_env(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_NSPL_URL", "https://ons/nspl.zip")
        assert Settings().nspl_url == "https://ons/nspl.zip"

    def test_uk_itl_lookup_url_from_env(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_UK_ITL_LOOKUP_URL", "https://ons/lad-itl.csv")
        assert Settings().uk_itl_lookup_url == "https://ons/lad-itl.csv"

    def test_uk_not_in_settings_countries(self):
        """Regression guard: UK must not appear in the GISCO country list —
        it would trigger wasted GISCO URL guesses (Codex review, PR #52)."""
        assert "UK" not in Settings().countries


class TestTrustedTokenLength:
    """A short env-var token is brute-forceable: AuthMiddleware answers 401
    before the rate limiter runs, so failed attempts are never metered."""

    STRONG = "a" * 48
    ALSO_STRONG = "b" * 32

    @staticmethod
    def _with_tokens(monkeypatch, raw: str) -> Settings:
        monkeypatch.setenv("PC2NUTS_TRUSTED_TOKENS", raw)
        return Settings()

    def test_empty_is_allowed(self, monkeypatch):
        s = self._with_tokens(monkeypatch, "")
        assert s.trusted_tokens == frozenset()

    def test_long_tokens_are_allowed(self, monkeypatch):
        s = self._with_tokens(monkeypatch, f"{self.STRONG},{self.ALSO_STRONG}")
        assert s.trusted_tokens == frozenset({self.STRONG, self.ALSO_STRONG})

    def test_short_token_fails_startup(self, monkeypatch):
        with pytest.raises(SystemExit) as excinfo:
            self._with_tokens(monkeypatch, "hunter2")
        assert "PC2NUTS_TRUSTED_TOKENS" in str(excinfo.value)

    def test_one_short_token_among_strong_ones_fails_startup(self, monkeypatch):
        with pytest.raises(SystemExit):
            self._with_tokens(monkeypatch, f"{self.STRONG},short")

    def test_error_does_not_echo_the_token(self, monkeypatch):
        """The message travels in logs and crash reports — keep the value out."""
        with pytest.raises(SystemExit) as excinfo:
            self._with_tokens(monkeypatch, "sekrit-but-short")
        assert "sekrit-but-short" not in str(excinfo.value)

    def test_boundary_length_is_accepted(self, monkeypatch):
        s = self._with_tokens(monkeypatch, "c" * 32)
        assert len(s.trusted_tokens) == 1

    def test_one_below_boundary_is_rejected(self, monkeypatch):
        with pytest.raises(SystemExit):
            self._with_tokens(monkeypatch, "c" * 31)
