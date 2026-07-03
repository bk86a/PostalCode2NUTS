"""Golden regression: the block resolver must reproduce every NUTS3 that the
retired GeoNames generator assigned to the 489 shipped AL codes."""

import csv
from pathlib import Path

from app.albania_blocks import resolve_al_block

GOLDEN = Path(__file__).parent / "fixtures" / "albania_geonames_golden.csv"


def _golden_rows():
    with open(GOLDEN, encoding="utf-8", newline="") as f:
        return [(r["postal_code"], r["nuts3"]) for r in csv.DictReader(f)]


def test_golden_fixture_is_populated():
    assert len(_golden_rows()) >= 480


def test_block_resolver_reproduces_every_geonames_code():
    mismatches = [
        (pc, geo, resolve_al_block(pc))
        for pc, geo in _golden_rows()
        if resolve_al_block(pc) != geo
    ]
    assert mismatches == [], f"block map disagrees with GeoNames on: {mismatches[:10]}"
