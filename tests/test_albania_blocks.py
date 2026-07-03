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


def test_unallocated_prefixes_return_none():
    # A code whose 2-digit district prefix is not allocated to any district does
    # not exist — return None rather than inventing a confident region (#118).
    assert resolve_al_block("1900") is None  # prefix 19 — no district
    assert resolve_al_block("2100") is None  # prefix 21 — between Durres(20)/Kavaje(25)
    assert resolve_al_block("1250") is None  # prefix 12 — between Tirana(10)/Kruje(15)
    assert resolve_al_block("9800") is None  # prefix 98 — above Sarande(97)
    assert resolve_al_block("9999") is None  # prefix 99 — no district


def test_within_district_range_still_resolves():
    # Any code inside an allocated district's 2-digit space resolves to that
    # district — the block scheme is authoritative at district granularity.
    assert resolve_al_block("1099") == "AL022"  # still prefix 10 (Tirana)
    assert resolve_al_block("7099") == "AL034"  # still prefix 70 (Korce)


def test_malformed_and_out_of_range_return_none():
    assert resolve_al_block("100") is None  # too short
    assert resolve_al_block("10011") is None  # too long
    assert resolve_al_block("10AB") is None  # non-digit
    assert resolve_al_block("0999") is None  # prefix 09 — no district
    assert resolve_al_block("") is None
