"""Territory gating in data_loader.lookup(): which tiers may run, and what comes back."""

import pytest

from app import data_loader, territories


@pytest.fixture(autouse=True)
def registry():
    territories.load_territories()


# ── Territories Eurostat classifies: the full cascade runs ───────────────────

def test_outermost_region_resolves_and_is_labelled(mock_data):
    data_loader._lookup[("FR", "97400")] = "FRY40"
    data_loader._build_prefix_index()
    r = data_loader.lookup("FR", "97400")
    assert r["match_type"] == "exact"
    assert r["nuts3"] == "FRY40"
    assert r["context"]["id"] == "RE"
    assert r["context"]["status"] == "outermost_region"
    assert r["context"]["nuts_coverage"] == "full"


def test_iso_route_matches_the_parent_route(mock_data):
    data_loader._lookup[("FR", "97400")] = "FRY40"
    data_loader._build_prefix_index()
    by_parent = data_loader.lookup("FR", "97400")
    by_iso = data_loader.lookup("RE", "97400")
    assert by_iso == by_parent


# ── Territories outside NUTS: tier 1 only ────────────────────────────────────

def test_oct_returns_a_territory_only_result(mock_data):
    r = data_loader.lookup("FR", "98800")
    assert r["context"]["id"] == "NC"
    assert r["context"]["status"] == "oct"
    assert r["context"]["nuts_coverage"] == "none"
    assert r["match_type"] is None
    for field in ("nuts1", "nuts2", "nuts3", "nuts1_name", "nuts2_name", "nuts3_name",
                  "nuts1_confidence", "nuts2_confidence", "nuts3_confidence"):
        assert r[field] is None


def test_oct_never_reaches_the_monaco_prefix_chain(mock_data):
    # 98000 is Monaco, held as an exact FR row. Before the gate, 98800 prefix-matched it.
    data_loader._lookup[("FR", "98000")] = "FRL03"
    data_loader._build_prefix_index()
    r = data_loader.lookup("FR", "98800")
    assert r["nuts3"] is None
    # Monaco itself is untouched and carries no territory block.
    monaco = data_loader.lookup("FR", "98000")
    assert monaco["nuts3"] == "FRL03"
    assert monaco.get("context") is None


def test_greenland_never_reaches_the_danish_prefix_fallback(mock_data):
    data_loader._lookup[("DK", "3700")] = "DK014"
    data_loader._build_prefix_index()
    r = data_loader.lookup("DK", "3900")
    assert r["context"]["id"] == "GL"
    assert r["nuts3"] is None


def test_saint_martin_loses_its_approximation(mock_data):
    data_loader._lookup[("FR", "97100")] = "FRY10"
    data_loader._build_prefix_index()
    r = data_loader.lookup("FR", "97150")
    assert r["context"]["id"] == "MF"
    assert r["context"]["status"] == "outermost_region"
    assert r["context"]["nuts_coverage"] == "none"
    assert r["nuts3"] is None


def test_tercet_entry_only_keeps_a_genuine_eurostat_row(mock_data):
    data_loader._lookup[("FR", "97133")] = "FRY10"
    data_loader._build_prefix_index()
    r = data_loader.lookup("FR", "97133")
    assert r["context"]["id"] == "BL"
    assert r["context"]["nuts_coverage"] == "tercet_entry_only"
    assert r["match_type"] == "exact"
    assert r["nuts3"] == "FRY10"
    assert r["nuts3_confidence"] == 1.0


def test_the_islands_have_no_sibling_codes_to_answer_for(mock_data):
    # 97133 and 97150 are the whole of Saint-Barthelemy and Saint-Martin. A
    # neighbouring code is Reunion CEDEX, so it is labelled RE and resolves.
    data_loader._lookup[("FR", "97400")] = "FRY40"
    data_loader._build_prefix_index()
    r = data_loader.lookup("FR", "97705")
    assert r["context"]["id"] == "RE"
    assert r["context"]["nuts_coverage"] == "full"
    assert r["nuts3"] == "FRY40"


