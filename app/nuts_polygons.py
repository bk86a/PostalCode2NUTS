"""Acquire the GISCO NUTS polygon set and build a NutsPip oracle.

Mirrors data_loader's runtime-download-and-cache pattern: an explicit local
path wins (tests / pre-staged file); otherwise the zip is fetched once from the
configured URL into the data-dir cache and reused on subsequent boots.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from app.nuts_pip import NutsPip, load_nuts3_features

_CACHE_NAME = "ref-nuts-polygons.geojson.zip"


def load_nuts_pip(*, url: str, path: str, cache_dir: str, client: httpx.Client | None) -> NutsPip:
    """Build a NutsPip from a local path if given, else a cached/downloaded zip."""
    if path:
        return NutsPip(load_nuts3_features(path))
    cache = Path(cache_dir) / _CACHE_NAME
    if not cache.exists():
        if client is None:
            raise ValueError("no cached polygons and no httpx client to download them")
        cache.parent.mkdir(parents=True, exist_ok=True)
        resp = client.get(url, timeout=120, follow_redirects=True)
        resp.raise_for_status()
        cache.write_bytes(resp.content)
    return NutsPip(load_nuts3_features(cache))
