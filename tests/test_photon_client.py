"""Tests for app/photon_client.py."""

import httpx

from app.photon_client import PhotonClient


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_geocode_returns_lat_lon():
    def handler(req):
        assert req.url.params["q"] == "Markt, 3080 Tervuren"
        return httpx.Response(
            200, json={"features": [{"geometry": {"type": "Point", "coordinates": [4.5149, 50.8246]}}]}
        )

    pc = PhotonClient("http://photon", _client(handler))
    assert pc.geocode("Markt", "Tervuren", "3080") == (50.8246, 4.5149)


def test_empty_features_returns_none():
    pc = PhotonClient("http://photon", _client(lambda r: httpx.Response(200, json={"features": []})))
    assert pc.geocode("X", "Y", "0000") is None


def test_http_error_returns_none():
    pc = PhotonClient("http://photon", _client(lambda r: httpx.Response(500)))
    assert pc.geocode("X", "Y", "0000") is None


def test_transport_error_returns_none():
    def boom(req):
        raise httpx.ConnectError("down", request=req)

    pc = PhotonClient("http://photon", _client(boom))
    assert pc.geocode("X", "Y", "0000") is None


def test_blank_query_returns_none():
    pc = PhotonClient("http://photon", _client(lambda r: httpx.Response(200, json={"features": []})))
    assert pc.geocode(None, None, None) is None
