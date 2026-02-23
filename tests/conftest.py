"""Shared fixtures for PostalCode2NUTS tests."""

from unittest.mock import patch

import pytest

from app import data_loader


# ── Minimal mock TERCET data ─────────────────────────────────────────────────
# DE: 3 entries → DE300, DE600  (tests exact + approximate via prefix)
# AT: 3 entries → AT130        (tests exact)
# EL: 1 entry  → EL303        (tests GR→EL mapping)
# FR: 1 estimate entry         (tests tier 2)
# XX: 2 entries → XX000        (single NUTS3, tests tier 5)
# YY: 4 entries → YY111 (3) + YY112 (1)  (unanimous NUTS1/2, dominant NUTS3, tests tier 4)

MOCK_LOOKUP = {
    ("DE", "10115"): "DE300",
    ("DE", "60311"): "DE712",
    ("DE", "10117"): "DE300",
    ("AT", "1010"): "AT130",
    ("AT", "1020"): "AT130",
    ("AT", "1030"): "AT130",
    ("EL", "11141"): "EL303",
    ("XX", "0001"): "XX000",
    ("XX", "0002"): "XX000",
    ("YY", "1001"): "YY111",
    ("YY", "1002"): "YY111",
    ("YY", "1003"): "YY111",
    ("YY", "2001"): "YY112",
}

MOCK_ESTIMATES = {
    ("FR", "97105"): {
        "nuts3": "FRY10",
        "nuts2": "FRY1",
        "nuts1": "FRY",
        "nuts3_confidence": 0.90,
        "nuts2_confidence": 0.95,
        "nuts1_confidence": 0.98,
    },
}

MOCK_NUTS_NAMES = {
    "DE3": "Berlin",
    "DE30": "Berlin",
    "DE300": "Berlin",
    "DE7": "Hessen",
    "DE71": "Darmstadt",
    "DE712": "Frankfurt am Main, Kreisfreie Stadt",
    "AT1": "Ostösterreich",
    "AT13": "Wien",
    "AT130": "Wien",
    "EL3": "Attiki",
    "EL30": "Attiki",
    "EL303": "Kentrikos Tomeas Athinon",
    "FRY": "Départements d'outre-mer",
    "FRY1": "Guadeloupe",
    "FRY10": "Guadeloupe",
    "XX0": "XX Region",
    "XX00": "XX Sub-Region",
    "XX000": "XX District",
    "YY1": "YY Region",
    "YY11": "YY Sub-Region",
    "YY111": "YY District A",
    "YY112": "YY District B",
}


@pytest.fixture()
def mock_data():
    """Populate data_loader module globals with minimal test data.

    Calls _build_prefix_index() to set up _prefix_index, _single_nuts3,
    and _country_fallback. Restores original state on teardown.
    """
    # Save originals
    orig_lookup = data_loader._lookup.copy()
    orig_estimates = data_loader._estimates.copy()
    orig_names = data_loader._nuts_names.copy()
    orig_prefix = {k: dict(v) for k, v in data_loader._prefix_index.items()}
    orig_single = data_loader._single_nuts3.copy()
    orig_fallback = data_loader._country_fallback.copy()

    # Populate
    data_loader._lookup.clear()
    data_loader._lookup.update(MOCK_LOOKUP)
    data_loader._estimates.clear()
    data_loader._estimates.update(MOCK_ESTIMATES)
    data_loader._nuts_names.clear()
    data_loader._nuts_names.update(MOCK_NUTS_NAMES)
    data_loader._build_prefix_index()

    yield

    # Restore
    data_loader._lookup.clear()
    data_loader._lookup.update(orig_lookup)
    data_loader._estimates.clear()
    data_loader._estimates.update(orig_estimates)
    data_loader._nuts_names.clear()
    data_loader._nuts_names.update(orig_names)
    data_loader._prefix_index.clear()
    data_loader._prefix_index.update(orig_prefix)
    data_loader._single_nuts3.clear()
    data_loader._single_nuts3.update(orig_single)
    data_loader._country_fallback.clear()
    data_loader._country_fallback.update(orig_fallback)


@pytest.fixture()
def client(mock_data):
    """FastAPI TestClient with mock data loaded (load_data patched out)."""
    from fastapi.testclient import TestClient

    with patch.object(data_loader, "load_data"):
        from app.main import app

        with TestClient(app) as tc:
            yield tc
