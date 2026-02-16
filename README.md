# PostalCode2NUTS

FastAPI microservice that maps European postal codes to [NUTS codes](https://ec.europa.eu/eurostat/web/nuts) (Nomenclature of Territorial Units for Statistics) using GISCO TERCET flat files.

Returns NUTS levels 1, 2, and 3 for any postal code across EU-27, EFTA, candidate countries, and the UK.

## Quick start

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Or with Docker:

```bash
docker build -t postalcode2nuts .
docker run -p 8000:8000 postalcode2nuts
```

## Usage

```
GET /lookup?country=AT&postal_code=1010
```

```json
{
  "postal_code": "1010",
  "country_code": "AT",
  "nuts1": "AT1",
  "nuts2": "AT13",
  "nuts3": "AT130"
}
```

Interactive API docs are available at `/docs` (Swagger UI) and `/redoc`.

## Endpoints

| Endpoint  | Description |
|-----------|-------------|
| `GET /lookup` | Look up NUTS 1/2/3 codes for a postal code + country |
| `GET /health` | Health check with data statistics |

## Configuration

All settings are overridable via environment variables prefixed with `PC2NUTS_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `PC2NUTS_TERCET_BASE_URL` | NUTS-2024 endpoint | GISCO TERCET base URL |
| `PC2NUTS_NUTS_VERSION` | `2024` | NUTS standard version |
| `PC2NUTS_DATA_DIR` | `./data` | Cache directory for downloaded ZIPs and SQLite DB |
| `PC2NUTS_DB_CACHE_TTL_DAYS` | `30` | Max age of the SQLite cache before rebuild |

## How it works

On first startup the service downloads TERCET flat files (one ZIP per country), parses CSV/TSV contents, and builds an in-memory dict for O(1) lookups. Parsed data is then persisted to a SQLite cache so subsequent startups load in ~1 second instead of re-downloading and re-parsing.

The SQLite cache is version-scoped (`postalcode2nuts_NUTS-{version}.db`), TTL-checked, and written atomically. If the TERCET server is unreachable, a valid cached DB ensures the service still starts with data.

## Data source

GISCO TERCET flat files, &copy; European Union &ndash; GISCO, licensed [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Covers EU-27, EFTA, candidate countries, and the UK.

## License

MIT
