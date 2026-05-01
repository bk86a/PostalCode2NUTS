"""Tests for app.limiter — verify the Limiter is wired with the right storage
mode based on settings.rate_limit_storage_uri."""

import importlib

import pytest


def _reload_limiter():
    """Reload app.config and app.limiter so the module-level Limiter picks up
    the current env. Returns the freshly-imported app.limiter module."""
    import app.config
    import app.limiter
    importlib.reload(app.config)
    importlib.reload(app.limiter)
    return app.limiter


class TestLimiterStorageSelection:
    def test_limiter_default_uses_memory_storage(self, monkeypatch):
        """When no storage URI is set, the Limiter is constructed with the
        slowapi default (in-process memory) and fallback is disabled. This is
        the byte-for-byte current behaviour."""
        monkeypatch.delenv("PC2NUTS_RATE_LIMIT_STORAGE_URI", raising=False)
        monkeypatch.delenv("PC2NUTS_WORKERS", raising=False)

        mod = _reload_limiter()

        # slowapi stores the configured URI on _storage_uri; default is None.
        assert mod.limiter._storage_uri is None
        # in_memory_fallback_enabled is only meaningful when a URI is set.
        assert mod.limiter._in_memory_fallback_enabled is False

    def test_limiter_with_redis_uri_enables_fallback(self, monkeypatch):
        """When a storage URI is set, the Limiter is constructed with that URI
        AND in_memory_fallback_enabled=True. No network call should happen at
        construction time — slowapi builds the storage object lazily."""
        monkeypatch.setenv("PC2NUTS_RATE_LIMIT_STORAGE_URI", "redis://localhost:6379/0")
        monkeypatch.setenv("PC2NUTS_WORKERS", "2")

        mod = _reload_limiter()

        assert mod.limiter._storage_uri == "redis://localhost:6379/0"
        assert mod.limiter._in_memory_fallback_enabled is True

    @pytest.fixture(autouse=True)
    def _restore_default_after_each_test(self, monkeypatch):
        """After each test, force a reload back to defaults so other tests
        in the suite see the unmodified module."""
        yield
        monkeypatch.delenv("PC2NUTS_RATE_LIMIT_STORAGE_URI", raising=False)
        monkeypatch.delenv("PC2NUTS_WORKERS", raising=False)
        _reload_limiter()
