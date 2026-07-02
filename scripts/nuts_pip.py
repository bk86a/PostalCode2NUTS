"""Point-in-polygon NUTS-3 oracle over GISCO NUTS-2024 polygons.

Loads GISCO reference NUTS regions (EPSG:4326, level 3), builds an STRtree, and
resolves a (lat, lon) to its NUTS 0/1/2/3 codes by point-in-polygon. NUTS codes
are hierarchical, so levels 0–2 are prefixes of the matched level-3 id.

Offline analysis tool — NOT imported by the served app. Requires `shapely`
(see requirements-dev.txt).
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from shapely import STRtree
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry


def load_nuts3_features(geojson_path: str | Path) -> list[tuple[str, BaseGeometry]]:
    """Parse a GISCO NUTS GeoJSON (or a .zip containing one) into (nuts_id, geom)
    pairs for level-3 regions only, in EPSG:4326 (lon/lat) coordinates.
    """
    path = Path(geojson_path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            name = next(
                n for n in zf.namelist()
                if "4326" in n and "LEVL_3" in n and n.endswith(".geojson")
            )
            with zf.open(name) as fh:
                data = json.load(fh)
    else:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)

    features: list[tuple[str, BaseGeometry]] = []
    for feat in data["features"]:
        props = feat["properties"]
        if props.get("LEVL_CODE") != 3:
            continue
        features.append((props["NUTS_ID"], shape(feat["geometry"])))
    return features


class NutsPip:
    """In-memory NUTS-3 point-in-polygon index."""

    def __init__(self, features: list[tuple[str, BaseGeometry]]):
        self._ids = [nid for nid, _ in features]
        self._geoms = [geom for _, geom in features]
        self._tree = STRtree(self._geoms)

    def lookup(self, lat: float, lon: float) -> dict | None:
        """Return {'nuts0','nuts1','nuts2','nuts3'} for the point, or None if the
        point falls outside every NUTS-3 region."""
        pt = Point(lon, lat)  # shapely/GeoJSON axis order is (x=lon, y=lat)
        for idx in self._tree.query(pt):
            if self._geoms[idx].covers(pt):
                nid = self._ids[idx]
                return {
                    "nuts3": nid,
                    "nuts2": nid[:4],
                    "nuts1": nid[:3],
                    "nuts0": nid[:2],
                }
        return None
