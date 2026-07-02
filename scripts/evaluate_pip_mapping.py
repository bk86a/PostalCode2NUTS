"""Evaluate coordinate→PIP NUTS mapping against the shipped lookup() logic.

For each (country, postcode) in a query set:
  * current = lookup(country, postcode)   — the shipped six-tier mechanism
  * coord   = GeoNames centroid for that (country, postcode)
  * pip     = NutsPip.lookup(*coord)       — polygon assignment

Each pair is classified into one bucket. This module holds the pure evaluation
core (classify / evaluate / summarize); the runnable entrypoint is added in a
later step. Offline analysis tool — NOT imported by the served app.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from geonames_coords import _normalize_pc


def classify(
    current: dict | None,
    coord: tuple[float, float] | None,
    pip: dict | None,
) -> str:
    """Bucket one (current, coord, pip) triple."""
    if coord is None:
        return "no_coord"
    if pip is None:
        return "pip_outside"
    if current is None:
        return "rescue"
    if current.get("nuts3") == pip["nuts3"]:
        return "agree"
    return "disagree"


def evaluate(rows, coords, oracle, lookup_fn) -> list[dict]:
    """Run the comparison over an iterable of (country, postcode) rows."""
    records: list[dict] = []
    for country, postcode in rows:
        current = lookup_fn(country, postcode)
        coord = coords.get((country.upper(), _normalize_pc(postcode)))
        pip = oracle.lookup(*coord) if coord else None
        records.append(
            {
                "country": country,
                "postcode": postcode,
                "current_tier": current.get("match_type") if current else "not_found",
                "current_nuts3": current.get("nuts3") if current else None,
                "pip_nuts3": pip["nuts3"] if pip else None,
                "bucket": classify(current, coord, pip),
            }
        )
    return records


def summarize(records: list[dict]) -> dict:
    """Aggregate records into overall and per-current-tier bucket crosstabs."""
    overall: Counter = Counter()
    by_tier: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        overall[r["bucket"]] += 1
        by_tier[r["current_tier"]][r["bucket"]] += 1
    return {"overall": dict(overall), "by_tier": {k: dict(v) for k, v in by_tier.items()}}
