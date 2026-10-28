"""load_data() must survive broken upstream files and must not persist a
partial rebuild as a fresh, complete cache."""

import io
import os
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import httpx2 as httpx
import pytest

from app import data_loader

BASE = "https://tercet.example.invalid/NUTS-2024/"


def _zip(csv_text: str, name: str = "pc.csv", *, encrypted: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, csv_text)
    raw = bytearray(buf.getvalue())
    if encrypted:
        # Set general-purpose bit 0 in the local and central headers.
        for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            i = raw.find(sig)
            raw[i + off] |= 0x01
    return bytes(raw)


GOOD = {
    "DE": _zip("CODE;NUTS3\n10115;DE300\n"),
    "FR": _zip("CODE;NUTS3\n75001;FR101\n"),
}


def _url(cc: str) -> str:
    return f"{BASE}pc2024_{cc}_NUTS-2024_v1.0.zip"


@pytest.fixture()
def loader(tmp_path, monkeypatch):
    """Point load_data at a scratch data_dir and a mock upstream.

    Returns (serve, calls): `serve` maps country → zip bytes or an int status.
    """
    for name in ("_lookup", "_estimates", "_nuts_names", "_outward_lookup"):
        monkeypatch.setattr(data_loader, name, {})
    monkeypatch.setattr(data_loader.settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(data_loader.settings, "tercet_base_url", BASE)
    monkeypatch.setattr(data_loader.settings, "countries", ["DE", "FR"])
    monkeypatch.setattr(data_loader.settings, "extra_sources", "")
    monkeypatch.setattr(data_loader.settings, "nspl_url", "")
    monkeypatch.setattr(data_loader.settings, "estimates_csv", str(tmp_path / "none.csv"))
    monkeypatch.setattr(data_loader.settings, "db_partial_retry_hours", 24)

    serve: dict[str, bytes | int] = dict(GOOD)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url == BASE:
            listing = "".join(f'<a href="{_url(cc).rsplit("/", 1)[-1]}">x</a>' for cc in ("DE", "FR"))
            return httpx.Response(200, text=listing)
        for cc, body in serve.items():
            if url == _url(cc):
                return httpx.Response(body) if isinstance(body, int) else httpx.Response(200, content=body)
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(
        data_loader.httpx, "Client", lambda *a, **k: real_client(transport=httpx.MockTransport(handler))
    )
    return serve, calls


def _db():
    return data_loader._db_path()


def _expire_caches(tmp_path):
    """Age the DB and every cached ZIP past the TTL so the next load rebuilds."""
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    with sqlite3.connect(_db()) as con:
        con.execute("UPDATE metadata SET value=? WHERE key='created_at'", (old,))
    for f in tmp_path.rglob("*.zip"):
        os.utime(f, (0, 0))


def _age_db(*, hours: float) -> None:
    old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with sqlite3.connect(_db()) as con:
        con.execute("UPDATE metadata SET value=? WHERE key='created_at'", (old,))


def _countries():
    return sorted({cc for cc, _ in data_loader._lookup})


class TestMalformedUpstream:
    @pytest.mark.parametrize(
        "bad",
        [
            _zip("CODE;NUTS3\n" + "1" * 200_000 + ";FR101\n"),  # csv.Error: field larger than field limit
            _zip("CODE;NUTS3\n75001;FR101\n", encrypted=True),  # RuntimeError: encrypted member
        ],
        ids=["overlong-field", "encrypted-member"],
    )
    def test_bad_zip_is_skipped_and_good_rows_kept(self, loader, bad, tmp_path):
        serve, _ = loader
        serve["FR"] = bad
        data_loader.load_data()  # must not raise
        assert _countries() == ["DE"]
        cached = list(tmp_path.rglob("pc2024_FR_*.zip"))
        assert cached == [], "unusable ZIP must not stay cached"

    def test_unexpected_error_falls_back_to_stale_db(self, loader, monkeypatch, tmp_path):
        data_loader.load_data()
        assert _countries() == ["DE", "FR"]
        _expire_caches(tmp_path)

        def boom(*a, **k):
            raise KeyError("surprise")

        monkeypatch.setattr(data_loader, "_download_and_parse_zip", boom)
        data_loader.load_data()
        assert _countries() == ["DE", "FR"]
        assert data_loader.get_data_stale() is True


class TestPartialRebuild:
    def test_failed_country_is_carried_forward_and_retried(self, loader, tmp_path):
        serve, calls = loader
        data_loader.load_data()
        assert data_loader._db_is_valid(_db()) is True
        _expire_caches(tmp_path)

        serve["FR"] = 503
        data_loader.load_data()
        # FR rows survive from the previous snapshot instead of vanishing.
        assert _countries() == ["DE", "FR"]
        assert data_loader.lookup("FR", "75001")["nuts3"] == "FR101"
        assert data_loader.get_data_stale() is True
        # A restart inside the retry window serves the snapshot, still as stale,
        # without re-downloading everything.
        calls.clear()
        data_loader.load_data()
        assert calls == []
        assert _countries() == ["DE", "FR"]
        assert data_loader.get_data_stale() is True

        # Past the window, the next start retries the download.
        _age_db(hours=25)
        serve["FR"] = GOOD["FR"]
        calls.clear()
        data_loader.load_data()
        assert any("pc2024_FR_" in c for c in calls), "start after the window must retry"
        assert data_loader.get_data_stale() is False
        assert data_loader._db_is_valid(_db()) is True

    def test_complete_rebuild_is_not_marked_partial(self, loader):
        data_loader.load_data()
        with sqlite3.connect(_db()) as con:
            meta = dict(con.execute("SELECT key, value FROM metadata"))
        assert meta["partial"] == "0"
        assert data_loader.get_data_stale() is False


class TestCodexReviewRegressions:
    def test_rejected_file_leaves_no_rows_so_country_is_carried_forward(self, loader, tmp_path):
        """Valid rows before an over-long field must not stay in _lookup: they
        would make FR look loaded and block restoring the rest of FR."""
        serve, _ = loader
        serve["FR"] = _zip("CODE;NUTS3\n75001;FR101\n75002;FR101\n")
        data_loader.load_data()
        _expire_caches(tmp_path)

        serve["FR"] = _zip("CODE;NUTS3\n75001;FR101\n" + "7" * 200_000 + ";FR101\n")
        data_loader.load_data()
        assert data_loader.lookup("FR", "75002")["nuts3"] == "FR101"
        assert data_loader.get_data_stale() is True

    def test_country_removed_from_config_is_not_resurrected(self, loader, monkeypatch, tmp_path):
        data_loader.load_data()
        _expire_caches(tmp_path)

        monkeypatch.setattr(data_loader.settings, "countries", ["DE"])
        serve, _ = loader
        del serve["FR"]
        # The listing still links FR; serve a 404 so only config decides.
        serve["FR"] = 404
        data_loader.load_data()
        assert _countries() == ["DE"]
        assert data_loader.get_data_stale() is False

    def test_disabled_nspl_does_not_resurrect_uk(self, loader, tmp_path):
        data_loader.load_data()
        # Simulate an old snapshot that also held NSPL-sourced UK rows.
        with sqlite3.connect(_db()) as con:
            con.execute("INSERT INTO lookup VALUES ('UK', 'SW1A2AA', 'TLI32')")
        _expire_caches(tmp_path)

        data_loader.load_data()  # NSPL is unset in the fixture
        assert "UK" not in _countries()
        assert data_loader.get_data_stale() is False
