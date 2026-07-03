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
        failure mode this validator exists to prevent."""
        with pytest.raises(ValidationError) as excinfo:
            Settings(workers=2, rate_limit_storage_uri=None)
        msg = str(excinfo.value)
        assert "PC2NUTS_WORKERS" in msg
        assert "PC2NUTS_RATE_LIMIT_STORAGE_URI" in msg

    def test_workers_gt_1_with_empty_storage_uri_fails_startup(self):
        """Empty string should be treated the same as None — both mean unset."""
        with pytest.raises(ValidationError):
            Settings(workers=2, rate_limit_storage_uri="")


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


def test_synthetic_nuts_fallback_has_fo():
    from app.config import settings
    assert settings.synthetic_nuts_fallback.get("FO") == "FO000"


class TestNSPLSettings:
    def test_nspl_url_defaults_empty(self):
        assert Settings().nspl_url == ""

    def test_itl_names_urls_defaults_empty(self):
        assert Settings().itl_names_urls == ""

    def test_itl_names_url_list_parses_csv(self):
        s = Settings(itl_names_urls="https://a/x.csv, https://b/y.csv ,")
        assert s.itl_names_url_list == ["https://a/x.csv", "https://b/y.csv"]

    def test_itl_names_url_list_empty_when_unset(self):
        assert Settings().itl_names_url_list == []

    def test_nspl_url_from_env(self, monkeypatch):
        monkeypatch.setenv("PC2NUTS_NSPL_URL", "https://ons/nspl.zip")
        assert Settings().nspl_url == "https://ons/nspl.zip"

    def test_uk_not_in_settings_countries(self):
        """Regression guard: UK must not appear in the GISCO country list —
        it would trigger wasted GISCO URL guesses (Codex review, PR #52)."""
        assert "UK" not in Settings().countries
