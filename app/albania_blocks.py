"""Authoritative Albania (AL) NUTS3 resolver from the postal-code BLOCK scheme.

Albania has no Eurostat TERCET file. Its postal codes are block-allocated by
district: the first two digits identify one of ~33 postal districts, and each
district sits in exactly one of the 12 qarks (= NUTS3). Keying on that allocated
2-digit prefix resolves any code in a real district to its NUTS3 — covering the
gaps GeoNames leaves (issue #118) by construction, at NUTS3 granularity — while a
code whose prefix belongs to no district returns None rather than a fabricated
region.

Source: official Posta Shqiptare allocation, cross-checked vs. Wikipedia "Postal
codes in Albania" and the UPU addressing PDF. The district->qark->NUTS3 mapping
reuses the GISCO-verified qark codes; the two non-obvious assignments (Kruje
15xx -> AL012, Kavaje 25xx -> AL022) are confirmed by GeoNames' own 15xx/25xx
tagging. Validated 100% against the 489 previously-shipped GeoNames codes (see
tests/test_albania_golden.py).
"""

from __future__ import annotations

SUPPORTED: frozenset[str] = frozenset({"AL"})

# (district-center code, NUTS3, district name). Ascending by code. The first two
# digits of each code are the district's allocated prefix; the last two identify
# a postal office within it. 1700 "Transit" / 1800 "EMS" are non-geographic
# service codes folded into Tirana (AL022), matching how GeoNames tags 17xx/18xx.
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

# Allocated 2-digit district prefix -> NUTS3. Each district owns a distinct
# prefix, so this is a 1:1 map with one entry per block.
_PREFIX_TO_NUTS3: dict[str, str] = {str(code)[:2]: nuts3 for code, nuts3, _ in BLOCKS}


def resolve_al_block(postal_code: str) -> str | None:
    """NUTS3 code for a well-formed 4-digit AL postal code, else None.

    A code resolves only when its first two digits are an allocated district
    prefix; codes in unallocated prefixes (no such district) return None rather
    than a fabricated region. Wrong length or non-numeric input also returns None.
    """
    if not (len(postal_code) == 4 and postal_code.isdigit()):
        return None
    return _PREFIX_TO_NUTS3.get(postal_code[:2])
