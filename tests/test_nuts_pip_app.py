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


# AB100 (lon 0..1) and a foreign FR100 (lon -1..-0.02); a gap in between.
SNAP_FEATURES = [
    ("AB100", box(0.0, 0.0, 1.0, 1.0)),
    ("FR100", box(-1.0, 0.0, -0.02, 1.0)),
]


def test_nearest_snaps_point_just_outside():
    oracle = NutsPip(SNAP_FEATURES)
    # (lat=0.5, lon=-0.015) is in the gap, ~0.55 km from FR100, ~1.67 km from AB100
    hit = oracle.nearest(0.5, -0.015, max_km=3.0)
    assert hit["nuts3"] == "FR100"  # nearest overall
    assert 0.4 < hit["snap_km"] < 0.7


def test_nearest_respects_country_filter():
    oracle = NutsPip(SNAP_FEATURES)
    # nearest is FR100, but restricting to country AB must pick the farther AB100
    hit = oracle.nearest(0.5, -0.015, max_km=3.0, country="AB")
    assert hit["nuts3"] == "AB100"
    assert hit["nuts2"] == "AB10" and hit["nuts0"] == "AB"


def test_nearest_returns_none_beyond_cap():
    oracle = NutsPip(SNAP_FEATURES)
    assert oracle.nearest(0.5, -0.015, max_km=1.0, country="AB") is None  # AB100 ~1.67 km


def test_nearest_disabled_when_cap_zero():
    oracle = NutsPip(SNAP_FEATURES)
    assert oracle.nearest(0.5, -0.015, max_km=0.0) is None


def test_nearest_none_when_no_same_country_region():
    oracle = NutsPip(SNAP_FEATURES)
    assert oracle.nearest(0.5, -0.015, max_km=3.0, country="ZZ") is None


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
