"""Tests for app.estimates_refresh — periodic refresh of tercet_missing_codes.csv (#44)."""

import asyncio
import hashlib
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


class TestRefreshOnce:
    @pytest.fixture
    def url(self, monkeypatch):
        u = "https://example.invalid/tercet.csv"
        monkeypatch.setattr("app.estimates_refresh.settings", _stub_settings(url=u))
        return u

    @pytest.fixture
    def seed_estimates(self):
        from app.data_loader import _estimates

        _estimates.clear()
        for i in range(100):
            _estimates[("DE", f"{10000+i}")] = {
                "nuts3": "DE300",
                "nuts2": "DE30",
                "nuts1": "DE3",
                "nuts3_confidence": 0.9,
                "nuts2_confidence": 0.95,
                "nuts1_confidence": 0.98,
            }
        yield _estimates
        _estimates.clear()

    @staticmethod
    def _csv(rows: list[tuple[str, str, str]] = None) -> bytes:
        rows = rows or [("DE", "99999", "high"), ("FR", "75000", "medium")]
        header = "COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\n"
        body = "".join(f"{cc},{pc},{cc}300,{cc}30,{cc}3,{conf}\n" for cc, pc, conf in rows)
        return (header + body).encode("utf-8")

    @pytest.mark.asyncio
    async def test_disabled_when_url_unset(self, monkeypatch):
        monkeypatch.setattr("app.estimates_refresh.settings", _stub_settings(url=""))
        from app.estimates_refresh import refresh_estimates_once

        result = await refresh_estimates_once()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_swaps_on_changed_content(self, url, seed_estimates):
        from app import estimates_refresh

        new_csv = self._csv([("DE", str(20000 + i), "high") for i in range(80)])

        def handler(request):
            return httpx.Response(200, content=new_csv, headers={"ETag": "W/new"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "refreshed"
        assert result.previous_count == 100
        assert result.new_count == 80
        assert estimates_refresh._stale is False
        assert estimates_refresh._last_etag == "W/new"

    @pytest.mark.asyncio
    async def test_unchanged_on_304(self, url, seed_estimates):
        from app import estimates_refresh

        estimates_refresh._last_etag = "W/abc"

        def handler(request):
            return httpx.Response(304)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "unchanged"
        assert result.previous_count == 100
        assert result.new_count == 100
        assert estimates_refresh._stale is False
        # Live dict was not touched
        assert len(seed_estimates) == 100

    @pytest.mark.asyncio
    async def test_unchanged_on_identical_hash(self, url, seed_estimates):
        from app import estimates_refresh

        body = self._csv()
        estimates_refresh._last_hash = hashlib.sha256(body).hexdigest()

        def handler(request):
            return httpx.Response(200, content=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "unchanged"
        assert estimates_refresh._stale is False

    @pytest.mark.asyncio
    async def test_failed_on_5xx(self, url, seed_estimates):
        from app import estimates_refresh

        def handler(request):
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "failed"
        assert estimates_refresh._stale is True
        assert len(seed_estimates) == 100  # unchanged

    @pytest.mark.asyncio
    async def test_warning_logs_once_per_outage(self, url, seed_estimates, caplog):
        """Successive failures must NOT each emit a WARNING; only the
        transition from success → failure should log."""
        from app import estimates_refresh

        def handler(request):
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            # First call: success → failure transition. Must log WARNING.
            with caplog.at_level("WARNING", logger="app.estimates_refresh"):
                await estimates_refresh.refresh_estimates_once(client=client)
            first_warnings = [r for r in caplog.records if r.levelname == "WARNING"]
            assert any("fetch failed" in r.message for r in first_warnings), (
                f"first failure should warn; got {first_warnings}"
            )

            # Second call: still failing. Must NOT emit a second "fetch failed" WARNING.
            caplog.clear()
            with caplog.at_level("WARNING", logger="app.estimates_refresh"):
                await estimates_refresh.refresh_estimates_once(client=client)
            second_warnings = [r for r in caplog.records if r.levelname == "WARNING"]
            assert not any("fetch failed" in r.message for r in second_warnings), (
                f"successive failures must be silent; got {second_warnings}"
            )

    @pytest.mark.asyncio
    async def test_failed_on_parse_error(self, url, seed_estimates):
        from app import estimates_refresh

        def handler(request):
            return httpx.Response(200, content=b"not,a,valid,csv\n\xff\xfe")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "failed"
        assert estimates_refresh._stale is True
        assert len(seed_estimates) == 100

    @pytest.mark.asyncio
    async def test_rejected_by_sanity_guard(self, url, seed_estimates):
        from app import estimates_refresh

        small_csv = self._csv([("DE", "99999", "high")])  # 1 row vs current 100

        def handler(request):
            return httpx.Response(200, content=small_csv)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "rejected"
        assert result.previous_count == 100
        assert result.new_count == 1
        assert estimates_refresh._stale is True
        # Live dict was not touched
        assert len(seed_estimates) == 100

    @pytest.mark.asyncio
    async def test_bootstrap_path_accepts_any_size(self, url):
        """When current is empty (first-ever fetch), the sanity guard must not block."""
        from app import estimates_refresh
        from app.data_loader import _estimates

        _estimates.clear()
        small_csv = self._csv([("DE", "99999", "high")])

        def handler(request):
            return httpx.Response(200, content=small_csv)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await estimates_refresh.refresh_estimates_once(client=client)

        assert result.status == "refreshed"
        assert result.new_count == 1


class TestRefreshLoop:
    @pytest.mark.asyncio
    async def test_no_op_when_url_unset(self, monkeypatch):
        """With the URL setting unset, the loop must return immediately."""
        monkeypatch.setattr("app.estimates_refresh.settings", _stub_settings(url=""))
        from app.estimates_refresh import refresh_estimates_loop

        # If the loop tried to sleep for 86400s we'd hang; wait_for guards us.
        await asyncio.wait_for(refresh_estimates_loop(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_no_op_when_interval_zero(self, monkeypatch):
        monkeypatch.setattr(
            "app.estimates_refresh.settings",
            _stub_settings(url="https://example.invalid/x.csv", interval=0),
        )
        from app.estimates_refresh import refresh_estimates_loop

        await asyncio.wait_for(refresh_estimates_loop(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_loop_calls_refresh_on_each_tick(self, monkeypatch):
        """The loop must call refresh_estimates_once once per tick."""
        monkeypatch.setattr(
            "app.estimates_refresh.settings",
            _stub_settings(url="https://example.invalid/x.csv", interval=1),
        )

        call_count = {"n": 0}

        async def fake_refresh():
            call_count["n"] += 1

        monkeypatch.setattr("app.estimates_refresh.refresh_estimates_once", fake_refresh)

        # Replace asyncio.sleep so we don't actually wait the configured interval.
        # After 3 sleep calls, raise CancelledError to break out of the otherwise
        # infinite loop.
        sleep_calls = 0

        async def fake_sleep(secs):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls >= 3:
                raise asyncio.CancelledError

        monkeypatch.setattr("app.estimates_refresh.asyncio.sleep", fake_sleep)

        from app.estimates_refresh import refresh_estimates_loop

        with pytest.raises(asyncio.CancelledError):
            await refresh_estimates_loop()

        # We slept 3 times, called refresh_estimates_once 2 times (sleep is BEFORE
        # the call, and the 3rd sleep raises before the 3rd refresh runs).
        assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_loop_survives_exception_in_refresh(self, monkeypatch):
        monkeypatch.setattr(
            "app.estimates_refresh.settings",
            _stub_settings(url="https://example.invalid/x.csv", interval=1),
        )

        async def fake_refresh_raises():
            raise RuntimeError("boom")

        monkeypatch.setattr("app.estimates_refresh.refresh_estimates_once", fake_refresh_raises)

        # Replace asyncio.sleep so we don't actually wait.
        sleeps: list[float] = []

        async def fake_sleep(secs):
            sleeps.append(secs)
            if len(sleeps) >= 3:
                raise asyncio.CancelledError

        monkeypatch.setattr("app.estimates_refresh.asyncio.sleep", fake_sleep)

        from app.estimates_refresh import refresh_estimates_loop

        with pytest.raises(asyncio.CancelledError):
            await refresh_estimates_loop()

        # Loop ran at least 2 iterations despite the exception each time
        assert len(sleeps) >= 2


class TestConcurrentRefresh:
    """Regression tests for the asyncio.Lock that serialises concurrent calls
    (Codex flagged the race on PR #72)."""

    @pytest.fixture
    def url(self, monkeypatch):
        u = "https://example.invalid/tercet.csv"
        monkeypatch.setattr("app.estimates_refresh.settings", _stub_settings(url=u))
        return u

    @pytest.fixture
    def seed_estimates(self):
        from app.data_loader import _estimates

        _estimates.clear()
        for i in range(100):
            _estimates[("DE", f"{10000 + i}")] = {
                "nuts3": "DE300",
                "nuts2": "DE30",
                "nuts1": "DE3",
                "nuts3_confidence": 0.9,
                "nuts2_confidence": 0.95,
                "nuts1_confidence": 0.98,
            }
        yield _estimates
        _estimates.clear()

    @pytest.mark.asyncio
    async def test_concurrent_calls_are_serialised(self, url, seed_estimates):
        """Two overlapping refresh_estimates_once() calls must not interleave
        their fetch+swap sequences. We instrument fetch_remote_csv to record
        entry/exit timestamps and assert the second call's entry is strictly
        after the first call's exit."""
        from app import estimates_refresh

        # Each fetch returns a unique CSV body so we can tell which one wins.
        def make_csv(tag: str) -> bytes:
            header = "COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\n"
            rows = "".join(
                f"{tag[0].upper()}{tag[1].upper()},{i:05d},XX300,XX30,XX3,high\n"
                for i in range(80)
            )
            return (header + rows).encode("utf-8")

        events: list[tuple[str, str]] = []  # [(tag, "enter"|"exit"), ...]
        responses = {"a": make_csv("aa"), "b": make_csv("bb")}
        next_tag = ["a"]

        async def instrumented_fetch(client):
            tag = next_tag[0]
            next_tag[0] = "b"
            events.append((tag, "enter"))
            # Yield twice so the other coroutine has a chance to advance.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            events.append((tag, "exit"))
            return responses[tag], 200, {"etag": f'W/"{tag}"'}

        original_fetch = estimates_refresh.fetch_remote_csv
        estimates_refresh.fetch_remote_csv = instrumented_fetch
        try:
            results = await asyncio.gather(
                estimates_refresh.refresh_estimates_once(),
                estimates_refresh.refresh_estimates_once(),
            )
        finally:
            estimates_refresh.fetch_remote_csv = original_fetch

        # Both calls should have completed without raising.
        assert len(results) == 2
        # Order must be: a-enter, a-exit, b-enter, b-exit (strictly nested).
        assert events == [("a", "enter"), ("a", "exit"), ("b", "enter"), ("b", "exit")], (
            f"events interleaved: {events}"
        )
        # The "b" call sees the "a" hash already set, so depending on whether
        # the bodies hash differently, b will either swap (different content)
        # or short-circuit as unchanged. Either is fine — the assertion above
        # is the real concurrency check.

    @pytest.mark.asyncio
    async def test_disabled_short_circuit_does_not_take_lock(self, monkeypatch):
        """The disabled short-circuit returns before acquiring the lock. This
        test isn't strictly needed for correctness, but documents the
        intentional placement of the disabled check OUTSIDE async with."""
        monkeypatch.setattr("app.estimates_refresh.settings", _stub_settings(url=""))
        from app import estimates_refresh

        # Hold the lock; if disabled-check were inside the lock, this call
        # would block forever (or until our timeout).
        async with estimates_refresh._refresh_lock:
            result = await asyncio.wait_for(
                estimates_refresh.refresh_estimates_once(), timeout=1.0
            )

        assert result.status == "disabled"


def _stub_settings(*, url: str = "", interval: int = 86400):
    """Build a minimal settings stub for tests that only need the two new fields."""

    class _S:
        estimates_refresh_url = url
        estimates_refresh_interval_seconds = interval

    return _S()
