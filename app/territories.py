"""Registry of EU outermost regions, OCTs and other non-NUTS European territories.

This module is a gate in front of the lookup cascade, not a tier inside it.
``classify()`` identifies the territory a (country, postal code) pair belongs to;
``data_loader.lookup()`` then uses ``Territory.in_nuts`` to decide which tiers may
run. Territories Eurostat does not classify may run tier 1 (exact TERCET) only,
which is what makes every approximation and fabricated code structurally
unreachable for them.

Pure data plus string matching — no dependency on loaded TERCET data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).parent / "territories.json"


@dataclass(frozen=True)
class Territory:
    id: str
    iso: str | None
    name: str
    status: str  # "outermost_region" | "oct" | "other"
    administering_country: str
    legal_basis: str | None
    note: str | None
    in_nuts: bool
    validate_as: str | None
    exact: tuple[str, ...]
    prefixes: tuple[str, ...]
    whole_country: bool

    @property
    def has_postal_system(self) -> bool:
        return self.validate_as is not None


@dataclass(frozen=True)
class Classification:
    territory: Territory
    postal_in_territory: bool


_registry: list[Territory] = []
_by_iso: dict[str, Territory] = {}
_by_parent: dict[str, list[Territory]] = {}
_meta: dict = {}


def load_territories() -> None:
    """Load app/territories.json into the module-level indexes. Idempotent."""
    with _DATA_PATH.open(encoding="utf-8") as fh:
        raw = json.load(fh)

    _registry.clear()
    _by_iso.clear()
    _by_parent.clear()
    _meta.clear()
    _meta.update(raw.get("_meta", {}))

    for entry in raw["territories"]:
        postal = entry.get("postal") or {}
        t = Territory(
            id=entry["id"],
            iso=entry.get("iso"),
            name=entry["name"],
            status=entry["status"],
            administering_country=entry["administering_country"],
            legal_basis=entry.get("legal_basis"),
            note=entry.get("note"),
            in_nuts=entry["in_nuts"],
            validate_as=postal.get("validate_as"),
            exact=tuple(postal.get("exact", ())),
            prefixes=tuple(postal.get("prefixes", ())),
            whole_country=bool(postal.get("whole_country", False)),
        )
        _registry.append(t)
        if t.iso:
            _by_iso[t.iso] = t
        # whole_country entries are ISO-only: no parent postal code routes to them.
        if t.validate_as and not t.whole_country:
            _by_parent.setdefault(t.validate_as, []).append(t)

    logger.info("Territories loaded: %d (%d with an ISO code)", len(_registry), len(_by_iso))


def count() -> int:
    return len(_registry)


def get_by_iso(iso: str) -> Territory | None:
    return _by_iso.get(iso)


def territory_iso_codes() -> set[str]:
    return set(_by_iso)


def _postal_belongs(t: Territory, extracted: str) -> bool:
    if not t.has_postal_system or t.whole_country:
        return True
    if extracted in t.exact:
        return True
    return any(extracted.startswith(p) for p in t.prefixes)


def _match_by_parent(country_code: str, extracted: str) -> Territory | None:
    candidates = _by_parent.get(country_code)
    if not candidates:
        return None
    # Exact codes win outright: 97133 is Saint-Barthélemy, not Guadeloupe's 971.
    for t in candidates:
        if extracted in t.exact:
            return t
    best: Territory | None = None
    best_len = -1
    for t in candidates:
        for p in t.prefixes:
            if extracted.startswith(p) and len(p) > best_len:
                best, best_len = t, len(p)
    return best


def classify(country_code: str, postal_code: str, extract) -> Classification | None:
    """Identify the territory a (country, postal code) pair belongs to.

    ``country_code`` may be a territory's own ISO code (the ISO route) or the
    code of the country that administers it (the parent route). ``extract`` is
    ``postal_patterns.extract_postal_code``, injected so this module stays free
    of that dependency and remains unit-testable on its own.

    The extraction country matters. On the ISO route the territory code has no
    pattern of its own, so the raw input is normalised under ``validate_as`` —
    otherwise ``GL/DK-3900`` would normalise to ``DK3900`` and be rejected as
    non-Greenlandic.

    Returns None when the pair is not territorial. When the caller used a
    territory's ISO code but the postal code falls outside that territory's
    ranges, the territory is returned with ``postal_in_territory=False`` so the
    caller can reject it rather than answer from the administering country's data.
    """
    t = _by_iso.get(country_code)
    if t is not None:
        scheme = t.validate_as or t.administering_country
        return Classification(t, _postal_belongs(t, extract(scheme, postal_code)))
    t = _match_by_parent(country_code, extract(country_code, postal_code))
    if t is not None:
        return Classification(t, True)
    return None
