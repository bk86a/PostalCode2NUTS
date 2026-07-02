"""Generate Albania (AL) postal-code → NUTS3 estimates from GeoNames.

Albania has a full NUTS hierarchy but no Eurostat TERCET correspondence file,
so we derive per-postal-code NUTS3 codes from GeoNames' admin1 (qark) tagging.
The 12 qarks map 1:1 to the 12 NUTS3 counties. Output is merged into
tercet_missing_codes.csv (Tier-2 estimates), leaving other countries untouched.

Run from the repo root:  python scripts/build_albania_estimates.py
"""

import io
import zipfile
from pathlib import Path

import httpx

GEONAMES_URL = "https://download.geonames.org/export/zip/AL.zip"
CSV_PATH = Path("tercet_missing_codes.csv")
CONFIDENCE = "high"

# GeoNames admin1 (qark) name -> NUTS3 code (NUTS 2024). Verified against GISCO.
QARK_TO_NUTS3 = {
    "Qarku i Dibrës": "AL011",
    "Qarku i Durrësit": "AL012",
    "Qarku i Kukësit": "AL013",
    "Qarku i Lezhës": "AL014",
    "Qarku i Shkodrës": "AL015",
    "Qarku i Elbasanit": "AL021",
    "Tirana": "AL022",
    "Qarku i Beratit": "AL031",
    "Qarku i Fierit": "AL032",
    "Qarku i Gjirokastrës": "AL033",
    "Qarku i Korçës": "AL034",
    "Qarku i Vlorës": "AL035",
}


def rows_from_geonames(records: list[list[str]]) -> list[dict]:
    """Convert parsed GeoNames records into deduped, sorted AL estimate rows."""
    seen: dict[str, str] = {}
    for rec in records:
        if len(rec) < 4:
            continue
        pc = rec[1].strip()
        qark = rec[3].strip()
        if not (len(pc) == 4 and pc.isdigit()):
            continue
        nuts3 = QARK_TO_NUTS3.get(qark)
        if nuts3 is None:
            raise ValueError(f"Unmapped GeoNames admin1 (qark): {qark!r}")
        seen[pc] = nuts3
    rows: list[dict] = []
    for pc in sorted(seen):
        nuts3 = seen[pc]
        rows.append(
            {
                "COUNTRY_CODE": "AL",
                "POSTAL_CODE": pc,
                "ESTIMATED_NUTS3": nuts3,
                "ESTIMATED_NUTS2": nuts3[:4],
                "ESTIMATED_NUTS1": nuts3[:3],
                "CONFIDENCE": CONFIDENCE,
            }
        )
    return rows


def merge_into_csv(csv_path: Path, al_rows: list[dict]) -> None:
    """Rewrite csv_path: header + AL rows + existing non-AL rows (original order).

    Preserves the file's existing line terminator (CRLF or LF) so regenerating
    does not churn every existing line's ending.
    """
    # newline="" disables universal-newline translation so we can detect the
    # file's actual line terminator instead of always seeing "\n"
    # (Path.read_text() has no newline= param until Python 3.13).
    with open(csv_path, encoding="utf-8", newline="") as f:
        raw = f.read()
    newline = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.splitlines()
    header = lines[0]
    kept = [ln for ln in lines[1:] if ln and not ln.startswith("AL,")]
    al_lines = [
        f"{r['COUNTRY_CODE']},{r['POSTAL_CODE']},{r['ESTIMATED_NUTS3']},"
        f"{r['ESTIMATED_NUTS2']},{r['ESTIMATED_NUTS1']},{r['CONFIDENCE']}"
        for r in al_rows
    ]
    csv_path.write_text(newline.join([header, *al_lines, *kept]) + newline, encoding="utf-8")


def main() -> None:
    with httpx.Client(follow_redirects=True) as client:
        resp = client.get(GEONAMES_URL, timeout=60)
        resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        text = zf.read("AL.txt").decode("utf-8")
    records = [line.split("\t") for line in text.splitlines() if line.strip()]
    rows = rows_from_geonames(records)
    merge_into_csv(CSV_PATH, rows)
    print(f"Wrote {len(rows)} Albania estimate rows to {CSV_PATH}")


if __name__ == "__main__":
    main()
