"""Tests for app/albania_blocks.py."""

from app.albania_blocks import BLOCKS, SUPPORTED, resolve_al_block


def test_supported_is_al_only():
    assert SUPPORTED == frozenset({"AL"})


def test_blocks_sorted_and_valid():
    codes = [c for c, _, _ in BLOCKS]
    assert codes == sorted(codes), "BLOCKS must be ascending by code"
    for _, nuts3, name in BLOCKS:
        assert len(nuts3) == 5 and nuts3.startswith("AL"), nuts3
        assert name


def test_known_district_centers_resolve():
    # District-center codes map to their qark's NUTS3.
    assert resolve_al_block("1001") == "AL022"  # Tirana
    assert resolve_al_block("2001") == "AL012"  # Durres
    assert resolve_al_block("9401") == "AL035"  # Vlore


def test_non_obvious_blocks():
    # The two assignments confirmed against GeoNames' own tagging.
    assert resolve_al_block("1501") == "AL012"  # Kruje -> Durres qark
    assert resolve_al_block("2501") == "AL022"  # Kavaje -> Tirana qark


def test_gap_codes_resolve_not_none():
    # Codes GeoNames omits (issue #118) still resolve via their block.
    assert resolve_al_block("1055") == "AL022"
    assert resolve_al_block("1065") == "AL022"
    assert resolve_al_block("3350") == "AL021"  # Gramsh block (GeoNames has none)
    assert resolve_al_block("6450") == "AL033"  # Permet block (GeoNames has none)


def test_service_codes_fold_into_tirana():
    assert resolve_al_block("1700") == "AL022"  # Transit
    assert resolve_al_block("1800") == "AL022"  # EMS


def test_top_open_range_maps_to_sarande():
    assert resolve_al_block("9800") == "AL035"
    assert resolve_al_block("9999") == "AL035"


def test_malformed_and_out_of_range_return_none():
    assert resolve_al_block("100") is None  # too short
    assert resolve_al_block("10011") is None  # too long
    assert resolve_al_block("10AB") is None  # non-digit
    assert resolve_al_block("0999") is None  # below the lowest block
    assert resolve_al_block("") is None
