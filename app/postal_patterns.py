"""Per-country postal code input patterns for prefix stripping and validation.

Each country entry may contain:
  - regex:      Input validation/extraction pattern (capture groups → postal code)
  - example:    Human-readable format examples
  - tercet_map: Optional transform to align extracted code with TERCET lookup key.
                Supported actions:
                  truncate:N  — keep only the first N characters
                  prepend:XX  — prepend string XX to the extracted code
                  keep_alpha  — keep only leading alphabetic characters
"""

import json
import re
from pathlib import Path

from app.data_loader import normalize_postal_code

# Each regex is used verbatim as provided. Patterns may have 0, 1, or 2 capture groups.
# Regexes are applied after .strip().upper() and are case-insensitive.
POSTAL_PATTERNS: dict[str, dict] = json.loads(
    (Path(__file__).parent / "postal_patterns.json").read_text()
)

# Pre-compile all patterns for performance
_COMPILED: dict[str, re.Pattern] = {
    cc: re.compile(pat["regex"], re.IGNORECASE)
    for cc, pat in POSTAL_PATTERNS.items()
}


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

    1. Look up compiled regex for the country
    2. Apply it to raw_input.strip().upper()
    3. If match: concatenate all capture groups (or full match if none) and normalize
    4. Apply tercet_map transform if defined (aligns code with TERCET lookup key)
    5. If no match or no pattern: fall back to normalize_postal_code(raw_input)
    """
    entry = POSTAL_PATTERNS.get(country_code)
    pattern = _COMPILED.get(country_code)
    if pattern is not None:
        m = pattern.match(raw_input.strip().upper())
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
    return normalize_postal_code(raw_input)
