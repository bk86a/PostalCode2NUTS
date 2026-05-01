"""Module-level slowapi Limiter, wired according to settings.

When PC2NUTS_RATE_LIMIT_STORAGE_URI is unset, the Limiter falls back to
slowapi's in-process MemoryStorage default — byte-for-byte the same as
the pre-#68 inline construction.

When the URI is set (e.g. 'redis://host:6379/0'), the Limiter routes
counters through the configured backend, with in_memory_fallback_enabled
giving us per-process MemoryStorage during transient backend outages.
slowapi handles the fail-degraded behaviour internally with exponential-
backoff re-probing — see app/main.py:_rate_limit_handler for the 429
response, and the spec at docs/superpowers/specs/2026-05-01-multi-worker-uvicorn-design.md
for the design rationale.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

if settings.rate_limit_storage_uri:
    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri=settings.rate_limit_storage_uri,
        in_memory_fallback_enabled=True,
    )
else:
    limiter = Limiter(key_func=get_remote_address)
