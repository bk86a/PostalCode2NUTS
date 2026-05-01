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
