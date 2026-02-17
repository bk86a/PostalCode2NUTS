# PostalCode2NUTS

FastAPI microservice that maps European postal codes to [NUTS codes](https://ec.europa.eu/eurostat/web/nuts) (Nomenclature of Territorial Units for Statistics) using [GISCO TERCET](https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units/postal-codes) flat files.

Returns NUTS levels 1, 2, and 3 for any postal code across 34 European countries and territories.

## Coverage

Based on GISCO TERCET NUTS-2024 correspondence tables (last updated March 2025).

**EU-27** (27 countries):
Austria (AT), Belgium (BE), Bulgaria (BG), Croatia (HR), Cyprus (CY), Czechia (CZ), Denmark (DK), Estonia (EE), Finland (FI), France (FR), Germany (DE), Greece (EL), Hungary (HU), Ireland (IE), Italy (IT), Latvia (LV), Lithuania (LT), Luxembourg (LU), Malta (MT), Netherlands (NL), Poland (PL), Portugal (PT), Romania (RO), Slovakia (SK), Slovenia (SI), Spain (ES), Sweden (SE)

**EFTA** (4 countries):
Iceland (IS), Liechtenstein (LI), Norway (NO), Switzerland (CH)

**EU candidate countries** (3):
North Macedonia (MK), Serbia (RS), Türkiye (TR)


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

## Endpoints

| Endpoint  | Description |
|-----------|-------------|
| `GET /lookup` | Look up NUTS 1/2/3 codes for a postal code + country |
| `GET /pattern` | Get the postal code regex pattern for a country |
| `GET /health` | Health check with data statistics |

Interactive API docs are available at `/docs` (Swagger UI) and `/redoc`.

### `GET /lookup`

Look up NUTS codes for a postal code in a given country.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `country` | string (2 letters) | yes | ISO 3166-1 alpha-2 country code |
| `postal_code` | string | yes | Postal code (with or without country prefix) |

**Example:**

```
GET /lookup?country=AT&postal_code=A-1010
```

```json
{
  "postal_code": "A-1010",
  "country_code": "AT",
  "nuts1": "AT1",
  "nuts2": "AT13",
  "nuts3": "AT130"
}
```

The service accepts postal codes with or without country prefixes. For example, all of the following resolve to the same result for Austria: `1010`, `A-1010`, `AT-1010`, `A1010`.

Greece uses the GISCO code `EL`, but you can query with either `EL` or `GR` — the service maps `GR` to `EL` automatically.

### `GET /pattern`

Returns the regex pattern used to validate and extract postal codes for a given country. When called without a `country` parameter, returns the list of all supported country codes.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `country` | string (2 letters) | no | ISO 3166-1 alpha-2 country code. Omit to list all available codes. |

**Examples:**

```
GET /pattern
```

```json
["AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FR", "HR", "HU", "IE", "IS", "IT", "LI", "LT", "LU", "LV", "MK", "MT", "NL", "NO", "PL", "PT", "RO", "RS", "SE", "SI", "SK", "TR"]
```

```
GET /pattern?country=AT
```

```json
{
  "country_code": "AT",
  "regex": "^(?:A-?|AT-?)?([0-9]{4})$",
  "example": "1010, A-1010, AT-1010"
}
```

### `GET /health`

Returns service status and data statistics.

```json
{
  "status": "ok",
  "total_postal_codes": 830032,
  "nuts_version": "2024"
}
```

## Error handling

The API uses standard HTTP status codes with human-readable error messages:

| Status | Meaning | When |
|--------|---------|------|
| **200** | Success | Lookup found, pattern returned, health OK |
| **400** | Bad request | Country code is not supported (lists available countries) |
| **404** | Not found | Postal code not found (shows expected format), or no pattern for country |
| **422** | Validation error | Parameter format invalid (e.g. country code not 2 letters, contains digits) |

**Examples:**

Unsupported country (400):
```json
{"detail": "Country 'XX' is not supported. Available countries: AT, BE, BG, CH, ..."}
```

Postal code not found (404) — includes expected format hint:
```json
{"detail": "No NUTS mapping found for postal code 'Traiskirchen' in country 'AT'. Expected format: 1010, A-1010, AT-1010"}
```

Invalid parameter format (422):
```json
{"detail": [{"msg": "String should match pattern '^[A-Za-z]{2}$'", "input": "12"}]}
```

## Postal code input patterns

The service uses per-country regex patterns (`app/postal_patterns.py`) to handle real-world postal code input variations:

- **Country prefix stripping** — `A-1010`, `D-10115`, `B-1000` are stripped to their numeric postal codes before lookup
- **Internal formatting** — dashes in Polish codes (`00-950`), spaces in Dutch codes (`1012 AB`), etc. are normalized
- **Fallback** — if the input doesn't match the country pattern, generic normalization is applied (strip all non-alphanumeric, uppercase)

### Pattern extraction flow

```
User input: "A-5600"
  → regex match for AT: captures "5600"
  → normalize: "5600"
  → lookup ("AT", "5600") in TERCET → NUTS result

User input: "00-950"
  → regex match for PL: captures "00" + "950" (2 groups)
  → concatenate + normalize: "00950"
  → lookup ("PL", "00950") in TERCET → NUTS result

User input: "Traiskirchen"
  → regex for AT: no match
  → fallback normalize: "TRAISKIRCHEN"
  → lookup ("AT", "TRAISKIRCHEN") → 404
```

### Supported patterns

| Country | Format | Prefix | Example inputs |
|---------|--------|--------|----------------|
| AT | 4 digits | A-, AT- | `1010`, `A-1010`, `AT-1010` |
| BE | 4 digits | B-, BE- | `1000`, `B-1000`, `BE-1000` |
| BG | 4 digits | BG- | `1000`, `BG-1000` |
| CH | 4 digits | CH- | `8000`, `CH-8000` |
| CY | 4 digits | CY- | `1010`, `CY-1010` |
| CZ | 3+2 digits | CZ- | `11000`, `110 00`, `CZ-11000` |
| DE | 5 digits | D-, DE- | `10115`, `D-10115`, `DE-10115` |
| DK | 4 digits | DK- | `1050`, `DK-1050` |
| EE | 5 digits | EE- | `10111`, `EE-10111` |
| EL | 5 digits | GR- | `10431`, `GR-10431` |
| ES | 5 digits | E- | `28001`, `E-28001` |
| FI | 5 digits | FI- | `00100`, `FI-00100` |
| FR | 5 digits | F- | `75001`, `F-75001` |
| HR | 5 digits | HR- | `10000`, `HR-10000` |
| HU | 4 digits | H- | `1011`, `H-1011` |
| IE | Eircode | — | `D02 X285`, `A65 F4E2` |
| IS | 3 digits | IS- | `101`, `IS-101` |
| IT | 5 digits | I-, IT- | `00118`, `I-00118`, `IT-00118` |
| LI | 4 digits | FL- | `9490`, `FL-9490` |
| LT | 5 digits | LT- | `01100`, `LT-01100` |
| LU | 4 digits | L- | `1009`, `L-1009` |
| LV | 4 digits | — | `1010` |
| MK | 4 digits | MK- | `1000`, `MK-1000` |
| MT | 2-3 letters + 2-4 digits | — | `VLT 1010`, `MSK 1234` |
| NL | 4 digits + 2 letters | NL- | `1012 AB`, `NL-1012AB` |
| NO | 4 digits | N- | `0150`, `N-0150` |
| PL | 2+3 digits | PL- | `00-950`, `00950`, `PL-00-950` |
| PT | 4+3 digits | — | `1000-001`, `1000001` |
| RO | 6 digits | RO- | `010001`, `RO-010001` |
| RS | 5 digits | — | `11000` |
| SE | 3+2 digits | S-, SE- | `10005`, `100 05`, `S-10005` |
| SI | 4 digits | SI- | `1000`, `SI-1000` |
| SK | 3+2 digits | SK- | `81101`, `811 01`, `SK-81101` |
| TR | 5 digits | TR- | `06100`, `TR-06100` |

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

## Project structure

```
app/
├── main.py              # FastAPI app, endpoints (/lookup, /pattern, /health)
├── config.py            # Settings (env vars, country list, NUTS version)
├── data_loader.py       # TERCET download, parsing, SQLite cache, lookup()
├── models.py            # Pydantic response models
└── postal_patterns.py   # Per-country regex patterns + extract_postal_code()
tests/
├── AT01TestData.csv     # Austrian test postal codes (1628 rows)
└── BETestData.csv       # Belgian test postal codes (1284 rows)
```

## Test data results

Test data files contain real-world postal code inputs (including dirty data: city names, phone numbers, foreign codes, etc.).

| Country | Test rows | Match rate | Notes |
|---------|-----------|------------|-------|
| AT | 1628 | 88.2% | 192 misses: dirty data, foreign codes, codes not in TERCET |
| BE | 1284 | 84.5% | 199 misses: dirty data, foreign codes, codes not in TERCET |

## Data source

[GISCO TERCET flat files](https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units/postal-codes) ([download](https://gisco-services.ec.europa.eu/tercet/flat-files)), &copy; European Union &ndash; GISCO, licensed [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
