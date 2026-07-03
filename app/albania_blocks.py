"""Authoritative Albania (AL) NUTS3 resolver from the postal-code BLOCK scheme.

Albania has no Eurostat TERCET file. Its postal codes are block-allocated by
district: the first two digits identify one of ~33 postal districts, and each
district sits in exactly one of the 12 qarks (= NUTS3). A range map keyed on the
district-center codes resolves ANY well-formed 4-digit code to its NUTS3 by the
block it falls into — covering the gaps GeoNames leaves (issue #118) by
construction, at NUTS3 granularity.

Source: official Posta Shqiptare allocation, cross-checked vs. Wikipedia "Postal
codes in Albania" and the UPU addressing PDF. The district->qark->NUTS3 mapping
reuses the GISCO-verified qark codes; the two non-obvious assignments (Kruje
15xx -> AL012, Kavaje 25xx -> AL022) are confirmed by GeoNames' own 15xx/25xx
tagging. Validated 100% against the 489 previously-shipped GeoNames codes (see
tests/test_albania_golden.py).
"""

from __future__ import annotations

from bisect import bisect_right

SUPPORTED: frozenset[str] = frozenset({"AL"})

# (district-center code, NUTS3, district name). Ascending by code. Each code is
# the LOWER bound of that district's block; a block runs to the next code.
# 1700 "Transit" / 1800 "EMS" are non-geographic service codes folded into
# Tirana (AL022), matching how GeoNames tags the 17xx/18xx prefixes.
BLOCKS: list[tuple[int, str, str]] = [
    (1000, "AL022", "Tirana"),
    (1500, "AL012", "Kruje"),
    (1700, "AL022", "Transit (service)"),
    (1800, "AL022", "EMS Office (service)"),
    (2000, "AL012", "Durres"),
    (2500, "AL022", "Kavaje"),
    (3000, "AL021", "Elbasan"),
    (3300, "AL021", "Gramsh"),
    (3400, "AL021", "Librazhd"),
    (3500, "AL021", "Peqin"),
    (4000, "AL015", "Shkoder"),
    (4300, "AL015", "Malesi e Madhe"),
    (4400, "AL015", "Puke"),
    (4500, "AL014", "Lezhe"),
    (4600, "AL014", "Mirdite"),
    (4700, "AL014", "Kurbin"),
    (5000, "AL031", "Berat"),
    (5300, "AL031", "Kucove"),
    (5400, "AL031", "Skrapar"),
    (6000, "AL033", "Gjirokaster"),
    (6300, "AL033", "Tepelene"),
    (6400, "AL033", "Permet"),
    (7000, "AL034", "Korce"),
    (7300, "AL034", "Pogradec"),
    (7400, "AL034", "Kolonje"),
    (8000, "AL011", "Mat"),
    (8300, "AL011", "Diber"),
    (8400, "AL011", "Bulqize"),
    (8500, "AL013", "Kukes"),
    (8600, "AL013", "Has"),
    (8700, "AL013", "Tropoje"),
    (9000, "AL032", "Lushnje"),
    (9300, "AL032", "Fier"),
    (9400, "AL035", "Vlore"),
    (9700, "AL035", "Sarande"),
]

_STARTS = [b[0] for b in BLOCKS]
_NUTS3 = [b[1] for b in BLOCKS]


def resolve_al_block(postal_code: str) -> str | None:
    """NUTS3 code for a well-formed 4-digit AL postal code, else None.

    Any code >= 1000 maps to its enclosing district block (incl. 9800-9999 ->
    Sarande/AL035 as best-effort). Codes < 1000, wrong length, or non-numeric
    return None.
    """
    if not (len(postal_code) == 4 and postal_code.isdigit()):
        return None
    n = int(postal_code)
    if n < _STARTS[0]:
        return None
    return _NUTS3[bisect_right(_STARTS, n) - 1]
