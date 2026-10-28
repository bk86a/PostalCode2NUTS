"""Tests for app/nuts_polygons.py."""

import json

import httpx2 as httpx

from app.nuts_polygons import load_nuts_pip

_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"NUTS_ID": "AB100", "LEVL_CODE": 3},
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        }
    ],
}


def test_load_from_local_path(tmp_path):
    p = tmp_path / "n.geojson"
    p.write_text(json.dumps(_GEOJSON))
    pip = load_nuts_pip(url="http://x", path=str(p), cache_dir=str(tmp_path), client=None)
    assert pip.lookup(0.5, 0.5)["nuts3"] == "AB100"


def test_downloads_and_caches(tmp_path):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        # a minimal zip containing the _RG_ member
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("NUTS_RG_01M_2024_4326_LEVL_3.geojson", json.dumps(_GEOJSON))
        return httpx.Response(200, content=buf.getvalue())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pip = load_nuts_pip(url="http://x/n.zip", path="", cache_dir=str(tmp_path), client=client)
    assert pip.lookup(0.5, 0.5)["nuts3"] == "AB100"
    # second call uses the cache, no new download
    load_nuts_pip(url="http://x/n.zip", path="", cache_dir=str(tmp_path), client=client)
    assert calls["n"] == 1


def _zip_bytes(member: str, payload: bytes) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, payload)
    return buf.getvalue()


_MEMBER = "NUTS_RG_01M_2024_4326_LEVL_3.geojson"


def test_non_zip_download_is_not_cached(tmp_path):
    import pytest

    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>oops</html>"))
    )
    with pytest.raises(ValueError):
        load_nuts_pip(url="http://x/n.zip", path="", cache_dir=str(tmp_path), client=client)
    assert not (tmp_path / "ref-nuts-polygons.geojson.zip").exists()


def test_corrupt_cache_is_replaced(tmp_path):
    (tmp_path / "ref-nuts-polygons.geojson.zip").write_bytes(b"<html>stale error page</html>")
    good = _zip_bytes(_MEMBER, json.dumps(_GEOJSON).encode())
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=good)))
    pip = load_nuts_pip(url="http://x/n.zip", path="", cache_dir=str(tmp_path), client=client)
    assert pip.lookup(0.5, 0.5)["nuts3"] == "AB100"


def test_oversized_member_is_refused_before_parsing(tmp_path, monkeypatch):
    import pytest

    from app import nuts_pip

    monkeypatch.setattr(nuts_pip, "MAX_GEOJSON_BYTES", 1024)
    bomb = _zip_bytes(_MEMBER, b" " * 4096 + json.dumps(_GEOJSON).encode())
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=bomb)))
    with pytest.raises(ValueError, match="over the"):
        load_nuts_pip(url="http://x/n.zip", path="", cache_dir=str(tmp_path), client=client)
