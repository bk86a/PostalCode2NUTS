"""Token-based rate-limit bypass for /lookup and /pattern.

See docs/superpowers/specs/2026-04-29-auth-token-bypass-design.md for the design.
"""

import contextvars
import hashlib
import hmac

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

# DB-backed token cache (refreshed by background task in main.lifespan).
# Empty until the first refresh succeeds; merged into the active set via _get_trusted_tokens.
_db_tokens: frozenset[str] = frozenset()
_token_db_stale: bool = False


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
    """Indirection seam for tests; returns the union of DB-loaded and env-var tokens."""
    return _db_tokens | settings.trusted_tokens


def is_trusted(candidate: str) -> bool:
    """Constant-time membership test against the configured trusted tokens.

    Uses hmac.compare_digest in a loop. Returns False for empty input or when
    no tokens are configured.
    """
    if not candidate:
        return False
    return any(hmac.compare_digest(candidate, t) for t in _get_trusted_tokens())


_request_var: contextvars.ContextVar[Request | None] = contextvars.ContextVar("pc2nuts_request", default=None)


class AuthMiddleware(BaseHTTPMiddleware):
    """Validate Authorization header up-front.

    - No header → request.state.trusted = False, normal flow.
    - Valid bearer token → request.state.trusted = True, request.state.token_id set.
    - Invalid token → 401 short-circuit (no rate-limit consumed).
    - Malformed header → 400 short-circuit.
    - /health is exempt entirely — header is ignored, request flows through.
      This protects monitoring tooling from false 401s if a service mesh adds
      an Authorization header globally.
    - Bypass disabled (empty token set) → header ignored entirely, normal flow.
      When PC2NUTS_TRUSTED_TOKENS is empty/unset the feature is off; any
      Authorization header is ignored and requests fall through to the normal
      per-IP rate limit unchanged (spec §3: behaviour identical to today).

    Also stores the Request in a ContextVar so the parameterless slowapi
    exempt_when callable can read it (slowapi calls exempt_when()).
    """

    EXEMPT_PATHS = frozenset({"/health"})

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.EXEMPT_PATHS:
            request.state.trusted = False
            request.state.token_id = None
            return await call_next(request)

        # Bypass disabled (no tokens configured) → ignore Authorization header
        # entirely; behaviour identical to pre-feature (per-IP rate limit only).
        if not _get_trusted_tokens():
            request.state.trusted = False
            request.state.token_id = None
            return await call_next(request)

        try:
            token = extract_bearer(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

        if token is not None:
            if not is_trusted(token):
                return JSONResponse({"detail": "invalid token"}, status_code=401)
            request.state.trusted = True
            request.state.token_id = token_id(token)
        else:
            request.state.trusted = False
            request.state.token_id = None

        ctx_token = _request_var.set(request)
        try:
            return await call_next(request)
        finally:
            _request_var.reset(ctx_token)


def is_trusted_request() -> bool:
    """Parameterless predicate for slowapi's exempt_when.

    Reads the current Request from the ContextVar set by AuthMiddleware and
    returns True iff request.state.trusted is True. Returns False outside
    a request context (defensive default).
    """
    request = _request_var.get()
    if request is None:
        return False
    return bool(getattr(request.state, "trusted", False))


def refresh_db_tokens(db) -> None:
    """Reload the active token set from the DB.

    On success: replace _db_tokens with the new frozenset, clear the stale flag.
    On failure: keep _db_tokens unchanged, set _token_db_stale = True.

    `db` is duck-typed: must expose .list_active() returning rows with a 'value' key.
    """
    global _db_tokens, _token_db_stale
    try:
        rows = db.list_active()
    except Exception as exc:  # noqa: BLE001 — caller passes a duck-typed client
        import logging

        logging.getLogger(__name__).warning("token DB refresh failed: %s", exc)
        _token_db_stale = True
        return
    _db_tokens = frozenset(r["value"] for r in rows if r.get("value"))
    _token_db_stale = False
