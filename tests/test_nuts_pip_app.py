"""Tests for app/nuts_pip.py (production point-in-polygon NUTS oracle)."""

import json

from shapely.geometry import box

from app.nuts_pip import NutsPip, load_nuts3_features

FEATURES = [
    ("AB100", box(0.0, 0.0, 1.0, 1.0)),  # lon 0..1, lat 0..1
    ("AB200", box(1.0, 0.0, 2.0, 1.0)),  # lon 1..2, lat 0..1
]


def test_point_inside_returns_hierarchy():
    oracle = NutsPip(FEATURES)
    assert oracle.lookup(0.5, 0.5) == {
        "nuts3": "AB100",
        "nuts2": "AB10",
        "nuts1": "AB1",
        "nuts0": "AB",
    }


def test_point_in_other_region():
    assert NutsPip(FEATURES).lookup(0.5, 1.5)["nuts3"] == "AB200"


def test_point_outside_returns_none():
    assert NutsPip(FEATURES).lookup(50.0, 50.0) is None


def test_load_filters_to_level_3(tmp_path):
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
    p = tmp_path / "nuts.geojson"
    p.write_text(json.dumps(geojson))
    assert [nid for nid, _ in load_nuts3_features(p)] == ["AB100"]
