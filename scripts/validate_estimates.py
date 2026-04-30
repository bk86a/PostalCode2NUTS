"""Validate low-confidence estimates against external geocoder + GISCO NUTS lookup.

For each `CONFIDENCE=low` row in `tercet_missing_codes.csv`:
  1. Geocode the postal code via Nominatim (strict `postalcode=` form), then
     fall back to Zippopotam.us. If both fail, mark as "no_geocode".
  2. Send the resulting lat/lon to GISCO `find-nuts.py` to get the NUTS3 code.
  3. Compare with our `ESTIMATED_NUTS3`.

Outputs:
  - A markdown report on stdout (intended for posting as an issue comment).
  - A state file (`.github/data/validation_state.json`) tracking each entry's
    last 3 results. Entries with 3 consecutive `agree` results are returned in
    the report's "ready_to_promote" list — the workflow opens a PR amending
    those rows from `low` → `medium`.

Run with no arguments to validate all `low` entries; `--limit N` to cap for
local testing; `--tier <tier>` to validate other confidence tiers.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "tercet_missing_codes.csv"
STATE_PATH = REPO_ROOT / ".github" / "data" / "validation_state.json"

USER_AGENT = "PostalCode2NUTS-Validator/1.0 (https://github.com/bk86a/PostalCode2NUTS)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ZIPPOPOTAM_URL = "https://api.zippopotam.us"
GISCO_FIND_NUTS = "https://gisco-services.ec.europa.eu/nuts/find-nuts.py"

NOMINATIM_RATE_LIMIT_S = 1.1  # Nominatim usage policy: max 1 RPS, with margin.
PROMOTION_CONSENSUS_DAYS = 3

ResultStatus = Literal["agree", "disagree", "no_geocode", "no_nuts", "error"]


@dataclass
class ValidationResult:
    country: str
    postal_code: str
    estimated_nuts3: str
    status: ResultStatus
    actual_nuts3: str = ""
    note: str = ""


def _geocode_nominatim(client: httpx.Client, country: str, postal_code: str) -> tuple[float, float] | None:
    try:
        r = client.get(
            NOMINATIM_URL,
            params={
                "postalcode": postal_code,
                "countrycodes": country.lower(),
                "format": "json",
                "limit": 1,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    return None


def _geocode_zippopotam(client: httpx.Client, country: str, postal_code: str) -> tuple[float, float] | None:
    try:
        r = client.get(f"{ZIPPOPOTAM_URL}/{country.lower()}/{postal_code}", timeout=15.0)
        if r.status_code != 200:
            return None
        data = r.json()
        place = data["places"][0]
        return float(place["latitude"]), float(place["longitude"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


def _gisco_nuts3(client: httpx.Client, lat: float, lon: float, year: str = "2024") -> str | None:
    """Look up NUTS3 code by coordinates. Returns None if no feature returned."""
    try:
        r = client.get(
            GISCO_FIND_NUTS,
            params={"x": lon, "y": lat, "proj": "4326", "year": year, "level": "3"},
            timeout=15.0,
        )
        r.raise_for_status()
        features = r.json().get("features", [])
        if features:
            return features[0]["properties"]["id"]
    except (httpx.HTTPError, ValueError, KeyError):
        return None
    return None


def _validate_row(client: httpx.Client, row: dict[str, str]) -> ValidationResult:
    country, postal_code = row["COUNTRY_CODE"], row["POSTAL_CODE"]
    estimated = row["ESTIMATED_NUTS3"]

    # Stage 1: geocode (strict only)
    coord = _geocode_nominatim(client, country, postal_code)
    time.sleep(NOMINATIM_RATE_LIMIT_S)  # Always pace Nominatim, even on miss.
    if coord is None:
        coord = _geocode_zippopotam(client, country, postal_code)
    if coord is None:
        return ValidationResult(country, postal_code, estimated, "no_geocode")

    # Stage 2: NUTS lookup
    actual = _gisco_nuts3(client, *coord)
    if actual is None:
        return ValidationResult(country, postal_code, estimated, "no_nuts",
                                note=f"coords={coord[0]:.4f},{coord[1]:.4f}")

    if actual == estimated:
        return ValidationResult(country, postal_code, estimated, "agree", actual)
    return ValidationResult(country, postal_code, estimated, "disagree", actual,
                            note=f"coords={coord[0]:.4f},{coord[1]:.4f}")


def _key(country: str, postal_code: str) -> str:
    return f"{country}|{postal_code}"


def _load_state() -> dict[str, list[str]]:
    if not STATE_PATH.exists():
        return {}
    return json.loads(STATE_PATH.read_text())


def _save_state(state: dict[str, list[str]]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _update_state_and_find_promotions(
    state: dict[str, list[str]], results: list[ValidationResult]
) -> list[ValidationResult]:
    """Append today's status to each entry's history (capped to last N).
    Returns the subset of agree-rows whose last N entries are all 'agree'."""
    promotions: list[ValidationResult] = []
    for r in results:
        k = _key(r.country, r.postal_code)
        history = state.get(k, [])
        history.append(r.status)
        history = history[-PROMOTION_CONSENSUS_DAYS:]
        state[k] = history
        if (len(history) >= PROMOTION_CONSENSUS_DAYS
                and all(h == "agree" for h in history)
                and r.status == "agree"):
            promotions.append(r)
    return promotions


def _render_report(
    results: list[ValidationResult],
    promotions: list[ValidationResult],
    run_date: str,
) -> str:
    counts = {"agree": 0, "disagree": 0, "no_geocode": 0, "no_nuts": 0, "error": 0}
    for r in results:
        counts[r.status] += 1
    total = len(results)
    validated = counts["agree"] + counts["disagree"]
    agree_rate = (counts["agree"] / validated * 100) if validated else 0.0

    lines = [
        f"## Validation run — {run_date}",
        "",
        f"**Total `low`-confidence entries:** {total}",
        "",
        "| Outcome | Count | % of total |",
        "|---|---:|---:|",
        f"| ✅ Agree (geocoded → NUTS3 matches estimate) | {counts['agree']} | {counts['agree']/total*100:.1f}% |",
        f"| ❌ Disagree | {counts['disagree']} | {counts['disagree']/total*100:.1f}% |",
        f"| ⚠️ Could not geocode | {counts['no_geocode']} | {counts['no_geocode']/total*100:.1f}% |",
        f"| ⚠️ Geocoded but no NUTS3 found at point | {counts['no_nuts']} | {counts['no_nuts']/total*100:.1f}% |",
        f"| 🛑 Pipeline error | {counts['error']} | {counts['error']/total*100:.1f}% |",
        "",
        f"**Agreement rate** (of geocoded+NUTS-found entries): {agree_rate:.1f}% "
        f"({counts['agree']}/{validated})",
        "",
    ]

    if promotions:
        lines += [
            f"### 🎉 Ready to promote — {len(promotions)} entries ({PROMOTION_CONSENSUS_DAYS} consecutive agreements)",
            "",
            "These will be moved from `low` → `medium` in the auto-PR opened by this run.",
            "",
            "<details><summary>List</summary>",
            "",
            "| Country | Postal code | NUTS3 |",
            "|---|---|---|",
        ]
        for r in promotions[:200]:
            lines.append(f"| {r.country} | `{r.postal_code}` | `{r.actual_nuts3}` |")
        if len(promotions) > 200:
            lines.append(f"| _… and {len(promotions) - 200} more_ | | |")
        lines += ["", "</details>", ""]

    disputes = [r for r in results if r.status == "disagree"]
    if disputes:
        lines += [
            f"### ⚠️ Disputed — {len(disputes)} entries",
            "",
            "GISCO returned a different NUTS3 than our estimate. May be: a true error in the estimate, a Nominatim mis-geocode, or a border-pixel artefact in GISCO's polygon lookup. Review periodically.",
            "",
            "<details><summary>List</summary>",
            "",
            "| Country | Postal code | Estimated | GISCO says | Note |",
            "|---|---|---|---|---|",
        ]
        for r in disputes[:200]:
            lines.append(f"| {r.country} | `{r.postal_code}` | `{r.estimated_nuts3}` | `{r.actual_nuts3}` | {r.note} |")
        if len(disputes) > 200:
            lines.append(f"| _… and {len(disputes) - 200} more_ | | | | |")
        lines += ["", "</details>", ""]

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", default="low", choices=["low", "medium", "high"])
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap number of entries (for local testing)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write report markdown to this path (in addition to stdout)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip state update — useful for one-off probes")
    parser.add_argument("--run-date", default=time.strftime("%Y-%m-%d"))
    args = parser.parse_args()

    rows = []
    with CSV_PATH.open(newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["CONFIDENCE"] == args.tier]
    if args.limit:
        rows = rows[: args.limit]
    print(f"Validating {len(rows)} entries (tier={args.tier})", file=sys.stderr)

    results: list[ValidationResult] = []
    with httpx.Client() as client:
        for i, row in enumerate(rows, 1):
            try:
                results.append(_validate_row(client, row))
            except Exception as exc:
                results.append(ValidationResult(
                    row["COUNTRY_CODE"], row["POSTAL_CODE"], row["ESTIMATED_NUTS3"],
                    "error", note=f"{type(exc).__name__}: {exc}"
                ))
            if i % 25 == 0:
                print(f"  {i}/{len(rows)}", file=sys.stderr)

    state = _load_state() if not args.dry_run else {}
    promotions = _update_state_and_find_promotions(state, results)
    if not args.dry_run:
        _save_state(state)
        # Reset history for promoted entries so they don't auto-promote again
        # if the workflow PR is merged.
        for r in promotions:
            state.pop(_key(r.country, r.postal_code), None)
        _save_state(state)

    report = _render_report(results, promotions, args.run_date)
    print(report)
    if args.output:
        args.output.write_text(report)

    # Emit the promotion list as a side artefact for the workflow.
    if promotions and not args.dry_run:
        promotions_path = REPO_ROOT / "promotions.json"
        promotions_path.write_text(json.dumps(
            [{"country": r.country, "postal_code": r.postal_code} for r in promotions],
            indent=2,
        ))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
