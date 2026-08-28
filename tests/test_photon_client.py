"""Tests for app/photon_client.py."""

import httpx2 as httpx

from app.photon_client import PhotonClient


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_primary_query_is_street_city_not_postcode():
    # The first query must be street+city — NOT street+postcode+city (which Photon
    # returns 0 results for). Postcode is never combined with the street.
    calls = []

    def handler(req):
        calls.append(req.url.params["q"])
        return httpx.Response(
            200, json={"features": [{"geometry": {"type": "Point", "coordinates": [4.5149, 50.8246]}}]}
        )

    pc = PhotonClient("http://photon", _client(handler))
    assert pc.geocode("Markt", "Tervuren", "3080") == (50.8246, 4.5149)
    assert calls[0] == "Markt, Tervuren"  # street+city first, no postcode


def test_falls_back_to_postcode_city_when_street_query_empty():
    def handler(req):
        q = req.url.params["q"]
        if q == "Museumpark 25, Rotterdam":  # street+city → empty (the real-world failure)
            return httpx.Response(200, json={"features": []})
        if q == "3000 AE Rotterdam":  # postcode+city fallback → hit
            return httpx.Response(200, json={"features": [{"geometry": {"coordinates": [4.47, 51.92]}}]})
        return httpx.Response(200, json={"features": []})

    pc = PhotonClient("http://photon", _client(handler))
    assert pc.geocode("Museumpark 25", "Rotterdam", "3000 AE") == (51.92, 4.47)


def test_falls_back_to_city_when_street_and_postcode_empty():
    def handler(req):
        q = req.url.params["q"]
        if q == "Rotterdam":
            return httpx.Response(200, json={"features": [{"geometry": {"coordinates": [4.48, 51.92]}}]})
        return httpx.Response(200, json={"features": []})

    pc = PhotonClient("http://photon", _client(handler))
    assert pc.geocode("Nowhere St 9", "Rotterdam", "0000 ZZ") == (51.92, 4.48)


def test_never_combines_street_and_postcode():
    seen = []

    def handler(req):
        seen.append(req.url.params["q"])
        return httpx.Response(200, json={"features": []})

    pc = PhotonClient("http://photon", _client(handler))
    pc.geocode("Museumpark 25", "Rotterdam", "3000 AE")
    assert seen, "expected at least one query"
    assert all(not ("Museumpark 25" in q and "3000 AE" in q) for q in seen)


def test_never_combines_street_and_postcode_without_city():
    # The no-city path (reachable via the offline enrich path) must also never
    # put street and postcode in one query — Photon returns nothing for that pair.
    seen = []

    def handler(req):
        seen.append(req.url.params["q"])
        return httpx.Response(200, json={"features": []})

    pc = PhotonClient("http://photon", _client(handler))
    pc.geocode("Museumpark 25", "", "3000 AE")
    assert seen, "expected at least one query"
    assert all(not ("Museumpark 25" in q and "3000 AE" in q) for q in seen)


def test_non_point_geometry_returns_none():
    # A non-Point feature has nested coordinates (e.g. [[lon,lat],...]); parsing
    # coords[1] as a float would raise TypeError. The contract is "never raises".
    def handler(req):
        geom = {"type": "LineString", "coordinates": [[4.5, 50.8], [4.6, 50.9]]}
        return httpx.Response(200, json={"features": [{"geometry": geom}]})

    pc = PhotonClient("http://photon", _client(handler))
    assert pc.geocode("Markt", "Tervuren", "3080") is None


def test_all_queries_empty_returns_none():
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


def test_oversized_geocoder_response_is_abandoned():
    """/resolve buffers the geocoder body on every request — a Photon that has
    been replaced or has gone wrong must not be able to exhaust the worker."""
    from app import photon_client

    def handler(req):
        return httpx.Response(200, content=b"x" * (photon_client._MAX_RESPONSE_BYTES + 1))

    pc = PhotonClient("http://photon", _client(handler))
    assert pc.geocode("Markt", "Tervuren", "3080") is None


def test_response_just_under_the_cap_still_parses():
    """The cap must not clip a legitimate response."""
    from app import photon_client

    feature = {"geometry": {"type": "Point", "coordinates": [4.5149, 50.8246]}}
    padding = "y" * 1024  # well under the ceiling, but not trivially small

    def handler(req):
        return httpx.Response(200, json={"features": [feature], "note": padding})

    pc = PhotonClient("http://photon", _client(handler))
    assert pc.geocode("Markt", "Tervuren", "3080") == (50.8246, 4.5149)
    assert photon_client._MAX_RESPONSE_BYTES > len(padding)
