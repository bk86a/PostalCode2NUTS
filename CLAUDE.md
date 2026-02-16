# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PostalCode2NUTS is a FastAPI microservice that maps European postal codes to NUTS codes (Nomenclature of Territorial Units for Statistics) using GISCO TERCET flat files from the European Union. Python 3.12, no database — all data is held in-memory for O(1) lookups.

## Build & Run

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally with hot-reload
uvicorn app.main:app --reload --port 8000

# Docker
docker build -t postalcode2nuts:latest .
docker run -p 8000:8000 postalcode2nuts:latest
```

API docs are auto-generated at `/docs` (Swagger) and `/redoc`.

## Environment Variables

All settings are overridable via environment variables prefixed with `PC2NUTS_`:

- `PC2NUTS_TERCET_BASE_URL` — GISCO TERCET base URL (default: NUTS-2024 endpoint)
- `PC2NUTS_NUTS_VERSION` — NUTS standard version (default: `"2024"`)
- `PC2NUTS_DATA_DIR` — Cache dir for downloaded ZIPs and SQLite DB (default: `./data`)
- `PC2NUTS_DB_CACHE_TTL_DAYS` — Max age of the SQLite cache in days before rebuild (default: `30`)

## Architecture

**Startup flow:** FastAPI lifespan event → `data_loader.load_data()` → checks for valid SQLite cache → if fresh, loads directly into `_lookup` dict (~1s); otherwise downloads/caches TERCET ZIPs → parses CSV/TSV → populates `_lookup` → saves to SQLite cache for next startup.

**SQLite persistence cache:** Parsed lookup data is cached in `{data_dir}/postalcode2nuts_NUTS-{version}.db`. The DB holds a `metadata` table (version, creation timestamp, entry count) and a `lookup` table (country_code, postal_code, nuts3). Writes are atomic via temp file + rename. If the TERCET server is down, a valid cached DB ensures the service still starts with data.

**Two-phase data loading:** First tries discovering ZIPs from directory listing HTML; then falls back to guessing URLs with known naming patterns for any missing countries.

**Postal code normalization:** All codes are uppercased with spaces, dashes, and special chars stripped before storage and lookup. This handles format variations across countries (PL: "00-950", SE: "111 22", UK: "SW1A 1AA").

**NUTS level derivation:** Only NUTS3 is stored. NUTS1 and NUTS2 are derived by slicing the NUTS3 code (e.g., `PL213` → NUTS1=`PL2`, NUTS2=`PL21`, NUTS3=`PL213`).

**Greece special case:** ISO code `GR` is mapped to GISCO code `EL` in the lookup function.

## Key Modules

- `app/main.py` — FastAPI app, lifespan handler, `/lookup` and `/health` endpoints
- `app/data_loader.py` — Core logic: ZIP download/caching, CSV parsing, postal code normalization, lookup function
- `app/config.py` — Pydantic Settings with `PC2NUTS_` env prefix; defines supported countries list (37 countries/territories)
- `app/models.py` — Pydantic response models: `NUTSResult`, `ErrorResponse`, `HealthResponse`

## Testing

No test suite exists yet. Key areas to test: `normalize_postal_code()`, `_parse_csv_content()`, `lookup()` (including Greece GR→EL mapping), and the `/lookup` + `/health` endpoints.

## Data Source

GISCO TERCET flat files, (c) European Union, licensed CC-BY-SA 4.0. Covers EU-27, EFTA, candidate countries, and UK.
