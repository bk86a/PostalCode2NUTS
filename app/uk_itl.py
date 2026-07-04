"""UK ITL support: LAD → ITL3 bridge and ITL region names.

NSPL maps postcodes to Local Authority Districts (its ``lad25cd`` column holds
GSS codes such as ``E06000001``); NSPL's own ``itl`` column holds GSS entity
codes (e.g. ``S30000026``), NOT the Eurostat ``TL`` codes we want to emit. ONS's
LAD→ITL lookup maps each LAD to its ITL3/2/1 codes in ``TL`` form (the NUTS
successor, e.g. ``TLC31`` → ``TLC3`` → ``TLC``). We bundle that lookup so the
resolution is ``postcode → LAD → ITL3`` yielding clean, truncatable TL codes plus
region names — independent of NSPL's GSS-coded columns.

The bundled CSV (``uk_lad_itl.csv``) carries the ONS column names
(``LAD25CD``, ``ITL325CD``/``ITL325NM``, ``ITL225CD``/``ITL225NM``,
``ITL125CD``/``ITL125NM``); an operator can point ``PC2NUTS_UK_ITL_LOOKUP_URL``
at a refreshed export in the same shape when ONS bumps the ITL vintage. Columns
are matched by their ``ITL<level>…CD``/``…NM`` suffix so vintage-year variants
(``ITL325CD`` vs ``ITL321CD``) both parse.
"""

import csv
import io
from pathlib import Path

_BUNDLED_PATH = Path(__file__).parent / "uk_lad_itl.csv"


def _find_col(fields: list[str], predicate) -> str | None:
    for f in fields:
        if predicate(f.upper()):
            return f
    return None


def parse_lad_itl(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """Parse a LAD→ITL CSV into ``(lad_to_itl3, itl_names)``.

    - ``lad_to_itl3``: ``LAD code (upper) -> ITL3 TL code`` (e.g. ``E06000001`` → ``TLC31``)
    - ``itl_names``:   ``ITL code (L1/L2/L3) -> region name``

    Returns two empty dicts if the required LAD or ITL3 columns are absent.
    """
    reader = csv.DictReader(io.StringIO(text))
    fields = [f.strip() for f in (reader.fieldnames or [])]

    lad_col = _find_col(fields, lambda u: u.startswith("LAD") and u.endswith("CD"))

    def level_cols(level: str) -> tuple[str | None, str | None]:
        code = _find_col(fields, lambda u: u.startswith(f"ITL{level}") and u.endswith("CD"))
        name = _find_col(fields, lambda u: u.startswith(f"ITL{level}") and u.endswith("NM"))
        return code, name

    l3c, l3n = level_cols("3")
    l2c, l2n = level_cols("2")
    l1c, l1n = level_cols("1")

    lad_to_itl3: dict[str, str] = {}
    itl_names: dict[str, str] = {}
    if not lad_col or not l3c:
        return lad_to_itl3, itl_names

    for row in reader:
        lad = (row.get(lad_col) or "").strip().upper()
        itl3 = (row.get(l3c) or "").strip().upper()
        if lad and itl3:
            lad_to_itl3[lad] = itl3
        for code_col, name_col in ((l3c, l3n), (l2c, l2n), (l1c, l1n)):
            if not code_col or not name_col:
                continue
            code = (row.get(code_col) or "").strip().upper()
            name = (row.get(name_col) or "").strip()
            if code and name:
                itl_names.setdefault(code, name)

    return lad_to_itl3, itl_names


def load_bundled() -> tuple[dict[str, str], dict[str, str]]:
    """Load the LAD→ITL bridge bundled with the package."""
    return parse_lad_itl(_BUNDLED_PATH.read_text(encoding="utf-8"))