# ── ISO route validation ─────────────────────────────────────────────────────

def test_iso_route_rejects_a_parent_valid_code_outside_the_territory(mock_data):
    data_loader._lookup[("DK", "2100")] = "DK011"
    data_loader._build_prefix_index()
    assert data_loader.lookup("GL", "2100") is None
    # The same code still resolves normally under its own country.
    assert data_loader.lookup("DK", "2100")["nuts3"] == "DK011"


def test_iso_route_rejects_mainland_norway_under_svalbard(mock_data):
    data_loader._lookup[("NO", "0150")] = "NO084"
    data_loader._build_prefix_index()
    assert data_loader.lookup("SJ", "0150") is None
    assert data_loader.lookup("NO", "0150")["nuts3"] == "NO084"


def test_svalbard_is_in_nuts(mock_data):
    data_loader._lookup[("NO", "9170")] = "NO0B2"
    data_loader._build_prefix_index()
    r = data_loader.lookup("SJ", "9170")
    assert r["nuts3"] == "NO0B2"
    assert r["context"]["nuts_coverage"] == "full"
    assert r["context"]["status"] == "other"


# ── Territories with no postal system ────────────────────────────────────────

def test_aruba_answers_on_the_country_code_alone(mock_data):
    r = data_loader.lookup("AW", "")
    assert r["context"]["id"] == "AW"
    assert r["context"]["nuts_coverage"] == "none"
    assert r["nuts3"] is None
    assert data_loader.lookup("AW", "anything")["context"]["id"] == "AW"


def test_dutch_octs_never_reach_the_netherlands_rows(mock_data):
    # AW/CW/SX/BQ have no postal system at all. A real Dutch postal code must
    # never key into NL's TERCET data through the administering-country fallback.
    data_loader._lookup[("NL", "1012")] = "NL329"
    data_loader._build_prefix_index()
    for iso in ("AW", "CW", "SX", "BQ"):
        r = data_loader.lookup(iso, "1012")
        assert r["context"]["id"] == iso
        assert r["context"]["nuts_coverage"] == "none"
        assert r["nuts3"] is None
        assert r["match_type"] is None


# ── Tier 6 is gone ───────────────────────────────────────────────────────────

def test_crown_dependency_codes_reach_the_territory_statement(mock_data):
    # Validation runs against the UK pattern; a Jersey code must survive it and
    # come back as a territory rather than a 404.
    r = data_loader.lookup("JE", "JE2 3XP")
    assert r is not None, "UK pattern rejected a Jersey code"
    assert r["context"]["id"] == "JE"
    assert r["context"]["nuts_coverage"] == "none"


def test_faroe_islands_no_longer_return_a_fabricated_code(mock_data):
    r = data_loader.lookup("FO", "100")
    assert r["context"]["id"] == "FO"
    assert r["context"]["nuts_coverage"] == "none"
    assert r["nuts3"] is None


def test_synthetic_tier_is_removed():
    assert not hasattr(data_loader, "_synthetic_nuts")
    assert not hasattr(data_loader, "_SYNTHETIC_NAMES")


# ── Nothing else moves ───────────────────────────────────────────────────────

def test_ordinary_lookups_are_unchanged(mock_data):
    r = data_loader.lookup("DE", "10115")
    assert r["nuts3"] == "DE300"
    assert r["match_type"] == "exact"
    assert r.get("context") is None


def test_montenegro_single_nuts3_fallback_survives(mock_data):
    data_loader._single_nuts3["ME"] = "ME000"
    r = data_loader.lookup("ME", "81000")
    assert r["nuts3"] == "ME000"
    assert r.get("context") is None


def test_territory_iso_codes_are_supported_countries():
    assert {"GL", "NC", "RE", "AW", "SJ", "FO"} <= data_loader.get_loaded_countries()
