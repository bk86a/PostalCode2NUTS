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

import csv
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

from geonames_coords import _normalize_pc, load_geonames_coords

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling scripts

from nuts_pip import NutsPip, load_nuts3_features  # noqa: E402


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


class _Oracle(Protocol):
    """Duck-typed oracle with a lookup method."""

    def lookup(self, lat: float, lon: float) -> dict | None: ...


def evaluate(
    rows: Iterable[tuple[str, str]],
    coords: dict[tuple[str, str], tuple[float, float]],
    oracle: _Oracle,
    lookup_fn: Callable[[str, str], dict | None],
) -> list[dict]:
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


def format_report(summary: dict) -> str:
    lines = ["## Overall", ""]
    for bucket, n in sorted(summary["overall"].items(), key=lambda kv: -kv[1]):
        lines.append(f"- {bucket}: {n}")
    lines.append("")
    lines.append("## By current tier")
    for tier, buckets in sorted(summary["by_tier"].items()):
        lines.append(f"### {tier}")
        for bucket, n in sorted(buckets.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {bucket}: {n}")
        lines.append("")
    return "\n".join(lines)


def _read_query_rows(path: str):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = {c.upper(): c for c in reader.fieldnames or []}
        cc_col, pc_col = cols["COUNTRY"], cols["POST_CODE"]
        for row in reader:
            yield row[cc_col].strip(), row[pc_col].strip()


def main() -> None:
    # Disable token-DB / trusted-token features for the offline run.
    os.environ.setdefault("PC2NUTS_TOKEN_DB_URL", "")
    os.environ.setdefault("PC2NUTS_TRUSTED_TOKENS", "")

    from app.data_loader import load_data, lookup

    queries_file = os.environ["PC2NUTS_QUERIES_FILE"]
    geojson = os.environ["PC2NUTS_NUTS_GEOJSON"]
    geonames_files = os.environ["PC2NUTS_GEONAMES_FILES"].split(os.pathsep)

    print("Loading NUTS polygons…", file=sys.stderr)
    oracle = NutsPip(load_nuts3_features(geojson))
    print("Loading GeoNames centroids…", file=sys.stderr)
    coords = load_geonames_coords([Path(p) for p in geonames_files])
    print("Loading TERCET data…", file=sys.stderr)
    load_data()

    rows = list(_read_query_rows(queries_file))
    records = evaluate(rows, coords, oracle, lookup)
    summary = summarize(records)

    # Aggregate, code-free summary → committable docs/ file.
    report = format_report(summary)
    print(report)

    # Per-row detail (individual codes) → gitignored local-data/ only.
    out = REPO / "local-data" / "pip-eval-detail.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "country",
                "postcode",
                "current_tier",
                "current_nuts3",
                "pip_nuts3",
                "bucket",
            ],
        )
        writer.writeheader()
        writer.writerows(records)
    print(f"\nPer-row detail written to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
