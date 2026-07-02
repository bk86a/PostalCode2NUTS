"""Tests for scripts/geonames_coords.py (GeoNames centroid loader)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "geonames_coords",
    Path(__file__).resolve().parent.parent / "scripts" / "geonames_coords.py",
)
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)


# GeoNames postal dump columns (tab-separated):
# 0 cc 1 pc 2 place 3 a1n 4 a1c 5 a2n 6 a2c 7 a3n 8 a3c 9 lat 10 lon 11 acc
SAMPLE = "\n".join(
    [
        "\t".join(["DE", "10115", "Berlin", "", "", "", "", "", "", "52.0", "13.0", "4"]),
        "\t".join(["DE", "10115", "Berlin b", "", "", "", "", "", "", "54.0", "13.0", "4"]),
        "\t".join(["de", "60311", "Frankfurt", "", "", "", "", "", "", "50.1", "8.7", "4"]),
        "\t".join(["NL", "1011 AB", "Amsterdam", "", "", "", "", "", "", "52.3", "4.9", "1"]),
        "\t".join(["FR", "75001", "Paris", "", "", "", "", "", "", "", "", "1"]),  # no coord
    ]
)


def test_averages_duplicate_postcodes(tmp_path):
    f = tmp_path / "geo.txt"
    f.write_text(SAMPLE + "\n", encoding="utf-8")
    coords = gc.load_geonames_coords([f])
    # two DE 10115 rows: lat averaged (52+54)/2 = 53.0, lon 13.0
    assert coords[("DE", "10115")] == (53.0, 13.0)


def test_uppercases_country_and_normalizes_pc(tmp_path):
    f = tmp_path / "geo.txt"
    f.write_text(SAMPLE + "\n", encoding="utf-8")
    coords = gc.load_geonames_coords([f])
    assert ("DE", "60311") in coords  # lowercase 'de' → 'DE'
    assert ("NL", "1011AB") in coords  # space stripped from postcode


def test_rows_without_coordinates_are_skipped(tmp_path):
    f = tmp_path / "geo.txt"
    f.write_text(SAMPLE + "\n", encoding="utf-8")
    coords = gc.load_geonames_coords([f])
    assert ("FR", "75001") not in coords


def test_normalize_pc():
    assert gc._normalize_pc(" 1011 ab ") == "1011AB"


def test_normalize_cc_remaps_country_key(tmp_path):
    # A GR (ISO) row; the app's canonical convention is EL. With a normalizer
    # that maps GR→EL, the key is stored under the canonical code.
    f = tmp_path / "geo.txt"
    row = ["GR", "10431", "Athina", "", "", "", "", "", "", "37.98", "23.72", "4"]
    f.write_text("\t".join(row) + "\n", encoding="utf-8")

    def norm(c):
        return "EL" if c.strip().upper() == "GR" else c.strip().upper()

    coords = gc.load_geonames_coords([f], normalize_cc=norm)
    assert ("EL", "10431") in coords
    assert ("GR", "10431") not in coords
