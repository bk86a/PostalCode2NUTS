"""Per-country postal code input patterns for prefix stripping and validation.

Each country entry may contain:
  - regex:           Input validation/extraction pattern (capture groups → postal code)
  - example:         Human-readable format examples
  - tercet_map:      Optional transform to align extracted code with TERCET lookup key.
                     Supported actions:
                       truncate:N  — keep only the first N characters
                       prepend:XX  — prepend string XX to the extracted code
                       keep_alpha  — keep only leading alphabetic characters
  - expected_digits: Expected number of digits for all-numeric postal codes.
                     Used by _preprocess() to restore leading zeros lost in Excel/CSV
                     exports (e.g. "8461" → "08461" for ES with expected_digits=5).
                     Omitted for countries with non-numeric codes (IE, MT, NL).

Before regex matching, raw input is preprocessed to fix common data artifacts:
  1. Strip trailing ".0" (Excel float coercion)
  2. Remove dot thousand-separators ("13.600" → "13600")
  3. Restore leading zeros using expected_digits (digit-only, exactly 1 short)
"""

import json
import re
from pathlib import Path

from app.data_loader import normalize_postal_code

# Each regex is used verbatim as provided. Patterns may have 0, 1, or 2 capture groups.
# Regexes are applied after .strip().upper() and are case-insensitive.
_patterns_path = Path(__file__).parent / "postal_patterns.json"
try:
    POSTAL_PATTERNS: dict[str, dict] = json.loads(_patterns_path.read_text())
except (json.JSONDecodeError, OSError) as _exc:
    raise SystemExit(f"Fatal: failed to load {_patterns_path}: {_exc}") from _exc

# Pre-compile all patterns for performance
_COMPILED: dict[str, re.Pattern] = {
    cc: re.compile(pat["regex"], re.IGNORECASE)
    for cc, pat in POSTAL_PATTERNS.items()
}


_THOUSANDS_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")


def _preprocess(raw: str, entry: dict | None) -> str:
    """Clean common data artifacts from raw postal code input.

    Applied before regex matching to recover codes mangled by Excel, CSV
    exports, or database dumps.
    """
    code = raw
    # 1. Strip Excel float suffix: "28040.0" → "28040"
    code = re.sub(r"\.0+$", "", code)
    # 2. Remove dot thousand-separators: "13.600" → "13600"
    if _THOUSANDS_RE.match(code):
        code = code.replace(".", "")
    # 3. Country-aware leading-zero padding (digit-only, exactly 1 short)
    if entry:
        expected = entry.get("expected_digits")
        if expected and code.isdigit() and len(code) == expected - 1:
            code = code.zfill(expected)
    return code


def _apply_tercet_map(code: str, rule: str) -> str:
    """Apply a tercet_map transform rule to an extracted postal code."""
    action, _, arg = rule.partition(":")
    if action == "truncate":
        return code[:int(arg)]
    if action == "prepend":
        return arg + code
    if action == "keep_alpha":
        m = re.match(r"^([A-Z]+)", code)
        return m.group(1) if m else code
    return code


def extract_postal_code(country_code: str, raw_input: str) -> str:
    """Extract and normalize postal code using country-specific pattern.

    1. Look up compiled regex and pattern entry for the country
    2. Preprocess raw input (strip Excel artifacts, restore leading zeros)
    3. Apply regex to cleaned.upper()
    4. If match: concatenate all capture groups (or full match if none) and normalize
    5. Apply tercet_map transform if defined (aligns code with TERCET lookup key)
    6. If no match or no pattern: fall back to normalize_postal_code(cleaned)
    """
    entry = POSTAL_PATTERNS.get(country_code)
    pattern = _COMPILED.get(country_code)
    cleaned = _preprocess(raw_input.strip(), entry)
    if pattern is not None:
        m = pattern.match(cleaned.upper())
        if m:
            groups = m.groups()
            if groups:
                code = normalize_postal_code("".join(groups))
            else:
                code = normalize_postal_code(m.group(0))
            tercet_map = entry.get("tercet_map") if entry else None
            if tercet_map:
                code = _apply_tercet_map(code, tercet_map)
            return code
    return normalize_postal_code(cleaned)
