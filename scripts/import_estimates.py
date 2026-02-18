#!/usr/bin/env python3
"""Import pre-computed NUTS estimates into the SQLite cache DB.

Reads a CSV with columns:
    COUNTRY_CODE, POSTAL_CODE, ESTIMATED_NUTS3, ESTIMATED_NUTS2,
    ESTIMATED_NUTS1, CONFIDENCE

Maps the text confidence label (high/medium/low) to per-level numerical
values and inserts into the 'estimates' table.

Usage:
    python -m scripts.import_estimates [--csv PATH] [--db PATH]
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

# Add project root to path so we can import app modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.data_loader import normalize_postal_code

DEFAULT_CSV = PROJECT_ROOT / "tests" / "tercet_missing_codes.csv"


def _default_db_path() -> Path:
    from app.config import settings
    return Path(settings.data_dir) / f"postalcode2nuts_NUTS-{settings.nuts_version}.db"


def import_estimates(csv_path: Path, db_path: Path) -> int:
    """Read CSV and upsert estimates into the DB. Returns count imported."""
    if not csv_path.is_file():
        print(f"ERROR: CSV file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    rows = []
    skipped = 0
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cc = row["COUNTRY_CODE"].strip().upper()
            pc = normalize_postal_code(row["POSTAL_CODE"])
            n3 = row["ESTIMATED_NUTS3"].strip()
            n2 = row["ESTIMATED_NUTS2"].strip()
            n1 = row["ESTIMATED_NUTS1"].strip()
            label = row["CONFIDENCE"].strip().lower()

            conf = settings.confidence_map.get(label)
            if conf is None:
                skipped += 1
                continue

            rows.append((
                cc, pc, n3, n2, n1,
                conf["nuts3"], conf["nuts2"], conf["nuts1"],
            ))

    if not rows:
        print("ERROR: No valid rows found in CSV.", file=sys.stderr)
        sys.exit(1)

    # Ensure the DB file exists (may not if first run before data load)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(db_path))
    try:
        # Create estimates table if it doesn't exist
        con.execute(
            "CREATE TABLE IF NOT EXISTS estimates ("
            "country_code TEXT NOT NULL, "
            "postal_code TEXT NOT NULL, "
            "nuts3 TEXT NOT NULL, "
            "nuts2 TEXT NOT NULL, "
            "nuts1 TEXT NOT NULL, "
            "nuts3_confidence REAL NOT NULL, "
            "nuts2_confidence REAL NOT NULL, "
            "nuts1_confidence REAL NOT NULL, "
            "PRIMARY KEY (country_code, postal_code))"
        )
        # Clear existing estimates and insert fresh
        con.execute("DELETE FROM estimates")
        con.executemany(
            "INSERT INTO estimates "
            "(country_code, postal_code, nuts3, nuts2, nuts1, "
            "nuts3_confidence, nuts2_confidence, nuts1_confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        # Update metadata if table exists
        try:
            con.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                ("estimate_count", str(len(rows))),
            )
        except sqlite3.OperationalError:
            pass  # metadata table may not exist yet (pre-first data load)
        con.commit()
    finally:
        con.close()

    if skipped:
        print(f"Warning: skipped {skipped} rows with unknown confidence labels.")
    print(f"Imported {len(rows)} estimates into {db_path}")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Import pre-computed NUTS estimates into the SQLite DB."
    )
    parser.add_argument(
        "--csv", type=Path, default=DEFAULT_CSV,
        help=f"Path to CSV file (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--db", type=Path, default=None,
        help="Path to SQLite DB (default: auto-detected from settings)",
    )
    args = parser.parse_args()

    db_path = args.db if args.db else _default_db_path()
    import_estimates(args.csv, db_path)


if __name__ == "__main__":
    main()
