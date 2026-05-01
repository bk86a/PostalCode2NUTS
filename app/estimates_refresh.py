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


async def refresh_estimates_once(
    client: Optional[httpx.AsyncClient] = None,
) -> RefreshResult:
    """One refresh attempt: fetch, sanity-check, swap.

    When `client` is None, an ephemeral httpx.AsyncClient is created and
    closed. Production callers (the lifespan loop, the admin endpoint)
    should pass a long-lived client to reuse connections.
    """
    global _last_hash, _last_etag, _last_modified, _stale

    previous_count = len(_estimates)

    if not settings.estimates_refresh_url:
        return RefreshResult(
            status="disabled",
            previous_count=previous_count,
            new_count=previous_count,
        )

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient()
    try:
        body, status, headers = await fetch_remote_csv(client)
    finally:
        if own_client:
            await client.aclose()

    # 304 Not Modified — content unchanged, refresh succeeded
    if status == 304:
        _stale = False
        return RefreshResult(
            status="unchanged",
            previous_count=previous_count,
            new_count=previous_count,
        )

    # Any other non-200 (including transport errors with status=0)
    if body is None:
        was_stale_before = _stale is True
        _stale = True
        if not was_stale_before:
            logger.warning(
                "Remote estimates fetch failed (status=%d); keeping current state",
                status,
            )
        return RefreshResult(
            status="failed",
            previous_count=previous_count,
            new_count=previous_count,
            reason=f"http={status}",
        )

    new_hash = hashlib.sha256(body).hexdigest()
    if new_hash == _last_hash:
        _stale = False
        return RefreshResult(
            status="unchanged",
            previous_count=previous_count,
            new_count=previous_count,
        )

    try:
        text = body.decode("utf-8-sig")
        new_dict, skipped = parse_estimates_from_text(text)
    except (UnicodeDecodeError, ValueError, csv.Error, KeyError) as exc:
        _stale = True
        logger.warning("Remote estimates parse failed: %s", exc)
        return RefreshResult(
            status="failed",
            previous_count=previous_count,
            new_count=previous_count,
            reason=f"parse: {exc}",
        )

    if not _passes_sanity_guard(len(new_dict), previous_count):
        _stale = True
        logger.warning(
            "Remote estimates sanity guard rejected swap (new=%d, current=%d)",
            len(new_dict),
            previous_count,
        )
        return RefreshResult(
            status="rejected",
            previous_count=previous_count,
            new_count=len(new_dict),
            reason=f"sanity guard: {len(new_dict)} < 50% of {previous_count}",
        )

    # Swap. _data_lock is a threading.Lock; acquiring it briefly from an async
    # context is fine — the swap is microseconds.
    with _data_lock:
        _estimates.clear()
        _estimates.update(new_dict)
        _revalidate_estimates()
    new_count = len(_estimates)

    _last_hash = new_hash
    _last_etag = headers.get("etag")
    _last_modified = headers.get("last-modified")
    _stale = False

    logger.info(
        "Remote estimates refreshed: %d -> %d (skipped %d rows during parse)",
        previous_count,
        new_count,
        skipped,
    )
    return RefreshResult(
        status="refreshed",
        previous_count=previous_count,
        new_count=new_count,
        skipped_rows=skipped,
    )


async def refresh_estimates_loop() -> None:
    """Periodic refresh task. Returns immediately when feature is disabled."""
    if not settings.estimates_refresh_url:
        return
    interval = settings.estimates_refresh_interval_seconds
    if interval <= 0:
        return
    while True:
        await asyncio.sleep(interval)
        try:
            await refresh_estimates_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("refresh_estimates_loop iteration crashed; will retry")
