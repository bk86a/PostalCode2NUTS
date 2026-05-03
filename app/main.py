"""PostalCode2NUTS — Postal code to NUTS code lookup API.

Data source: GISCO TERCET flat files
(c) European Union - GISCO, 2024, postal code point dataset, Licence CC-BY-SA 4.0
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse

from app import __version__, config as _config
from app.auth import AuthMiddleware, is_trusted_request
from app.estimates_refresh import get_refresh_stale as _get_estimates_refresh_stale
from app.config import settings
from app.limiter import limiter
from app.data_loader import (
    get_data_loaded_at,
    get_data_stale,
    get_estimates_table,
    get_extra_source_count,
    get_loaded_countries,
    get_lookup_table,
    get_nuts_names,
    load_data,
    lookup,
    normalize_country,
)
from app.models import ErrorResponse, HealthResponse, NUTSResult, PatternResponse
from app.postal_patterns import PATTERNS_META, POSTAL_PATTERNS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Access logger — separate from app logger.
# Propagates to the root logger so pytest caplog can capture records.
# When access_log_file is set, also writes to a dedicated rotating file.
access_logger = logging.getLogger("app.access")
access_logger.setLevel(logging.INFO)
if settings.access_log_file:
    _handler = RotatingFileHandler(
        settings.access_log_file,
        maxBytes=settings.access_log_max_mb * 1024 * 1024,
        backupCount=settings.access_log_backup_count,
    )
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    access_logger.addHandler(_handler)
    access_logger.propagate = False


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        log_suffix = ""
        tid = getattr(request.state, "token_id", None)
        if tid:
            log_suffix = f" token_id={tid}"
        access_logger.info(
            "%s %s %s %d %.1fms%s",
            request.client.host if request.client else "-",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            log_suffix,
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading TERCET data (NUTS %s)...", settings.nuts_version)
    load_data()
    table = get_lookup_table()
    estimates = get_estimates_table()
    names = get_nuts_names()
    logger.info(
        "Ready — %d postal codes loaded, %d estimates available, %d NUTS names.",
        len(table),
        len(estimates),
        len(names),
    )
    extra = get_extra_source_count()
    if extra:
        logger.info("Extra data sources configured: %d", extra)
    if get_data_stale():
        logger.warning("Serving STALE data — TERCET refresh failed, using expired cache")

    # ── Token DB refresh (#61) ──────────────────────────────────────────────
    # Use _config.settings (module-level reference) so that test reloads of the
    # config module don't cause a stale settings binding to be read here.
    refresh_task: asyncio.Task | None = None
    if _config.settings.token_db_url:
        from app import auth as auth_mod
        from app.token_db import TokenDB

        token_db = TokenDB(
            _config.settings.token_db_url,
            auth_token=_config.settings.token_db_auth_token,
        )

        async def _refresh_loop():
            interval = max(1, _config.settings.token_refresh_seconds)
            while True:
                await asyncio.to_thread(auth_mod.refresh_db_tokens, token_db)
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    return

        # Initial synchronous refresh so /lookup is correct from the first request
        await asyncio.to_thread(auth_mod.refresh_db_tokens, token_db)
        refresh_task = asyncio.create_task(_refresh_loop())
        logger.info(
            "Token DB refresh task started (interval %ds)",
            _config.settings.token_refresh_seconds,
        )

    # ── Estimates remote-refresh (#44) ──────────────────────────────────────
    estimates_refresh_task: asyncio.Task | None = None
    if _config.settings.estimates_refresh_url:
        from app import estimates_refresh as _estimates_refresh

        # Bootstrap synchronously so the worker reflects upstream before reporting ready.
        try:
            result = await _estimates_refresh.refresh_estimates_once()
            logger.info(
                "Estimates bootstrap fetch: %s (previous=%d, new=%d)",
                result.status,
                result.previous_count,
                result.new_count,
            )
        except Exception:
            logger.exception("Estimates bootstrap fetch crashed; continuing with bundled CSV")
            _estimates_refresh._stale = True

        if _config.settings.estimates_refresh_interval_seconds > 0:
            estimates_refresh_task = asyncio.create_task(_estimates_refresh.refresh_estimates_loop())
            logger.info(
                "Estimates refresh task started (interval %ds)",
                _config.settings.estimates_refresh_interval_seconds,
            )

    yield

    if refresh_task is not None:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass

    if estimates_refresh_task is not None:
        estimates_refresh_task.cancel()
        try:
            await estimates_refresh_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="PostalCode2NUTS",
    description=(
        "Look up European NUTS codes (levels 1-3) for a given postal code "
        "and country. Data sourced from GISCO TERCET flat files."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
)
app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    headers = {}
    if settings.rate_limit_headers:
        window_seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
        retry_after = "60"
        for unit, secs in window_seconds.items():
            if unit in settings.rate_limit:
                retry_after = str(secs)
                break
        headers = {
            "Retry-After": retry_after,
            "X-RateLimit-Limit": settings.rate_limit,
            "X-RateLimit-Remaining": "0",
        }
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
        headers=headers,
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

# CORS middleware
if settings.cors_origins:
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET"],
            allow_headers=["*"],
        )


app.add_middleware(AuthMiddleware)
app.add_middleware(AccessLogMiddleware)


def _available_countries_str() -> str:
    """Sorted comma-separated list of country codes with loaded data."""
    return ", ".join(sorted(get_loaded_countries()))


@app.get(
    "/lookup",
    response_model=NUTSResult,
    responses={
        400: {"model": ErrorResponse, "description": "Unsupported country"},
        404: {"model": ErrorResponse, "description": "Postal code not found"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
    summary="Look up NUTS codes for a postal code",
)
@limiter.limit(settings.rate_limit, exempt_when=is_trusted_request)
def lookup_postal_code(
    request: Request,
    response: Response,
    postal_code: str = Query(
        ...,
        max_length=20,
        description="Postal code to look up (e.g. '00-950', '1010', '10115')",
        examples=["00-950", "1010", "10115"],
    ),
    country: str = Query(
        ...,
        min_length=2,
        max_length=2,
        pattern=r"^[A-Za-z]{2}$",
        description="ISO 3166-1 alpha-2 country code (e.g. 'PL', 'AT', 'DE')",
        examples=["PL", "AT", "DE"],
    ),
):
    cc = normalize_country(country)

    if cc not in get_loaded_countries():
        raise HTTPException(
            status_code=400,
            detail=(f"Country '{cc}' is not supported. Available countries: {_available_countries_str()}"),
        )

    result = lookup(country, postal_code)
    if result is None:
        pattern = POSTAL_PATTERNS.get(cc)
        hint = f" Expected format: {pattern['example']}" if pattern else ""
        raise HTTPException(
            status_code=404,
            detail=(f"No NUTS mapping found for postal code '{postal_code}' in country '{cc}'.{hint}"),
        )
    response.headers["Cache-Control"] = f"public, max-age={settings.cache_max_age}"
    return NUTSResult(
        postal_code=postal_code,
        country_code=cc,
        **result,
    )


@app.get(
    "/pattern",
    response_model=PatternResponse | list[str],
    responses={
        404: {"model": ErrorResponse, "description": "No pattern for this country"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
    summary="Get postal code regex pattern for a country",
)
@limiter.limit(settings.rate_limit, exempt_when=is_trusted_request)
def get_pattern(
    request: Request,
    response: Response,
    country: str | None = Query(
        default=None,
        min_length=2,
        max_length=2,
        pattern=r"^[A-Za-z]{2}$",
        description="ISO 3166-1 alpha-2 country code. Omit to list all available codes.",
        examples=["AT", "DE", "NL"],
    ),
):
    response.headers["Cache-Control"] = f"public, max-age={settings.cache_max_age}"
    if country is None:
        return sorted(POSTAL_PATTERNS.keys())
    cc = country.upper()
    pattern = POSTAL_PATTERNS.get(cc)
    if pattern is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No postal code pattern defined for country '{cc}'. "
                f"Available countries: {', '.join(sorted(POSTAL_PATTERNS.keys()))}"
            ),
        )
    return PatternResponse(
        country_code=cc,
        regex=pattern["regex"],
        example=pattern["example"],
    )


@app.get(
    "/",
    summary="Service entry point",
    include_in_schema=False,
)
def root(request: Request, response: Response):
    response.headers["Cache-Control"] = f"public, max-age={settings.cache_max_age}"
    base = str(request.base_url).rstrip("/")
    return {
        "service": "PostalCode2NUTS",
        "version": __version__,
        "links": {
            "openapi": f"{base}/openapi.json",
            "docs": f"{base}/docs" if settings.docs_enabled else None,
            "redoc": f"{base}/redoc" if settings.docs_enabled else None,
            "health": f"{base}/health",
            "lookup_example": f"{base}/lookup?country=DE&postal_code=10115",
            "pattern_example": f"{base}/pattern?country=DE",
            "source": "https://github.com/bk86a/PostalCode2NUTS",
        },
    }


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check and data statistics",
)
def health(response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store"
    table = get_lookup_table()
    estimates = get_estimates_table()
    stale = get_data_stale()

    # Token DB staleness — only meaningful when the feature is enabled.
    from app import auth as auth_mod

    token_db_stale = auth_mod._token_db_stale if _config.settings.token_db_url else None

    return HealthResponse(
        status="ok" if len(table) > 0 else "no_data",
        total_postal_codes=len(table),
        total_estimates=len(estimates),
        total_nuts_names=len(get_nuts_names()),
        nuts_version=settings.nuts_version,
        extra_sources=get_extra_source_count(),
        patterns_version=PATTERNS_META.get("version", "unknown"),
        data_stale=stale,
        last_updated=get_data_loaded_at(),
        token_db_stale=token_db_stale,
        estimates_refresh_stale=_get_estimates_refresh_stale(),
    )


@app.post(
    "/admin/refresh-estimates",
    summary="Force-refresh estimates from the configured remote URL",
    description=(
        "Operator-only — requires `Authorization: Bearer <trusted-token>`. "
        "Synchronously fetches the configured `PC2NUTS_ESTIMATES_REFRESH_URL` and, "
        "if the content has changed and passes the sanity guard, replaces the "
        "in-memory estimates table. No body."
    ),
    responses={
        200: {"description": "Refresh succeeded (content changed and applied)"},
        401: {"description": "Missing or invalid trusted bearer token"},
        409: {"description": "Sanity guard rejected the candidate CSV"},
        502: {"description": "Upstream fetch or parse failed"},
        503: {"description": "Feature disabled (PC2NUTS_ESTIMATES_REFRESH_URL unset)"},
    },
)
async def admin_refresh_estimates(request: Request) -> JSONResponse:
    if not getattr(request.state, "trusted", False):
        raise HTTPException(status_code=401, detail="Trusted token required")

    from app import estimates_refresh as _estimates_refresh

    result = await _estimates_refresh.refresh_estimates_once()

    if result.status == "disabled":
        return JSONResponse(status_code=503, content={"status": "disabled"})
    if result.status == "rejected":
        return JSONResponse(
            status_code=409,
            content={
                "status": "rejected",
                "reason": result.reason,
                "previous_count": result.previous_count,
                "candidate_count": result.new_count,
            },
        )
    if result.status == "failed":
        return JSONResponse(
            status_code=502,
            content={"status": "failed", "reason": result.reason},
        )

    # "refreshed" or "unchanged"
    return JSONResponse(
        status_code=200,
        content={
            "status": result.status,
            "previous_count": result.previous_count,
            "new_count": result.new_count,
            "skipped_rows": result.skipped_rows,
            "source_url": settings.estimates_refresh_url,
        },
    )


@app.get(
    "/admin/memory",
    summary="Memory and runtime diagnostics",
    description=(
        "Operator-only — requires `Authorization: Bearer <trusted-token>`. "
        "Returns sizes of in-memory state, /proc process counters, asyncio task / "
        "thread / file-descriptor counts, and a `gc.get_objects()` type histogram. "
        "Intended for one-off leak investigations; safe to call repeatedly but "
        "the gc walk takes ~hundreds of ms on large heaps."
    ),
    include_in_schema=False,
)
async def admin_memory(request: Request) -> JSONResponse:
    if not getattr(request.state, "trusted", False):
        raise HTTPException(status_code=401, detail="Trusted token required")

    import asyncio
    import gc
    import os
    import threading
    from collections import Counter

    from app import auth as _auth
    from app import data_loader as _dl
    from app.limiter import limiter as _limiter

    sizes: dict[str, int] = {
        "data_loader._lookup": len(_dl._lookup),
        "data_loader._estimates": len(_dl._estimates),
        "data_loader._prefix_index_countries": len(_dl._prefix_index),
        "data_loader._prefix_index_total_entries": sum(
            sum(len(v) for v in idx.values()) for idx in _dl._prefix_index.values()
        ),
        "data_loader._nuts_names": len(_dl._nuts_names),
        "data_loader._single_nuts3": len(_dl._single_nuts3),
        "data_loader._country_fallback": len(_dl._country_fallback),
        "auth._db_tokens": len(_auth._db_tokens),
    }
    storage = getattr(_limiter, "_storage", None)
    if storage is not None:
        for attr in ("storage", "expirations", "events", "locks"):
            d = getattr(storage, attr, None)
            if d is not None:
                try:
                    sizes[f"limiter._storage.{attr}"] = len(d)
                except TypeError:
                    pass

    proc: dict[str, str | int] = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                key, _, val = line.partition(":")
                if key in {"VmRSS", "VmSize", "VmHWM", "RssAnon", "RssFile", "Threads"}:
                    proc[key] = val.strip()
    except OSError:
        pass
    try:
        proc["fd_count"] = len(os.listdir("/proc/self/fd"))
    except OSError:
        pass

    try:
        tasks = asyncio.all_tasks()
        task_info: dict[str, object] = {
            "count": len(tasks),
            "sample": sorted({str(t.get_coro())[:160] for t in tasks})[:15],
        }
    except RuntimeError:
        task_info = {"count": -1, "sample": []}

    gc.collect()
    type_counts = Counter(type(o).__name__ for o in gc.get_objects())
    top_types = [{"type": t, "count": c} for t, c in type_counts.most_common(30)]

    return JSONResponse(
        status_code=200,
        content={
            "sizes": sizes,
            "proc": proc,
            "asyncio_tasks": task_info,
            "thread_count": threading.active_count(),
            "gc_top_30_types": top_types,
        },
    )
