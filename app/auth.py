"""Token-based rate-limit bypass for /lookup and /pattern.

See docs/superpowers/specs/2026-04-29-auth-token-bypass-design.md for the design.
"""

import hashlib
import hmac

from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings


def token_id(token: str) -> str:
    """First 8 hex chars of sha256(token). Stable, non-reversible audit id."""
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def extract_bearer(request: Request) -> str | None:
    """Return the bearer token from the Authorization header, or None if absent.

    Raises HTTPException(400) when the header is present but malformed:
    wrong scheme, missing value, or extra whitespace-separated tokens.
    """
    header = request.headers.get("authorization")
    if header is None:
        return None
    parts = header.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise HTTPException(status_code=400, detail="malformed Authorization header")
    return parts[1]


def _get_trusted_tokens() -> frozenset[str]:
    """Indirection seam for tests; returns settings.trusted_tokens at call time."""
    return settings.trusted_tokens


def is_trusted(candidate: str) -> bool:
    """Constant-time membership test against the configured trusted tokens.

    Uses hmac.compare_digest in a loop. Returns False for empty input or when
    no tokens are configured.
    """
    if not candidate:
        return False
    return any(hmac.compare_digest(candidate, t) for t in _get_trusted_tokens())
