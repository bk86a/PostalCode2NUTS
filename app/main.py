"""PostalCode2NUTS — Postal code to NUTS code lookup API.

Data source: GISCO TERCET flat files
(c) European Union - GISCO, 2024, postal code point dataset, Licence CC-BY-SA 4.0
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from app.config import settings
from app.data_loader import get_lookup_table, load_data, lookup
from app.models import ErrorResponse, HealthResponse, NUTSResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Loading TERCET data (NUTS %s)...", settings.nuts_version)
    load_data()
    table = get_lookup_table()
    logger.info("Ready — %d postal codes loaded.", len(table))
    yield


app = FastAPI(
    title="PostalCode2NUTS",
    description=(
        "Look up European NUTS codes (levels 1-3) for a given postal code "
        "and country. Data sourced from GISCO TERCET flat files."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.get(
    "/lookup",
    response_model=NUTSResult,
    responses={
        404: {"model": ErrorResponse, "description": "Postal code not found"},
        422: {"model": ErrorResponse, "description": "Invalid parameters"},
    },
    summary="Look up NUTS codes for a postal code",
)
def lookup_postal_code(
    postal_code: str = Query(
        ...,
        description="Postal code to look up (e.g. '00-950', '1010', '10115')",
        examples=["00-950", "1010", "10115"],
    ),
    country: str = Query(
        ...,
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 country code (e.g. 'PL', 'AT', 'DE')",
        examples=["PL", "AT", "DE"],
    ),
):
    result = lookup(country, postal_code)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No NUTS mapping found for postal code '{postal_code}' "
                f"in country '{country.upper()}'"
            ),
        )
    return NUTSResult(
        postal_code=postal_code,
        country_code=country.upper(),
        **result,
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check and data statistics",
)
def health():
    table = get_lookup_table()
    return HealthResponse(
        status="ok" if len(table) > 0 else "no_data",
        total_postal_codes=len(table),
        nuts_version=settings.nuts_version,
    )
