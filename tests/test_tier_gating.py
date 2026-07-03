"""Tier gating: NUTS polygons load only when a Photon geocoder is configured."""

import importlib
import json
from unittest.mock import MagicMock

from fastapi.testclient import TestClient


def _reload_app(monkeypatch, photon_url, geojson_path=""):
    monkeypatch.setenv("PC2NUTS_PHOTON_URL", photon_url)
    monkeypatch.setenv("PC2NUTS_NUTS_GEOJSON_PATH", geojson_path)
    monkeypatch.setenv("PC2NUTS_TOKEN_DB_URL", "")
    monkeypatch.setenv("PC2NUTS_ESTIMATES_REFRESH_URL", "")
    import app.config as cfg
    importlib.reload(cfg)
    import app.main as main
    importlib.reload(main)
    return main


def test_lite_mode_skips_polygon_load(monkeypatch):
    main = _reload_app(monkeypatch, photon_url="")
    spy = MagicMock()
    monkeypatch.setattr(main, "load_nuts_pip", spy)
    with TestClient(main.app) as tc:
        body = tc.get("/health").json()
    spy.assert_not_called()               # no ~160 MB polygon download in Lite
    assert body["pip_ready"] is False
    assert body["geocoder_configured"] is False


def test_full_mode_loads_polygons(monkeypatch, tmp_path):
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NUTS_ID": "DE111", "LEVL_CODE": 3},
                "geometry": {"type": "Polygon",
                             "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]},
            }
        ],
    }
    p = tmp_path / "nuts.geojson"
    p.write_text(json.dumps(geojson))
    main = _reload_app(monkeypatch, photon_url="http://photon", geojson_path=str(p))
    with TestClient(main.app) as tc:
        body = tc.get("/health").json()
    assert body["pip_ready"] is True
    assert body["geocoder_configured"] is True
