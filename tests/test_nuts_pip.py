"""Tests for scripts/nuts_pip.py (point-in-polygon NUTS oracle)."""

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from shapely.geometry import box

_spec = importlib.util.spec_from_file_location(
    "nuts_pip",
    Path(__file__).resolve().parent.parent / "scripts" / "nuts_pip.py",
)
nuts_pip = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nuts_pip)


# Two adjacent unit squares in lon/lat space, as NUTS-3 regions.
FEATURES = [
    ("AB100", box(0.0, 0.0, 1.0, 1.0)),  # lon 0..1, lat 0..1
    ("AB200", box(1.0, 0.0, 2.0, 1.0)),  # lon 1..2, lat 0..1
]


def test_point_inside_returns_hierarchy():
    oracle = nuts_pip.NutsPip(FEATURES)
    # lookup(lat, lon) → point (lon=0.5, lat=0.5) is inside AB100
    assert oracle.lookup(0.5, 0.5) == {
        "nuts3": "AB100",
        "nuts2": "AB10",
        "nuts1": "AB1",
        "nuts0": "AB",
    }


def test_point_in_other_region():
    oracle = nuts_pip.NutsPip(FEATURES)
    assert oracle.lookup(0.5, 1.5)["nuts3"] == "AB200"  # lon=1.5


def test_point_outside_all_regions_returns_none():
    oracle = nuts_pip.NutsPip(FEATURES)
    assert oracle.lookup(50.0, 50.0) is None


def test_load_nuts3_features_filters_to_level_3(tmp_path):
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NUTS_ID": "AB", "LEVL_CODE": 0},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 1], [0, 1], [0, 0]]]},
            },
            {
                "type": "Feature",
                "properties": {"NUTS_ID": "AB100", "LEVL_CODE": 3},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
            },
        ],
    }
    path = tmp_path / "nuts.geojson"
    path.write_text(json.dumps(geojson))
    features = nuts_pip.load_nuts3_features(path)
    assert [nid for nid, _ in features] == ["AB100"]  # level-0 dropped


def test_load_nuts3_features_from_zip_picks_region_not_boundary(tmp_path):
    rg = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NUTS_ID": "AB100", "LEVL_CODE": 3},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
            }
        ],
    }
    bn = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NUTS_ID": "AB100", "LEVL_CODE": 3},
                # deliberately does NOT pass through (0.5, 0.5), so a wrong
                # (boundary) selection would make the PIP lookup below miss.
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]},
            }
        ],
    }
    zpath = tmp_path / "ref-nuts.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        # write BN first so a naive `next()` would wrongly pick it
        zf.writestr("NUTS_BN_01M_2024_4326_LEVL_3.geojson", json.dumps(bn))
        zf.writestr("NUTS_RG_01M_2024_4326_LEVL_3.geojson", json.dumps(rg))

    features = nuts_pip.load_nuts3_features(zpath)
    assert [nid for nid, _ in features] == ["AB100"]

    # the geometry must be the polygon (region), not the boundary linestring
    assert features[0][1].geom_type == "Polygon"

    # and PIP must actually work: a boundary-only geometry would miss here.
    oracle = nuts_pip.NutsPip(features)
    assert oracle.lookup(0.5, 0.5)["nuts3"] == "AB100"


def test_load_nuts3_features_zip_without_region_raises(tmp_path):
    bn = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NUTS_ID": "AB100", "LEVL_CODE": 3},
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            }
        ],
    }
    zpath = tmp_path / "no-rg.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("NUTS_BN_01M_2024_4326_LEVL_3.geojson", json.dumps(bn))

    with pytest.raises(ValueError):
        nuts_pip.load_nuts3_features(zpath)
