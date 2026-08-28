"""Endpoint behaviour for outermost regions, OCTs and other non-NUTS territories."""

import re

from app import data_loader


def test_oct_returns_200_with_a_territory_block(client):
    r = client.get("/lookup", params={"country": "NC", "postal_code": "98800"})
    assert r.status_code == 200
    body = r.json()
    assert body["territory"]["name"] == "New Caledonia"
    assert body["territory"]["status"] == "oct"
    assert body["territory"]["nuts_coverage"] == "none"
    assert body["territory"]["legal_basis"] == "TFEU Part Four, Annex II"
    assert body["nuts3"] is None
    assert body["match_type"] is None


def test_both_routes_agree_apart_from_the_echoed_country(client):
    a = client.get("/lookup", params={"country": "NC", "postal_code": "98800"}).json()
    b = client.get("/lookup", params={"country": "FR", "postal_code": "98800"}).json()
    assert a["country_code"] == "NC"
    assert b["country_code"] == "FR"
    a.pop("country_code"), b.pop("country_code")
    assert a == b


def test_code_outside_the_named_territory_is_a_404_naming_the_territory(client, mock_data):
    data_loader._lookup[("DK", "2100")] = "DK011"
    data_loader._build_prefix_index()
    r = client.get("/lookup", params={"country": "GL", "postal_code": "2100"})
    assert r.status_code == 404
    assert "Greenland" in r.json()["detail"]
    assert "DK011" not in r.json()["detail"]


def test_malformed_territory_code_is_a_404(client):
    r = client.get("/lookup", params={"country": "NC", "postal_code": "12345"})
    assert r.status_code == 404


def test_territory_without_a_postal_system_answers_on_the_country_code(client):
    r = client.get("/lookup", params={"country": "AW", "postal_code": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["territory"]["name"] == "Aruba"
    assert "no postal codes" in body["territory"]["note"]


def test_outermost_region_keeps_its_nuts_codes(client, mock_data):
    data_loader._lookup[("FR", "97400")] = "FRY40"
    data_loader._build_prefix_index()
    body = client.get("/lookup", params={"country": "RE", "postal_code": "97400"}).json()
    assert body["nuts3"] == "FRY40"
    assert body["territory"]["status"] == "outermost_region"
    assert body["territory"]["nuts_coverage"] == "full"


def test_ordinary_lookup_has_a_null_territory(client):
    body = client.get("/lookup", params={"country": "DE", "postal_code": "10115"}).json()
    assert body["territory"] is None
    assert body["nuts3"] == "DE300"


def test_unsupported_country_still_400s(client):
    r = client.get("/lookup", params={"country": "BR", "postal_code": "01000"})
    assert r.status_code == 400


def test_pattern_narrows_to_the_territorys_own_range_when_it_is_in_nuts(client):
    """A territory Eurostat classifies advertises its own range, not the parent's:
    FR's pattern would validate Paris's 75001 as a Réunion code."""
    body = client.get("/pattern", params={"country": "RE"}).json()
    assert body["country_code"] == "RE"
    assert body["regex"] != client.get("/pattern", params={"country": "FR"}).json()["regex"]
    assert re.match(body["regex"], "97400")
    assert not re.match(body["regex"], "75001")


def test_pattern_keeps_the_parent_pattern_for_a_territory_outside_nuts(client):
    """Deliberate scope: only the NUTS-linked territories are narrowed for now."""
    body = client.get("/pattern", params={"country": "NC"}).json()
    assert body["regex"] == client.get("/pattern", params={"country": "FR"}).json()["regex"]


def test_pattern_404s_for_a_territory_with_no_postal_system(client):
    r = client.get("/pattern", params={"country": "AW"})
    assert r.status_code == 404
    assert "no postal codes" in r.json()["detail"]


def test_health_reports_the_registry_size(client):
    assert client.get("/health").json()["territories"] == 26
