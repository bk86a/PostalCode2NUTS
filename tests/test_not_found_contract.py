"""The 200-not-found contract.

A well-formed query against a served route always answers 200, whether or not
the service holds data for it. 404 is reserved for a genuinely unknown route.
"""

import pytest


class TestLookupNotFound:
    def test_unserved_country_is_200(self, client):
        r = client.get("/lookup", params={"country": "ZZ", "postal_code": "12345"})
        assert r.status_code == 200
        body = r.json()
        assert body["found"] is False
        assert body["message"]
        assert body["country_code"] == "ZZ"
        assert body["postal_code"] == "12345"

    def test_unserved_country_message_lists_what_is_available(self, client):
        body = client.get("/lookup", params={"country": "ZZ", "postal_code": "1"}).json()
        assert "Available countries" in body["message"]
        assert "DE" in body["message"]

    def test_not_found_body_has_every_data_field_null(self, client):
        body = client.get("/lookup", params={"country": "FO", "postal_code": "1234"}).json()
        assert body["found"] is False
        for field in (
            "match_type",
            "nuts1",
            "nuts1_name",
            "nuts1_confidence",
            "nuts2",
            "nuts2_name",
            "nuts2_confidence",
            "nuts3",
            "nuts3_name",
            "nuts3_confidence",
            "context",
        ):
            assert body[field] is None, field

    def test_hit_is_found_with_no_message(self, client):
        body = client.get("/lookup", params={"country": "DE", "postal_code": "10115"}).json()
        assert body["found"] is True
        assert body["message"] is None
        assert body["nuts3"] == "DE300"

    def test_not_found_is_still_cacheable(self, client):
        r = client.get("/lookup", params={"country": "ZZ", "postal_code": "12345"})
        assert "public" in r.headers.get("cache-control", "")

    def test_no_response_is_a_404(self, client):
        for params in (
            {"country": "ZZ", "postal_code": "12345"},
            {"country": "FO", "postal_code": "1234"},
            {"country": "DE", "postal_code": "10115"},
        ):
            assert client.get("/lookup", params=params).status_code != 404


class TestPatternNotFound:
    def test_unknown_country_is_200(self, client):
        r = client.get("/pattern", params={"country": "ZZ"})
        assert r.status_code == 200
        body = r.json()
        assert body["found"] is False
        assert body["country_code"] == "ZZ"
        assert body["regex"] is None and body["example"] is None
        assert body["message"]

    def test_hit_is_found_with_no_message(self, client):
        body = client.get("/pattern", params={"country": "DE"}).json()
        assert body["found"] is True
        assert body["message"] is None
        assert body["regex"]


class TestGenuine404:
    """404 survives where it is the honest answer: the route does not exist."""

    @pytest.mark.parametrize("path", ["/nope", "/lookup/extra", "/pattern/DE", "/v2/lookup"])
    def test_unknown_route_still_404s(self, client, path):
        assert client.get(path).status_code == 404


class TestResolveNotFound:
    def test_nothing_resolves_is_200_found_false(self, client, monkeypatch):
        import app.main as main

        monkeypatch.setattr(main, "lookup", lambda c, p: None)
        r = client.get("/resolve", params={"country": "DE", "postal_code": "99999"})
        assert r.status_code == 200
        body = r.json()
        assert body["found"] is False
        assert body["resolved_via"] == "none"
        assert body["message"]
        assert body["nuts3"] is None
