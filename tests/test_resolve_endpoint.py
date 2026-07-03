"""Integration tests for GET /resolve via TestClient."""

import importlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from shapely.geometry import box  # noqa: F401  (ensures shapely present)


@pytest.fixture
def client(tmp_path, monkeypatch):
    # A tiny local polygon file so the oracle covers a known square.
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NUTS_ID": "BE241", "LEVL_CODE": 3},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]],
                },
            }
        ],
    }
    p = tmp_path / "nuts.geojson"
    p.write_text(json.dumps(geojson))
    monkeypatch.setenv("PC2NUTS_NUTS_GEOJSON_PATH", str(p))
    monkeypatch.setenv("PC2NUTS_PHOTON_URL", "http://photon")
    monkeypatch.setenv("PC2NUTS_TOKEN_DB_URL", "")
    monkeypatch.setenv("PC2NUTS_ESTIMATES_REFRESH_URL", "")

    import app.config as cfg

    importlib.reload(cfg)
    import app.main as main

    importlib.reload(main)

    # Force a weak postal result + a geocode that lands at (5,5) inside DE111.
    monkeypatch.setattr(
        main,
        "lookup",
        lambda c, p: {
            "match_type": "approximate",
            "nuts1": "BE2",
            "nuts1_name": None,
            "nuts2": "BE24",
            "nuts2_name": None,
            "nuts3": "BE241",
            "nuts3_name": None,
            "nuts3_confidence": 0.4,
        },
    )
    # main._photon_client is only populated by lifespan(), which runs on
    # TestClient.__enter__ — so it must be entered before we can patch its
    # underlying httpx client with the mock transport.
    with TestClient(main.app) as c:
        main._photon_client._client = httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"features": [{"geometry": {"coordinates": [5.0, 5.0]}}]})
            )
        )
        yield c


def test_resolve_weak_geocodes(client):
    r = client.get(
        "/resolve",
        params={"country": "BE", "postal_code": "3080", "street": "Rue", "city": "X"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["resolved_via"] == "geocode"
    assert body["nuts3"] == "BE241"
    assert body["geocode"]["status"] == "ok"


def test_resolve_weak_no_address(client):
    r = client.get("/resolve", params={"country": "BE", "postal_code": "3080"})
    assert r.json()["geocode"]["status"] == "no_address"
    assert r.json()["resolved_via"] == "postal"


def test_resolve_does_not_log_address(client, caplog):
    with caplog.at_level("INFO"):
        client.get(
            "/resolve",
            params={"country": "BE", "postal_code": "3080", "street": "SecretStreet", "city": "SecretCity"},
        )
    assert "SecretStreet" not in caplog.text and "SecretCity" not in caplog.text


def test_resolve_422_does_not_echo_overlong_street(client):
    sentinel = "LEAKYSTREET" * 30
    r = client.get(
        "/resolve",
        params={"country": "BE", "postal_code": "3080", "street": sentinel},
    )
    assert r.status_code == 422
    assert sentinel not in r.text


def test_lookup_422_body_unaffected(client):
    # Missing required `country` param — no street/city loc, so the
    # validation-error handler must leave the body byte-for-byte as FastAPI's
    # default would produce it (nothing stripped, no Cache-Control header).
    r = client.get("/lookup", params={"postal_code": "10115"})
    assert r.status_code == 422
    body = r.json()
    errors = body["detail"]
    assert len(errors) == 1
    assert errors[0]["loc"] == ["query", "country"]
    assert "input" in errors[0]
    assert "Cache-Control" not in r.headers
