"""Per-country postal code input patterns for prefix stripping and validation.

Each country entry may contain:
  - regex:           Input validation/extraction pattern (capture groups → postal code)
  - example:         Human-readable format examples
  - tercet_map:      Optional transform to align extracted code with TERCET lookup key.
                     Supported actions:
                       truncate:N   — keep only the first N characters
                       prepend:XX   — prepend string XX to the extracted code
                       keep_alpha   — keep only leading alphabetic characters
                       outward_only — marker: country supports outward-code
                                      fallback (lookup Tier 3.5); no key transform
  - expected_digits: Expected number of digits for all-numeric postal codes.
                     Used by _preprocess() to restore leading zeros lost in Excel/CSV
                     exports (e.g. "8461" → "08461" for ES with expected_digits=5).
                     Omitted for countries with non-numeric codes (IE, MT, NL).

Before regex matching, raw input is preprocessed to fix common data artifacts:
  1. Remove dot thousand-separators ("13.600" → "13600")
  2. Strip trailing ".0" (Excel float coercion)
  3. Restore leading zeros using expected_digits (digit-only, exactly 1 short)
Thousands removal runs before .0 stripping so that "13.000" → "13000" (not "13").
"""

import json
import re
from pathlib import Path

from app.data_loader import normalize_postal_code

# Each regex is used verbatim as provided. Patterns may have 0, 1, or 2 capture groups.
# Regexes are applied after .strip().upper() and are case-insensitive.
_patterns_path = Path(__file__).parent / "postal_patterns.json"
try:
    _raw: dict[str, dict] = json.loads(_patterns_path.read_text())
except (json.JSONDecodeError, OSError) as _exc:
    raise SystemExit(f"Fatal: failed to load {_patterns_path}: {_exc}") from _exc

PATTERNS_META: dict[str, str] = _raw.pop("_meta", {})
POSTAL_PATTERNS: dict[str, dict] = _raw

# Pre-compile all patterns for performance
_COMPILED: dict[str, re.Pattern] = {
    cc: re.compile(pat["regex"], re.IGNORECASE) for cc, pat in POSTAL_PATTERNS.items()
}


_THOUSANDS_RE = re.compile(r"^\d{1,3}(\.\d{3})+$")


def _preprocess(raw: str, entry: dict | None) -> str:
    """Clean common data artifacts from raw postal code input.

    Applied before regex matching to recover codes mangled by Excel, CSV
    exports, or database dumps.
    """
    code = raw
    # 1. Remove dot thousand-separators: "13.600" → "13600"
    #    Must run before .0 stripping so "13.000" → "13000" (not "13").
    if _THOUSANDS_RE.match(code):
        code = code.replace(".", "")
    # 2. Strip Excel float suffix: "28040.0" → "28040"
    code = re.sub(r"\.0+$", "", code)
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
        return code[: int(arg)]
    if action == "prepend":
        return arg + code
    if action == "keep_alpha":
        m = re.match(r"^([A-Z]+)", code)
        return m.group(1) if m else code
    if action == "outward_only":
        # Marker: the country supports outward-code-only fallback (lookup Tier 3.5).
        # It does not transform the Tier 1 key; see extract_outward().
        return code
    return code


def extract_outward(country_code: str, raw_input: str) -> str | None:
    """Return the outward (district) portion for countries flagged outward_only.

    For UK postcodes, the outward portion is the normalised code minus its last
    three characters (the inward code). Input shorter than 4 chars after
    normalisation is treated as already being an outward code (e.g. bare "SW1A").

    Returns None for countries that do not declare tercet_map="outward_only".
    """
    entry = POSTAL_PATTERNS.get(country_code)
    if not entry or entry.get("tercet_map") != "outward_only":
        return None
    normalised = normalize_postal_code(raw_input)
    if len(normalised) <= 4:
        return normalised
    return normalised[:-3]


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


# The single all-digit capture group that every narrowable parent pattern uses,
# e.g. FR's ``([0-9]{5})``. Territories carve a prefix range out of that group.
_DIGIT_GROUP = re.compile(r"\(\[0-9\]\{(\d+)\}\)")


def narrow_to_ranges(parent: dict, exact: tuple[str, ...], prefixes: tuple[str, ...]) -> dict | None:
    """Restrict a parent country's pattern to a territory's own postal ranges.

    A territory validated against its administering country accepts that country's
    whole numbering space, so FR's pattern would call Paris's ``75001`` a valid
    Réunion code. ``/lookup`` already rejects it — the registry knows Réunion is
    ``974xx`` — so this narrows the *reported* pattern to the same ranges, leaving
    the parent's accepted country prefixes (``F-``, ``FR-``) untouched.

    Returns None when narrowing is not possible: no ranges to narrow to, or a
    parent pattern that is not a single all-digit group (only FR and NO are needed
    today; PT and ES split their codes across two groups). Callers fall back to
    the parent pattern unchanged.
    """
    if not exact and not prefixes:
        return None
    digits = parent.get("expected_digits")
    if not digits:
        return None
    groups = _DIGIT_GROUP.findall(parent["regex"])
    if len(groups) != 1 or int(groups[0]) != digits:
        return None

    alternatives = list(exact)
    for prefix in prefixes:
        rest = digits - len(prefix)
        if rest < 0:
            return None
        alternatives.append(prefix + {0: "", 1: "[0-9]"}.get(rest, f"[0-9]{{{rest}}}"))

    regex = _DIGIT_GROUP.sub("(" + "|".join(alternatives) + ")", parent["regex"], count=1)

    # Rebuild the example around a real code from the territory's own range, so
    # "75001, F-75001, FR-75001" becomes "97400, F-97400, FR-97400".
    sample = exact[0] if exact else prefixes[0].ljust(digits, "0")
    example = parent.get("example", "")
    parent_sample = re.search(r"\d{2,}", example)
    example = example.replace(parent_sample.group(), sample) if parent_sample else sample

    return {**parent, "regex": regex, "example": example}
