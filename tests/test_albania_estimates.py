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


def test_merge_preserves_crlf(tmp_path):
    csv_path = tmp_path / "est.csv"
    csv_path.write_bytes(
        b"COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\r\n"
        b"AT,1010,AT130,AT13,AT1,high\r\n"
    )
    al_rows = build.rows_from_geonames([["AL", "1001", "T", "Tirana", "40"]])
    build.merge_into_csv(csv_path, al_rows)
    data = csv_path.read_bytes()
    assert b"\r\nAL,1001,AL022,AL02,AL0,high\r\n" in data
    assert data.endswith(b"AT,1010,AT130,AT13,AT1,high\r\n")
    assert data.count(b"\r\n") == 3


def test_merge_preserves_lf(tmp_path):
    csv_path = tmp_path / "est.csv"
    csv_path.write_bytes(
        b"COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\n"
        b"AT,1010,AT130,AT13,AT1,high\n"
    )
    al_rows = build.rows_from_geonames([["AL", "1001", "T", "Tirana", "40"]])
    build.merge_into_csv(csv_path, al_rows)
    data = csv_path.read_bytes()
    assert b"\r\n" not in data
    assert b"AL,1001,AL022,AL02,AL0,high\n" in data
