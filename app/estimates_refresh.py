"""Periodic refresh of tercet_missing_codes.csv from a remote URL (#44).

When PC2NUTS_ESTIMATES_REFRESH_URL is set, a per-worker asyncio task fetches
the URL on every PC2NUTS_ESTIMATES_REFRESH_INTERVAL_SECONDS tick (default 24 h),
parses the body, and full-replaces the in-memory _estimates dict if the
content has changed and passes a 50% relative-row sanity guard.

Defaults preserve the current single-source behaviour: when the URL setting
is unset, this module exposes refresh_estimates_once() that returns a
"disabled" RefreshResult and refresh_estimates_loop() that returns immediately.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings
from app.data_loader import _data_lock, _estimates, _revalidate_estimates, parse_estimates_from_text

logger = logging.getLogger(__name__)


# Module-scoped state. Reset by importlib.reload() in tests.
_last_hash: Optional[str] = None
_last_etag: Optional[str] = None
_last_modified: Optional[str] = None
_stale: Optional[bool] = None  # None when feature disabled


@dataclass
class RefreshResult:
    status: str  # "refreshed" | "unchanged" | "rejected" | "failed" | "disabled"
    previous_count: int
    new_count: int
    skipped_rows: int = 0
    reason: str = ""


def get_refresh_stale() -> Optional[bool]:
    """Return the staleness flag for /health. None when feature disabled."""
    return _stale


def _passes_sanity_guard(new_count: int, current_count: int) -> bool:
    """50%-of-current floor; pass freely when the live state is empty."""
    if current_count == 0:
        return True
    return new_count >= 0.5 * current_count


async def fetch_remote_csv(
    client: httpx.AsyncClient,
) -> tuple[Optional[bytes], int, dict[str, str]]:
    """GET settings.estimates_refresh_url with conditional headers.

    Returns (body, status_code, response_headers). body is None on 304 Not
    Modified, on any non-200 status, and on transport errors. Caller decides
    what to log based on the status code (304 is silent; non-200 is a warning).
    """
    headers: dict[str, str] = {}
    if _last_etag:
        headers["If-None-Match"] = _last_etag
    if _last_modified:
        headers["If-Modified-Since"] = _last_modified

    try:
        r = await client.get(settings.estimates_refresh_url, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        logger.debug("Remote estimates fetch transport error: %s", exc)
        return None, 0, {}

    response_headers = {k.lower(): v for k, v in r.headers.items()}
    if r.status_code == 304:
        return None, 304, response_headers
    if r.status_code != 200:
        return None, r.status_code, response_headers
    return r.content, 200, response_headers
