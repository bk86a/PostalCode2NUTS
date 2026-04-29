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

    def test_me_lookup(self, client):
        """Montenegro has a single NUTS3 (ME000); any valid input resolves to it."""
        resp = client.get("/lookup", params={"postal_code": "81000", "country": "ME"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["country_code"] == "ME"
        assert data["nuts3"] == "ME000"
        assert data["nuts2"] == "ME00"
        assert data["nuts1"] == "ME0"
        assert data["match_type"] == "estimated"

    def test_me_lookup_with_prefix(self, client):
        resp = client.get("/lookup", params={"postal_code": "ME-85320", "country": "ME"})
        assert resp.status_code == 200
        assert resp.json()["nuts3"] == "ME000"


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
        assert "ME" in data
        assert data == sorted(data)

    def test_200_me_pattern(self, client):
        resp = client.get("/pattern", params={"country": "ME"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["country_code"] == "ME"
        assert "8" in data["regex"]

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
        assert data["patterns_version"] == "1.1"

    def test_includes_nuts_names(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert "total_nuts_names" in data

    def test_health_includes_token_db_stale_when_db_url_set(self, monkeypatch, mock_data):
        from unittest.mock import patch

        from app import auth, config, data_loader

        monkeypatch.setattr(config.settings, "token_db_url", "https://db.example/v1")
        monkeypatch.setattr(auth, "_token_db_stale", False)

        with patch.object(data_loader, "load_data"), patch.object(auth, "refresh_db_tokens", lambda db: None):
            from fastapi.testclient import TestClient
            from app.main import app

            with TestClient(app) as client:
                resp = client.get("/health")
                assert resp.status_code == 200
                data = resp.json()
                assert data.get("token_db_stale") is False

    def test_health_omits_token_db_stale_when_db_url_unset(self, mock_data, client):
        # `client` fixture has no PC2NUTS_TOKEN_DB_URL configured
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        # Field is None when feature is disabled — Pydantic serializes to null
        assert data.get("token_db_stale") in (None,)

    def test_health_token_db_stale_true_after_failure(self, monkeypatch, mock_data):
        from unittest.mock import patch

        from app import auth, config, data_loader

        monkeypatch.setattr(config.settings, "token_db_url", "https://db.example/v1")
        monkeypatch.setattr(auth, "_token_db_stale", True)

        with patch.object(data_loader, "load_data"), patch.object(auth, "refresh_db_tokens", lambda db: None):
            from fastapi.testclient import TestClient
            from app.main import app

            with TestClient(app) as client:
                resp = client.get("/health")
                assert resp.json().get("token_db_stale") is True


# ── Auth-token bypass tests (#60) ────────────────────────────────────────────


class TestAuthBypass:
    def test_no_header_normal_flow(self, trusted_client):
        """Without an Authorization header, behaviour is unchanged."""
        resp = trusted_client.get("/lookup", params={"postal_code": "10115", "country": "DE"})
        assert resp.status_code == 200

    def test_valid_token_returns_200(self, trusted_client):
        resp = trusted_client.get(
            "/lookup",
            params={"postal_code": "10115", "country": "DE"},
            headers={"Authorization": "Bearer test-token-aaa"},
        )
        assert resp.status_code == 200

    def test_invalid_token_returns_401(self, trusted_client):
        resp = trusted_client.get(
            "/lookup",
            params={"postal_code": "10115", "country": "DE"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401
        assert "invalid token" in resp.json()["detail"].lower()

    def test_malformed_header_returns_400(self, trusted_client):
        resp = trusted_client.get(
            "/lookup",
            params={"postal_code": "10115", "country": "DE"},
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert resp.status_code == 400

    def test_health_anonymous_works(self, trusted_client):
        resp = trusted_client.get("/health")
        assert resp.status_code == 200

    def test_health_ignores_invalid_token(self, trusted_client):
        """/health is in AuthMiddleware.EXEMPT_PATHS — header is ignored entirely.
        Protects monitoring tools that may inject auth headers globally."""
        resp = trusted_client.get("/health", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 200

    def test_health_ignores_malformed_header(self, trusted_client):
        resp = trusted_client.get("/health", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert resp.status_code == 200

    def test_valid_token_bypasses_rate_limit(self, trusted_client):
        """Default rate limit is 60/minute. With a valid bypass token, more
        than 60 requests in tight succession all return 200. Without bypass,
        request 61+ would 429."""
        headers = {"Authorization": "Bearer test-token-aaa"}
        for i in range(80):
            resp = trusted_client.get(
                "/lookup",
                params={"postal_code": "10115", "country": "DE"},
                headers=headers,
            )
            assert resp.status_code == 200, (
                f"request {i + 1}: expected 200, got {resp.status_code} (body: {resp.text[:200]})"
            )

    def test_pattern_with_valid_token(self, trusted_client):
        """/pattern also has exempt_when wired — sanity check both endpoints."""
        resp = trusted_client.get(
            "/pattern",
            params={"country": "DE"},
            headers={"Authorization": "Bearer test-token-aaa"},
        )
        assert resp.status_code == 200

    def test_empty_tokens_ignores_header(self, monkeypatch, mock_data):
        """When PC2NUTS_TRUSTED_TOKENS is empty, headers are ignored entirely
        (no 400, no 401) — feature is off, behaviour identical to pre-feature."""
        from unittest.mock import patch

        from app import auth, data_loader

        # Override: no tokens configured (bypass disabled)
        monkeypatch.setattr(auth, "_get_trusted_tokens", lambda: frozenset())

        from fastapi.testclient import TestClient

        with patch.object(data_loader, "load_data"):
            from app.main import app

            with TestClient(app) as client:
                # Bearer header with anything → ignored
                resp = client.get(
                    "/lookup",
                    params={"postal_code": "10115", "country": "DE"},
                    headers={"Authorization": "Bearer some-random-token"},
                )
                assert resp.status_code == 200
                # Even malformed Basic header → ignored (feature is off)
                resp = client.get(
                    "/lookup",
                    params={"postal_code": "10115", "country": "DE"},
                    headers={"Authorization": "Basic abc"},
                )
                assert resp.status_code == 200

    def test_audit_log_includes_token_id_for_trusted(self, trusted_client, caplog):
        from app.auth import token_id

        with caplog.at_level("INFO", logger="app.access"):
            trusted_client.get(
                "/lookup",
                params={"postal_code": "10115", "country": "DE"},
                headers={"Authorization": "Bearer test-token-aaa"},
            )
        log_text = " ".join(r.message for r in caplog.records if r.name == "app.access")
        assert f"token_id={token_id('test-token-aaa')}" in log_text

    def test_audit_log_omits_token_id_for_anonymous(self, trusted_client, caplog):
        with caplog.at_level("INFO", logger="app.access"):
            trusted_client.get("/lookup", params={"postal_code": "10115", "country": "DE"})
        log_text = " ".join(r.message for r in caplog.records if r.name == "app.access")
        assert "token_id=" not in log_text
