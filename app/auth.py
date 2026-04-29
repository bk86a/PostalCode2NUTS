"""Token-based rate-limit bypass for /lookup and /pattern.

See docs/superpowers/specs/2026-04-29-auth-token-bypass-design.md for the design.
"""

import hashlib


def token_id(token: str) -> str:
    """First 8 hex chars of sha256(token). Stable, non-reversible audit id."""
    return hashlib.sha256(token.encode()).hexdigest()[:8]
