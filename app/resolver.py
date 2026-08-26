"""Pure postal→NUTS-or-geocode cascade for /resolve. HTTP-free, unit-testable.

Geocoding fires ONLY when the postal result is weak (not_found or
nuts3_confidence < threshold) AND the caller supplied a street/city address.
Otherwise the postal result (even a weak one) is returned as best-effort — so a
/resolve answer is never worse than /lookup.
"""

from __future__ import annotations

from collections.abc import Callable


def resolve(
    country: str,
    postal_code: str,
    street: str | None,
    city: str | None,
    *,
    lookup_fn: Callable[[str, str], dict | None],
    geocode_fn: Callable[[str | None, str | None, str], tuple[float, float] | None] | None,
    pip,
    name_fn: Callable[[str], str | None],
    threshold: float = 0.85,
    snap_km: float = 0.0,
) -> dict:
    current = lookup_fn(country, postal_code)
    match_type = current.get("match_type") if current else "not_found"
    conf = current.get("nuts3_confidence") if current else None
    weak = current is None or (conf is not None and conf < threshold)
    has_address = bool((street or "").strip() or (city or "").strip())

    def _postal() -> dict:
        if current is None:
            return {
                "resolved_via": "none",
                "nuts1": None,
                "nuts1_name": None,
                "nuts2": None,
                "nuts2_name": None,
                "nuts3": None,
                "nuts3_name": None,
                "nuts3_confidence": None,
            }
        return {
            "resolved_via": "postal",
            "nuts1": current.get("nuts1"),
            "nuts1_name": current.get("nuts1_name"),
            "nuts2": current.get("nuts2"),
            "nuts2_name": current.get("nuts2_name"),
            "nuts3": current.get("nuts3"),
            "nuts3_name": current.get("nuts3_name"),
            "nuts3_confidence": current.get("nuts3_confidence"),
        }

    territory = current.get("territory") if current else None
    base = {"country_code": country, "postal_code": postal_code, "match_type": match_type}
    if territory is not None:
        base["territory"] = territory
        if territory["nuts_coverage"] == "none":
            # No NUTS polygon covers the territory, so a coordinate cannot help:
            # PIP would return pip_outside and we would fall back to the postal
            # answer anyway. Skip the geocoder rather than pay for that round trip.
            return {
                **base,
                "match_type": None,
                "resolved_via": "none",
                "nuts1": None,
                "nuts1_name": None,
                "nuts2": None,
                "nuts2_name": None,
                "nuts3": None,
                "nuts3_name": None,
                "nuts3_confidence": None,
                "geocode": {"status": "not_attempted"},
            }

    if not weak:
        return {**base, **_postal(), "geocode": {"status": "not_attempted"}}
    if not has_address:
        return {**base, **_postal(), "geocode": {"status": "no_address"}}
    if geocode_fn is None:
        return {**base, **_postal(), "geocode": {"status": "geocoder_unavailable"}}

    coord = geocode_fn(street, city, postal_code)
    if coord is None:
        return {**base, **_postal(), "geocode": {"status": "no_result"}}

    lat, lon = coord
    hit = pip.lookup(lat, lon)
    if hit is not None and not hit["nuts3"].startswith(country):
        # Inside a NEIGHBORING country's polygon — Photon mislocated the address. Don't
        # trust it, and don't snap (snapping would rescue a known-bad point into a
        # same-country border region). Fall back to postal best-effort.
        return {**base, **_postal(), "geocode": {"status": "pip_outside", "lat": lat, "lon": lon}}
    geocode: dict = {"status": "ok", "lat": lat, "lon": lon}
    if hit is None:
        # Point fell outside every polygon — try snapping to the nearest same-country
        # NUTS-3 region (coastline/border rescue) before giving up.
        snapped = pip.nearest(lat, lon, snap_km, country) if snap_km and snap_km > 0 else None
        if snapped is None:
            return {**base, **_postal(), "geocode": {"status": "pip_outside", "lat": lat, "lon": lon}}
        hit = snapped
        geocode = {"status": "snapped", "lat": lat, "lon": lon, "snap_km": snapped["snap_km"]}

    geocode["nuts3"] = hit["nuts3"]
    return {
        **base,
        "resolved_via": "geocode",
        "nuts1": hit["nuts1"],
        "nuts1_name": name_fn(hit["nuts1"]),
        "nuts2": hit["nuts2"],
        "nuts2_name": name_fn(hit["nuts2"]),
        "nuts3": hit["nuts3"],
        "nuts3_name": name_fn(hit["nuts3"]),
        "nuts3_confidence": None,
        "geocode": geocode,
    }
