"""Analyse a real-world country+postcode dataset against the lookup logic.

Buckets every (country, postcode) pair into one of:
  exact       — Tier 1 (TERCET hit)
  estimated   — Tier 2 (curated estimate from tercet_missing_codes.csv)
  approximate — Tier 3 (runtime prefix approximation) or Tier 4 (country majority)
  estimated_fallback — Tier 5 (single-NUTS3 country, e.g. LI/CY/LU)
  not_found   — lookup returned None
  regex_fail  — postcode failed to match the country's regex
  unsupported — country not in POSTAL_PATTERNS

Outputs:
  - stdout: full summary (per-country tables, confidence histograms, etc.)
  - local-data/failures.csv (gitignored): the specific rows that failed (for
    private debugging — never committed)
  - docs/real-query-analysis-YYYY-MM-DD.md: aggregate-only summary suitable
    for committing (no individual codes in the body)

Required env: PC2NUTS_QUERIES_FILE (path to the input CSV with COUNTRY,
POST_CODE columns; values may be quoted/whitespace-padded).
"""

from __future__ import annotations

import csv
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

# Disable token-DB / trusted-token feature for the offline run.
os.environ.setdefault("PC2NUTS_TOKEN_DB_URL", "")
os.environ.setdefault("PC2NUTS_TRUSTED_TOKENS", "")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.data_loader import load_data, lookup, normalize_country  # noqa: E402
from app.postal_patterns import POSTAL_PATTERNS, extract_postal_code  # noqa: E402


def _bucket(result: dict | None, raw_pc: str, country: str) -> str:
    """Map a lookup() result to a bucket name."""
    if country not in POSTAL_PATTERNS and normalize_country(country) not in POSTAL_PATTERNS:
        return "unsupported"

    extracted = extract_postal_code(normalize_country(country), raw_pc)
    if not extracted:
        return "regex_fail"

    if result is None:
        return "not_found"
    return result.get("match_type", "unknown")


def _confidence_band(c: float | None) -> str:
    if c is None:
        return "n/a"
    if c >= 0.95:
        return "≥0.95"
    if c >= 0.85:
        return "0.85-0.95"
    if c >= 0.70:
        return "0.70-0.85"
    if c >= 0.40:
        return "0.40-0.70"
    return "<0.40"


def main() -> int:
    queries_file = os.environ.get("PC2NUTS_QUERIES_FILE")
    if not queries_file:
        print("ERROR: set PC2NUTS_QUERIES_FILE to the path of the input CSV", file=sys.stderr)
        return 2

    print("Loading service data...", file=sys.stderr, flush=True)
    t0 = time.monotonic()
    load_data()
    print(f"  ready in {time.monotonic()-t0:.1f}s", file=sys.stderr)

    bucket_counts: Counter[str] = Counter()
    per_country_buckets: dict[str, Counter[str]] = defaultdict(Counter)
    confidence_bands: Counter[str] = Counter()
    failures: list[dict[str, str]] = []  # rows that hit anything other than exact

    print(f"Reading {queries_file}...", file=sys.stderr, flush=True)
    total = 0
    t0 = time.monotonic()
    with open(queries_file, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            total += 1
            country = (row.get("COUNTRY") or "").strip()
            postcode_raw = (row.get("POST_CODE") or "").strip()

            try:
                result = lookup(country, postcode_raw)
            except Exception as exc:  # noqa: BLE001
                bucket = "error"
                per_country_buckets[country][bucket] += 1
                bucket_counts[bucket] += 1
                failures.append({
                    "country": country, "postcode": postcode_raw,
                    "bucket": bucket, "detail": f"{type(exc).__name__}: {exc}",
                    "extracted": "", "nuts3": "",
                })
                continue

            bucket = _bucket(result, postcode_raw, country)
            bucket_counts[bucket] += 1
            per_country_buckets[country][bucket] += 1

            if result is not None:
                c = result.get("nuts3_confidence")
                confidence_bands[_confidence_band(c)] += 1
            else:
                confidence_bands["n/a"] += 1

            if bucket != "exact":
                cc_norm = normalize_country(country)
                extracted = extract_postal_code(cc_norm, postcode_raw) if cc_norm in POSTAL_PATTERNS else ""
                failures.append({
                    "country": country, "postcode": postcode_raw,
                    "bucket": bucket, "extracted": extracted,
                    "nuts3": (result or {}).get("nuts3", ""),
                    "detail": "",
                })
            if total % 25_000 == 0:
                print(f"  {total:,} rows processed ({time.monotonic()-t0:.1f}s)", file=sys.stderr)

    elapsed = time.monotonic() - t0
    print(f"Done — {total:,} rows in {elapsed:.1f}s ({total/elapsed:.0f}/s)", file=sys.stderr)

    # --- stdout summary ---
    print()
    print(f"## Real-query analysis — {total:,} rows from {len(per_country_buckets)} countries")
    print()
    print("### Overall bucket distribution")
    print()
    print("| Bucket | Count | % |")
    print("|---|---:|---:|")
    bucket_order = ["exact", "estimated", "approximate", "estimated_fallback",
                    "not_found", "regex_fail", "unsupported", "error", "unknown"]
    for b in bucket_order:
        n = bucket_counts.get(b, 0)
        if n:
            print(f"| {b} | {n:,} | {n/total*100:.1f}% |")

    print()
    print("### Confidence band (NUTS3 level)")
    print()
    print("| Band | Count | % |")
    print("|---|---:|---:|")
    band_order = ["≥0.95", "0.85-0.95", "0.70-0.85", "0.40-0.70", "<0.40", "n/a"]
    for band in band_order:
        n = confidence_bands.get(band, 0)
        if n:
            print(f"| {band} | {n:,} | {n/total*100:.1f}% |")

    print()
    print("### Per-country bucket distribution")
    print()
    print("| Country | Total | exact | estimated | approximate | not_found | regex_fail | other |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for cc in sorted(per_country_buckets, key=lambda c: -sum(per_country_buckets[c].values())):
        b = per_country_buckets[cc]
        cc_total = sum(b.values())
        other = cc_total - sum(b.get(k, 0) for k in
                               ["exact", "estimated", "approximate", "not_found", "regex_fail"])
        print(f"| {cc} | {cc_total:,} | "
              f"{b.get('exact', 0):,} ({b.get('exact', 0)/cc_total*100:.0f}%) | "
              f"{b.get('estimated', 0):,} | "
              f"{b.get('approximate', 0):,} | "
              f"{b.get('not_found', 0):,} | "
              f"{b.get('regex_fail', 0):,} | "
              f"{other:,} |")

    # --- failures CSV (private) ---
    if failures:
        out_dir = REPO / "local-data"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "failures.csv"
        with out_path.open("w", newline="") as f:
            w = csv.DictWriter(
                f, fieldnames=["country", "postcode", "extracted", "bucket", "nuts3", "detail"]
            )
            w.writeheader()
            w.writerows(failures)
        print(f"\n  → {len(failures):,} non-exact rows written to {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
