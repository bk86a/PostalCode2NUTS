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


class TestFetchRemoteCsv:
    @pytest.fixture
    def url(self, monkeypatch):
        u = "https://example.invalid/tercet.csv"
        monkeypatch.setattr("app.estimates_refresh.settings", _stub_settings(url=u))
        return u

    @staticmethod
    def _client_with(handler):
        """Build an httpx.AsyncClient that routes every request through handler."""
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    @pytest.mark.asyncio
    async def test_returns_body_on_200(self, url):
        from app.estimates_refresh import fetch_remote_csv

        body = b"COUNTRY_CODE,POSTAL_CODE\nDE,99999\n"

        def handler(request):
            assert str(request.url) == url
            return httpx.Response(200, content=body, headers={"ETag": "W/abc"})

        async with self._client_with(handler) as client:
            data, status, headers = await fetch_remote_csv(client)

        assert status == 200
        assert data == body
        assert headers.get("etag") == "W/abc"

    @pytest.mark.asyncio
    async def test_returns_none_on_304(self, url):
        from app.estimates_refresh import fetch_remote_csv

        def handler(request):
            return httpx.Response(304)

        async with self._client_with(handler) as client:
            data, status, _ = await fetch_remote_csv(client)

        assert status == 304
        assert data is None

    @pytest.mark.asyncio
    async def test_sends_conditional_headers_when_state_present(self, url, monkeypatch):
        from app import estimates_refresh

        monkeypatch.setattr(estimates_refresh, "_last_etag", "W/abc")
        monkeypatch.setattr(estimates_refresh, "_last_modified", "Wed, 01 Jan 2026 00:00:00 GMT")

        seen: dict[str, str] = {}

        def handler(request):
            seen["if-none-match"] = request.headers.get("if-none-match", "")
            seen["if-modified-since"] = request.headers.get("if-modified-since", "")
            return httpx.Response(200, content=b"", headers={})

        async with self._client_with(handler) as client:
            await estimates_refresh.fetch_remote_csv(client)

        assert seen["if-none-match"] == "W/abc"
        assert seen["if-modified-since"] == "Wed, 01 Jan 2026 00:00:00 GMT"

    @pytest.mark.asyncio
    async def test_returns_none_on_5xx(self, url):
        from app.estimates_refresh import fetch_remote_csv

        def handler(request):
            return httpx.Response(503, content=b"down")

        async with self._client_with(handler) as client:
            data, status, _ = await fetch_remote_csv(client)

        assert status == 503
        assert data is None

    @pytest.mark.asyncio
    async def test_returns_none_on_transport_error(self, url):
        from app.estimates_refresh import fetch_remote_csv

        def handler(request):
            raise httpx.ConnectError("boom")

        async with self._client_with(handler) as client:
            data, status, _ = await fetch_remote_csv(client)

        assert status == 0
        assert data is None


def _stub_settings(*, url: str = "", interval: int = 86400):
    """Build a minimal settings stub for tests that only need the two new fields."""

    class _S:
        estimates_refresh_url = url
        estimates_refresh_interval_seconds = interval

    return _S()
