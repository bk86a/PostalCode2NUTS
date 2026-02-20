"""PostalCode2NUTS — Postal code to NUTS code lookup API.

Data source: GISCO TERCET flat files
(c) European Union - GISCO, 2024, postal code point dataset, Licence CC-BY-SA 4.0
"""

import logging
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from app import __version__
from app.config import settings
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
)
from app.models import ErrorResponse, HealthResponse, NUTSResult, PatternResponse
from app.postal_patterns import POSTAL_PATTERNS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

# Access logger — separate from app logger
access_logger = logging.getLogger("app.access")
access_logger.setLevel(logging.INFO)
access_logger.propagate = False
if settings.access_log_file:
    _handler = RotatingFileHandler(
        settings.access_log_file,
        maxBytes=settings.access_log_max_mb * 1024 * 1024,
        backupCount=settings.access_log_backup_count,
    )
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    access_logger.addHandler(_handler)
else:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    access_logger.addHandler(_handler)


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        access_logger.info(
            '%s %s %s %d %.1fms',
            request.client.host if request.client else "-",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
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
    yield


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


def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
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
@limiter.limit(settings.rate_limit)
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
    cc = country.upper()
    if cc == "GR":
        cc = "EL"

    if cc not in get_loaded_countries():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Country '{cc}' is not supported. "
                f"Available countries: {_available_countries_str()}"
            ),
        )

    result = lookup(country, postal_code)
    if result is None:
        pattern = POSTAL_PATTERNS.get(cc)
        hint = f" Expected format: {pattern['example']}" if pattern else ""
        raise HTTPException(
            status_code=404,
            detail=(
                f"No NUTS mapping found for postal code '{postal_code}' "
                f"in country '{cc}'.{hint}"
            ),
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
@limiter.limit(settings.rate_limit)
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
    "/health",
    response_model=HealthResponse,
    summary="Health check and data statistics",
)
def health(response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store"
    table = get_lookup_table()
    estimates = get_estimates_table()
    stale = get_data_stale()
    return HealthResponse(
        status="ok" if len(table) > 0 else "no_data",
        total_postal_codes=len(table),
        total_estimates=len(estimates),
        total_nuts_names=len(get_nuts_names()),
        nuts_version=settings.nuts_version,
        extra_sources=get_extra_source_count(),
        data_stale=stale,
        last_updated=get_data_loaded_at(),
    )
