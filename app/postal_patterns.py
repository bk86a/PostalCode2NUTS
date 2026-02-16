"""Per-country postal code input patterns for prefix stripping and validation."""

import re

from app.data_loader import normalize_postal_code

# Each regex is used verbatim as provided. Patterns may have 0, 1, or 2 capture groups.
# Regexes are applied after .strip().upper() and are case-insensitive.
POSTAL_PATTERNS: dict[str, dict[str, str]] = {
    "AT": {
        "regex": r"^(?:A-?|AT-?)?([0-9]{4})$",
        "example": "1010, A-1010, AT-1010",
    },
    "BE": {
        "regex": r"^(?:B-?|BE-?)?([0-9]{4})$",
        "example": "1000, B-1000, BE-1000",
    },
    "BG": {
        "regex": r"^(?:BG-?)?([0-9]{4})$",
        "example": "1000, BG-1000",
    },
    "CH": {
        "regex": r"^(?:CH-?)?([0-9]{4})$",
        "example": "8000, CH-8000",
    },
    "CY": {
        "regex": r"^(?:CY-?)?([0-9]{4})$",
        "example": "1010, CY-1010",
    },
    "CZ": {
        "regex": r"^(?:CZ-?)?(\d{3}\s?\d{2})$",
        "example": "11000, CZ-11000, 110 00",
    },
    "DE": {
        "regex": r"^(?:D-?|DE-?)?([0-9]{5})$",
        "example": "10115, D-10115, DE-10115",
    },
    "DK": {
        "regex": r"^(?:DK-?)?([0-9]{4})$",
        "example": "1050, DK-1050",
    },
    "EE": {
        "regex": r"^(?:EE-?)?([0-9]{5})$",
        "example": "10111, EE-10111",
    },
    "EL": {
        "regex": r"^(?:GR-?)?([0-9]{5})$",
        "example": "10431, GR-10431, EL-10431",
    },
    "ES": {
        "regex": r"^(?:E-?)?([0-9]{5})$",
        "example": "28001, E-28001",
    },
    "FI": {
        "regex": r"^(?:FI-?)?([0-9]{5})$",
        "example": "00100, FI-00100",
    },
    "FR": {
        "regex": r"^(?:F-?)?([0-9]{5})$",
        "example": "75001, F-75001",
    },
    "HR": {
        "regex": r"^(?:HR-?)?([0-9]{5})$",
        "example": "10000, HR-10000",
    },
    "HU": {
        "regex": r"^(?:H-?)?([0-9]{4})$",
        "example": "1011, H-1011",
    },
    "IE": {
        "regex": r"^[A-Z](?:\d{2}|6W)\s[A-Z0-9]{4}$",
        "example": "D02 X285, A65 F4E2",
    },
    "IS": {
        "regex": r"^(?:IS-?)?([0-9]{3})$",
        "example": "101, IS-101",
    },
    "IT": {
        "regex": r"^(?:I-?|IT-?)?([0-9]{5})$",
        "example": "00118, I-00118, IT-00118",
    },
    "LI": {
        "regex": r"^(?:FL-?)?([0-9]{4})$",
        "example": "9490, FL-9490",
    },
    "LT": {
        "regex": r"^(?:LT-?)?([0-9]{5})$",
        "example": "01100, LT-01100",
    },
    "LU": {
        "regex": r"^(?:L-?)?([0-9]{4})$",
        "example": "1009, L-1009",
    },
    "LV": {
        "regex": r"^\d{4}$",
        "example": "1010",
    },
    "MK": {
        "regex": r"^(?:MK-?)?([0-9]{4})$",
        "example": "1000, MK-1000",
    },
    "MT": {
        "regex": r"^([A-Z]{2,3}\s\d{2,4})$",
        "example": "VLT 1010, FNT 1010, MSK 1234",
    },
    "NL": {
        "regex": r"^(?:NL-?)?(\d{4}\s?[A-Z]{2})$",
        "example": "1012 AB, NL-1012AB",
    },
    "NO": {
        "regex": r"^(?:N-?)?([0-9]{4})$",
        "example": "0150, N-0150",
    },
    "PL": {
        "regex": r"^(?:PL-?)?([0-9]{2})-?([0-9]{3})$",
        "example": "00-950, 00950, PL-00-950",
    },
    "PT": {
        "regex": r"^([0-9]{4})-?([0-9]{3})$",
        "example": "1000-001, 1000001",
    },
    "RO": {
        "regex": r"^(?:RO-?)?([0-9]{6})$",
        "example": "010001, RO-010001",
    },
    "RS": {
        "regex": r"^([0-9]{5})$",
        "example": "11000",
    },
    "SE": {
        "regex": r"^(?:S-?|SE-?)?(\d{3}\s?\d{2})$",
        "example": "10005, 100 05, S-10005, SE-10005",
    },
    "SI": {
        "regex": r"^(?:SI-?)?([0-9]{4})$",
        "example": "1000, SI-1000",
    },
    "SK": {
        "regex": r"^(?:SK-?)?(\d{3}\s?\d{2})$",
        "example": "81101, SK-81101, 811 01",
    },
    "TR": {
        "regex": r"^(?:TR-?)?(\d{5})$",
        "example": "06100, TR-06100, 34000",
    },
}

# Pre-compile all patterns for performance
_COMPILED: dict[str, re.Pattern] = {
    cc: re.compile(pat["regex"], re.IGNORECASE)
    for cc, pat in POSTAL_PATTERNS.items()
}


def extract_postal_code(country_code: str, raw_input: str) -> str:
    """Extract and normalize postal code using country-specific pattern.

    1. Look up compiled regex for the country
    2. Apply it to raw_input.strip().upper()
    3. If match: concatenate all capture groups (or full match if none) and normalize
    4. If no match or no pattern: fall back to normalize_postal_code(raw_input)
    """
    pattern = _COMPILED.get(country_code)
    if pattern is not None:
        m = pattern.match(raw_input.strip().upper())
        if m:
            groups = m.groups()
            if groups:
                # Concatenate all capture groups (handles 1 or 2 groups)
                return normalize_postal_code("".join(groups))
            else:
                # No capture groups (e.g. IE, LV) — use the full match
                return normalize_postal_code(m.group(0))
    return normalize_postal_code(raw_input)
