"""Point-in-polygon NUTS-3 oracle over GISCO NUTS-2024 polygons (production).

Loads GISCO reference NUTS regions (EPSG:4326, level 3), builds an STRtree, and
resolves a (lat, lon) to its NUTS 0/1/2/3 codes. NUTS codes are hierarchical, so
levels 0-2 are prefixes of the matched level-3 id.
"""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

from shapely import STRtree
from shapely.geometry import Point, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

_EARTH_KM = 6371.0088


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two (lat, lon) points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


def load_nuts3_features(geojson_path: str | Path) -> list[tuple[str, BaseGeometry]]:
    """Parse a GISCO NUTS GeoJSON (or a .zip containing one) into (nuts_id, geom)
    pairs for level-3 regions only, in EPSG:4326 (lon/lat)."""
    path = Path(geojson_path)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            name = next(
                (
                    n
                    for n in zf.namelist()
                    if "_RG_" in n and "4326" in n and "LEVL_3" in n and n.endswith(".geojson")
                ),
                None,
            )
            if name is None:
                raise ValueError(f"no 4326 LEVL_3 region (_RG_) GeoJSON member in {path}")
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
        """Return {'nuts0','nuts1','nuts2','nuts3'} for the point, or None if it
        falls outside every NUTS-3 region."""
        pt = Point(lon, lat)  # GeoJSON/shapely axis order is (x=lon, y=lat)
        for idx in self._tree.query(pt):
            if self._geoms[idx].covers(pt):
                nid = self._ids[idx]
                return {"nuts3": nid, "nuts2": nid[:4], "nuts1": nid[:3], "nuts0": nid[:2]}
        return None

    def nearest(self, lat: float, lon: float, max_km: float, country: str | None = None) -> dict | None:
        """Snap a point that fell outside every polygon to the nearest NUTS-3 region.

        Used to rescue coastline/border points where a valid geocode lands just
        outside a (generalized) NUTS polygon. Returns the same hierarchy dict as
        ``lookup`` plus ``snap_km`` (the distance snapped), or None when nothing is
        within ``max_km``. When ``country`` is given, only regions whose NUTS id
        carries that country prefix are considered — this prevents snapping across a
        border into the wrong country. ``max_km <= 0`` disables snapping.
        """
        if max_km <= 0:
            return None
        pt = Point(lon, lat)
        # Coarse bbox prefilter sized in degrees for this latitude (lon compresses
        # by cos(lat)); the exact distance is computed geodesically below.
        dlat = max_km / 110.574
        coslat = max(math.cos(math.radians(lat)), 0.01)
        dlon = max_km / (111.320 * coslat)
        search = box(lon - dlon, lat - dlat, lon + dlon, lat + dlat)

        best_id: str | None = None
        best_km = float("inf")
        for idx in self._tree.query(search):
            nid = self._ids[idx]
            if country and not nid.startswith(country):
                continue
            on_geom, _ = nearest_points(self._geoms[idx], pt)
            d = _haversine_km(lat, lon, on_geom.y, on_geom.x)
            if d < best_km:
                best_km, best_id = d, nid

        if best_id is None or best_km > max_km:
            return None
        nid = best_id
        return {
            "nuts3": nid, "nuts2": nid[:4], "nuts1": nid[:3], "nuts0": nid[:2],
            "snap_km": round(best_km, 3),
        }
