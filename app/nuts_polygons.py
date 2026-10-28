"""Acquire the GISCO NUTS polygon set and build a NutsPip oracle.

Mirrors data_loader's runtime-download-and-cache pattern: an explicit local
path wins (tests / pre-staged file); otherwise the zip is fetched once from the
configured URL into the data-dir cache and reused on subsequent boots.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import httpx2 as httpx

from app.data_loader import _get_capped, _write_atomic
from app.nuts_pip import NutsPip, load_nuts3_features

_CACHE_NAME = "ref-nuts-polygons.geojson.zip"

logger = logging.getLogger(__name__)


_DEFAULT_MAX_BYTES = 512 * 1024 * 1024


def load_nuts_pip(
    *,
    url: str,
    path: str,
    cache_dir: str,
    client: httpx.Client | None,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> NutsPip:
    """Build a NutsPip from a local path if given, else a cached/downloaded zip.

    `max_bytes` caps the download so a hostile or broken upstream cannot exhaust
    memory at startup; the GISCO zip is ~160 MB.
    """
    if path:
        return NutsPip(load_nuts3_features(path))
    cache = Path(cache_dir) / _CACHE_NAME
    if cache.exists() and not zipfile.is_zipfile(cache):
        # A bad cached file would otherwise disable PIP on every boot.
        logger.warning("Cached %s is not a valid ZIP; deleting and re-downloading", cache.name)
        cache.unlink(missing_ok=True)
    if not cache.exists():
        if client is None:
            raise ValueError("no cached polygons and no HTTP client to download them")
        cache.parent.mkdir(parents=True, exist_ok=True)
        resp = _get_capped(client, url, limit_bytes=max_bytes, timeout=120)
        resp.raise_for_status()
        if not zipfile.is_zipfile(io.BytesIO(resp.content)):
            raise ValueError(f"polygon download from {url} is not a ZIP")
        # Atomic: workers cold-starting together must never read a half-written zip.
        _write_atomic(cache, resp.content)
    return NutsPip(load_nuts3_features(cache))
