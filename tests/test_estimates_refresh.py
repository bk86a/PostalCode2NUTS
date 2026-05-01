"""Tests for app.estimates_refresh — periodic refresh of tercet_missing_codes.csv (#44)."""

import importlib
from unittest.mock import patch

import httpx
import pytest


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reload the module before each test so module-scoped state doesn't leak."""
    import app.estimates_refresh

    importlib.reload(app.estimates_refresh)
    yield


class TestSanityGuard:
    def test_accepts_when_current_is_empty(self):
        from app.estimates_refresh import _passes_sanity_guard

        assert _passes_sanity_guard(new_count=0, current_count=0) is True
        assert _passes_sanity_guard(new_count=10, current_count=0) is True

    def test_accepts_when_new_is_at_or_above_50_percent(self):
        from app.estimates_refresh import _passes_sanity_guard

        assert _passes_sanity_guard(new_count=5000, current_count=10000) is True
        assert _passes_sanity_guard(new_count=10001, current_count=10000) is True

    def test_rejects_when_new_is_below_50_percent(self):
        from app.estimates_refresh import _passes_sanity_guard

        assert _passes_sanity_guard(new_count=4999, current_count=10000) is False
        assert _passes_sanity_guard(new_count=0, current_count=10000) is False
