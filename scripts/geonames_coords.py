"""Load GeoNames postal-code centroids as {(country, postcode): (lat, lon)}.

GeoNames postal dump format (tab-separated, download.geonames.org/export/zip/):
  0 country_code  1 postal_code  2 place_name
  3 admin1_name   4 admin1_code  5 admin2_name  6 admin2_code
  7 admin3_name   8 admin3_code  9 latitude    10 longitude   11 accuracy

Multiple rows may share a (country, postcode); their coordinates are averaged
into one centroid. Offline analysis tool — NOT imported by the served app.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path


def _normalize_pc(postal_code: str) -> str:
    """Loosest comparable postcode form: strip surrounding whitespace and inner
    spaces, uppercase."""
    return postal_code.strip().replace(" ", "").upper()


def load_geonames_coords(
    paths: list[str | Path],
) -> dict[tuple[str, str], tuple[float, float]]:
    # (cc, pc) -> [lat_sum, lon_sum, count]
    sums: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 11:
                    continue
                lat_s, lon_s = cols[9].strip(), cols[10].strip()
                if not lat_s or not lon_s:
                    continue
                key = (cols[0].strip().upper(), _normalize_pc(cols[1]))
                acc = sums[key]
                acc[0] += float(lat_s)
                acc[1] += float(lon_s)
                acc[2] += 1.0
    return {key: (s[0] / s[2], s[1] / s[2]) for key, s in sums.items()}
