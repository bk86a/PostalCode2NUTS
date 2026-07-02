"""Tests for scripts/build_albania_estimates.py (pure transform)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "build_albania_estimates",
    Path(__file__).resolve().parent.parent / "scripts" / "build_albania_estimates.py",
)
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


# GeoNames columns: country, postalcode, place, admin1name, admin1code, ...
SAMPLE = [
    ["AL", "1001", "Tirane", "Tirana", "40"],
    ["AL", "1001", "Tirane dup", "Tirana", "40"],  # duplicate PC, same qark
    ["AL", "5001", "Qender Berat", "Qarku i Beratit", "40"],
    ["AL", "9401", "Vlore", "Qarku i Vlorës", "50"],
]


def test_maps_qark_to_nuts3_and_derives_levels():
    rows = build.rows_from_geonames(SAMPLE)
    by_pc = {r["POSTAL_CODE"]: r for r in rows}
    assert by_pc["1001"]["ESTIMATED_NUTS3"] == "AL022"
    assert by_pc["1001"]["ESTIMATED_NUTS2"] == "AL02"
    assert by_pc["1001"]["ESTIMATED_NUTS1"] == "AL0"
    assert by_pc["5001"]["ESTIMATED_NUTS3"] == "AL031"
    assert by_pc["9401"]["ESTIMATED_NUTS3"] == "AL035"
    assert all(r["COUNTRY_CODE"] == "AL" for r in rows)
    assert all(r["CONFIDENCE"] == "high" for r in rows)


def test_dedupes_by_postal_code():
    rows = build.rows_from_geonames(SAMPLE)
    pcs = [r["POSTAL_CODE"] for r in rows]
    assert len(pcs) == len(set(pcs)) == 3


def test_sorted_by_postal_code():
    rows = build.rows_from_geonames(SAMPLE)
    pcs = [r["POSTAL_CODE"] for r in rows]
    assert pcs == sorted(pcs)


def test_unmapped_qark_raises():
    import pytest

    with pytest.raises(ValueError):
        build.rows_from_geonames([["AL", "1001", "X", "Unknown County", "40"]])


def test_skips_non_four_digit():
    rows = build.rows_from_geonames([["AL", "100", "X", "Tirana", "40"]])
    assert rows == []
