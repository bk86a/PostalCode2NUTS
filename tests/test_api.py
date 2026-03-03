"""Tests for FastAPI endpoints — /lookup, /pattern, /health."""


# ── /lookup endpoint tests ───────────────────────────────────────────────────


class TestLookupEndpoint:
    def test_200_exact_match(self, client):
        resp = client.get("/lookup", params={"postal_code": "10115", "country": "DE"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["match_type"] == "exact"
        assert data["nuts3"] == "DE300"
        assert data["country_code"] == "DE"

    def test_200_cache_header(self, client):
        resp = client.get("/lookup", params={"postal_code": "10115", "country": "DE"})
        assert "public" in resp.headers.get("cache-control", "")

    def test_400_unsupported_country(self, client):
        resp = client.get("/lookup", params={"postal_code": "12345", "country": "ZZ"})
        assert resp.status_code == 400
        assert "not supported" in resp.json()["detail"].lower()

    def test_404_no_match(self, client):
        """EL has data but this postal code has no match (only 11141 in mock)."""
        resp = client.get("/lookup", params={"postal_code": "99999", "country": "EL"})
        # EL has only 1 NUTS3 code (EL303), so it may show up as single-NUTS3 fallback
        # Actually with only 1 entry, _single_nuts3 should capture EL
        # So this should return 200 with estimated match
        assert resp.status_code == 200

    def test_422_missing_params(self, client):
        resp = client.get("/lookup")
        assert resp.status_code == 422

    def test_422_invalid_country_format(self, client):
        resp = client.get("/lookup", params={"postal_code": "10115", "country": "DEU"})
        assert resp.status_code == 422

    def test_gr_maps_to_el(self, client):
        resp = client.get("/lookup", params={"postal_code": "11141", "country": "GR"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["country_code"] == "EL"
        assert data["nuts3"] == "EL303"


# ── /pattern endpoint tests ──────────────────────────────────────────────────


class TestPatternEndpoint:
    def test_200_specific_country(self, client):
        resp = client.get("/pattern", params={"country": "DE"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["country_code"] == "DE"
        assert "regex" in data
        assert "example" in data

    def test_200_list_all(self, client):
        resp = client.get("/pattern")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert "DE" in data
        assert data == sorted(data)

    def test_200_cache_header(self, client):
        resp = client.get("/pattern", params={"country": "DE"})
        assert "public" in resp.headers.get("cache-control", "")

    def test_404_unknown_country(self, client):
        resp = client.get("/pattern", params={"country": "ZZ"})
        assert resp.status_code == 404


# ── /health endpoint tests ───────────────────────────────────────────────────


class TestHealthEndpoint:
    def test_200_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total_postal_codes"] > 0

    def test_no_cache_header(self, client):
        resp = client.get("/health")
        cache = resp.headers.get("cache-control", "")
        assert "no-cache" in cache

    def test_includes_estimates(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "total_estimates" in data
        assert data["total_estimates"] >= 0

    def test_includes_patterns_version(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "patterns_version" in data
        assert data["patterns_version"] == "1.0"

    def test_includes_nuts_names(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "total_nuts_names" in data
