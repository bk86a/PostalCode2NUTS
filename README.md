# PostalCode2NUTS

FastAPI microservice that maps European postal codes to [NUTS codes](https://ec.europa.eu/eurostat/web/nuts) (Nomenclature of Territorial Units for Statistics) using [GISCO TERCET](https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units/postal-codes) flat files.

Returns NUTS levels 1, 2, and 3 for any postal code across 34 European countries and territories, with confidence scores indicating how the result was determined.

## Coverage

Based on GISCO TERCET correspondence tables. The NUTS version is determined automatically from the configured TERCET base URL (default: NUTS-2024).

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

**Example — exact match:**

```
GET /lookup?country=AT&postal_code=A-1010
```

```json
{
  "postal_code": "A-1010",
  "country_code": "AT",
  "match_type": "exact",
  "nuts1": "AT1",
  "nuts1_confidence": 1.0,
  "nuts2": "AT13",
  "nuts2_confidence": 1.0,
  "nuts3": "AT130",
  "nuts3_confidence": 1.0
}
```

**Example — estimated match:**

```
GET /lookup?country=AT&postal_code=1012
```

```json
{
  "postal_code": "1012",
  "country_code": "AT",
  "match_type": "estimated",
  "nuts1": "AT1",
  "nuts1_confidence": 0.98,
  "nuts2": "AT13",
  "nuts2_confidence": 0.95,
  "nuts3": "AT130",
  "nuts3_confidence": 0.9
}
```

Every response includes:

| Field | Description |
|-------|-------------|
| `match_type` | How the result was determined: `exact`, `estimated`, or `approximate` |
| `nuts{1,2,3}_confidence` | Confidence score (0.0–1.0) for each NUTS level |

See [Three-tier lookup](#three-tier-lookup) below for details on match types and confidence values.

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
  "total_estimates": 6477,
  "nuts_version": "2024",
  "extra_sources": 0,
  "data_stale": false,
  "last_updated": "2025-01-15T12:00:00+00:00"
}
```

| Field | Description |
|-------|-------------|
| `status` | `ok` if data is loaded, `no_data` otherwise |
| `extra_sources` | Number of extra ZIP source URLs configured (0 when not using extra sources) |
| `data_stale` | `true` if serving expired cache after a failed TERCET refresh |
| `last_updated` | ISO 8601 timestamp of when TERCET data was last successfully loaded |

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
| CZ | 3 digits + optional space + 2 digits | CZ- | `11000`, `110 00`, `CZ-11000` |
| DE | 5 digits | D-, DE- | `10115`, `D-10115`, `DE-10115` |
| DK | 4 digits | DK- | `1050`, `DK-1050` |
| EE | 5 digits | EE- | `10111`, `EE-10111` |
| EL | 5 digits or 2+3 / 3+2 with space | GR-, EL- | `10431`, `GR-10431`, `EL-10431`, `105 57` |
| ES | 5 digits | E- | `28001`, `E-28001` |
| FI | 5 digits | FI- | `00100`, `FI-00100` |
| FR | 5 digits | F- | `75001`, `F-75001` |
| HR | 5 digits | HR- | `10000`, `HR-10000` |
| HU | 4 digits | H- | `1011`, `H-1011` |
| IE | Eircode: letter + 2 digits (or 6W) + space + 4 alphanumerics; lookup uses routing key (first 3 chars) | — | `D02 X285`, `A65 F4E2` |
| IS | 3 digits | IS- | `101`, `IS-101` |
| IT | 5 digits | I-, IT- | `00118`, `I-00118`, `IT-00118` |
| LI | 4 digits | FL- | `9490`, `FL-9490` |
| LT | 5 digits | LT- | `01100`, `LT-01100` |
| LU | 4 digits | L- | `1009`, `L-1009` |
| LV | 4 digits; TERCET key prefixed with "LV" | LV-, LV | `1010`, `LV-1010`, `LV 1010` |
| MK | 4 digits | MK- | `1000`, `MK-1000` |
| MT | 2–3 letters + space + 2–4 digits; lookup uses letter prefix only | — | `VLT 1010`, `FNT 1010`, `MSK 1234` |
| NL | 4 digits + optional space + 2 letters | NL- | `1012 AB`, `NL-1012AB` |
| NO | 4 digits | N- | `0150`, `N-0150` |
| PL | 2 digits + optional dash + 3 digits | PL- | `00-950`, `00950`, `PL-00-950` |
| PT | 4 digits + optional dash + 3 digits | — | `1000-001`, `1000001` |
| RO | 6 digits | RO- | `010001`, `RO-010001` |
| RS | 5 digits | — | `11000` |
| SE | 3 digits + optional space + 2 digits | S-, SE- | `10005`, `100 05`, `S-10005`, `SE-10005` |
| SI | 4 digits | SI- | `1000`, `SI-1000` |
| SK | 3 digits + optional space + 2 digits | SK- | `81101`, `811 01`, `SK-81101` |
| TR | 5 digits | TR- | `06100`, `TR-06100`, `34000` |

## Configuration

All settings are overridable via environment variables prefixed with `PC2NUTS_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `PC2NUTS_TERCET_BASE_URL` | `https://gisco-services.ec.europa.eu/tercet/NUTS-2024/` (NUTS-2024 at present) | GISCO TERCET base URL. The NUTS version is derived from this URL. |
| `PC2NUTS_DATA_DIR` | `./data` | Cache directory for downloaded ZIPs and SQLite DB |
| `PC2NUTS_DB_CACHE_TTL_DAYS` | `30` | Days between automatic TERCET data refreshes. If the refresh fails, the service falls back to the previous data and sets `data_stale: true` in the health endpoint. |
| `PC2NUTS_ESTIMATES_CSV` | `./tests/tercet_missing_codes.csv` | Path to the estimates CSV. Loaded automatically at startup if the file exists. |
| `PC2NUTS_EXTRA_SOURCES` | *(empty)* | Comma-separated list of ZIP URLs containing additional postal code data. Loaded after TERCET; entries overwrite TERCET data. |

## Three-tier lookup

The service resolves postal codes using a three-tier fall-through strategy. Each tier adds coverage for codes not found by the tier above, and every result includes a `match_type` and per-level confidence scores so consumers can decide how much to trust the result.

### Tier 1: Exact match (`match_type: "exact"`)

Direct lookup in the TERCET correspondence table. The postal code exists verbatim in the official GISCO dataset.

- Confidence: **1.0** at all NUTS levels.
- This is the primary data source and covers the vast majority of valid European postal codes.

### Tier 2: Pre-computed estimate (`match_type: "estimated"`)

Lookup in a curated table of postal codes that are absent from TERCET but have been assigned estimated NUTS regions based on neighbouring codes and geographic analysis. These estimates are imported ahead of time via the `import_estimates` script (see below).

Each estimate carries a confidence label (high / medium / low) that is mapped to numerical scores per NUTS level:

| Label  | NUTS1 | NUTS2 | NUTS3 |
|--------|-------|-------|-------|
| high   | 0.98  | 0.95  | 0.90  |
| medium | 0.90  | 0.80  | 0.70  |
| low    | 0.70  | 0.55  | 0.40  |

Confidence is higher at coarser NUTS levels because neighbouring codes are more likely to share the same NUTS1 region than the same NUTS3 region.

### Tier 3: Runtime approximation (`match_type: "approximate"`)

If neither an exact match nor a pre-computed estimate exists, the service performs a runtime estimation using prefix matching against all known TERCET codes for that country.

**How it works:**

1. Find the longest prefix of the query postal code that matches one or more known postal codes.
2. Collect all NUTS3 codes for those matching neighbours.
3. Majority-vote at each NUTS level (NUTS3, NUTS2, NUTS1) to pick the most common region.
4. Compute confidence as:

   ```
   confidence = min(agreement_ratio * prefix_ratio, cap)
   ```

   - `agreement_ratio` = how many neighbours agree on the winning region / total neighbours
   - `prefix_ratio` = matched prefix length / full postal code length
   - Caps: 0.90 (NUTS1), 0.85 (NUTS2), 0.80 (NUTS3)

5. If confidence at NUTS1 level is below 0.1, the result is discarded entirely (returns 404).

### No match (404)

If all three tiers fail, the service returns a 404 with a format hint for the expected postal code pattern.

## How it works

On first startup the service downloads TERCET flat files (one ZIP per country), parses CSV/TSV contents, and builds an in-memory dict for O(1) lookups. Parsed data is then persisted to a SQLite cache so subsequent startups load in ~1 second instead of re-downloading and re-parsing.

The SQLite cache is scoped by the NUTS version derived from the base URL (e.g. `postalcode2nuts_NUTS-2024.db`), TTL-checked, and written atomically. Changing the base URL to a new NUTS version automatically creates a separate cache.

**Stale data fallback:** When the cache TTL expires and the service attempts a fresh download from TERCET, a failure (network error, server down) no longer results in empty data. Instead, the service falls back to the expired cache and continues serving lookups. The `/health` endpoint reports `data_stale: true` so monitoring systems can detect the condition. On the next restart the service will try to refresh again.

At startup the service also loads any pre-computed estimates from the DB, removes estimates that now have exact TERCET matches (revalidation), and builds a prefix index over all TERCET codes for runtime approximation.

## Estimates

Pre-computed NUTS estimates cover postal codes that are absent from TERCET. The service loads them automatically at startup from the CSV file configured via `PC2NUTS_ESTIMATES_CSV` (default: `./tests/tercet_missing_codes.csv`). No manual import step is needed — just update the CSV and restart.

**CSV format:**

```
COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE
AT,1012,AT130,AT13,AT1,high
AT,3082,AT123,AT12,AT1,low
```

The `CONFIDENCE` column accepts `high`, `medium`, or `low`. Rows with empty or unrecognized labels are skipped.

If the CSV is not present, estimates are loaded from the SQLite cache (backward-compatible with previous versions).

**Manual import (optional):**

The `scripts/import_estimates.py` script can be used to import estimates directly into the SQLite DB, e.g. for a custom CSV path or one-off imports:

```bash
python -m scripts.import_estimates --csv /path/to/estimates.csv --db /path/to/cache.db
```

## Extra data sources

You can supplement or override TERCET data by providing additional ZIP files containing postal code → NUTS3 mappings. This is useful for mirrors, corrections, or internal datasets.

**Configuration:**

Set the `PC2NUTS_EXTRA_SOURCES` environment variable to a comma-separated list of ZIP URLs:

```bash
export PC2NUTS_EXTRA_SOURCES="https://example.com/corrections_AT.zip,https://example.com/custom_data.zip"
```

**Expected ZIP/CSV format:**

Each ZIP must contain one or more CSV files with at least two columns:

| Column | Required | Aliases accepted |
|--------|----------|-----------------|
| Postal code | yes | `CODE`, `PC`, `POSTAL_CODE`, `POSTCODE`, `PC_FMT` |
| NUTS3 code | yes | `NUTS3_2024` (current version), `NUTS3`, `NUTS_ID`, `NUTS` |
| Country code | no | `COUNTRY_CODE`, `CC`, `CNTR_CODE` |

Minimal example CSV:

```csv
POSTAL_CODE,NUTS3,COUNTRY_CODE
1010,AT130,AT
10115,DE300,DE
```

**Country code resolution:**

The country code for each row is resolved in this order:

1. **CSV column** — if the file has a `COUNTRY_CODE` (or `CC`, `CNTR_CODE`) column, the per-row value is used
2. **URL filename** — if the URL matches the pattern `pc{year}_{CC}_...` (e.g. `pc2025_AT_custom.zip`), that country code is used for all rows
3. **Skip** — if neither is available, the file is skipped with a warning

**Conflict resolution:**

Sources are processed left-to-right in the order listed. All extra sources use last-write-wins, meaning they overwrite any existing TERCET entry for the same country + postal code combination. If the same key appears in multiple extra sources, the last source wins.

**Cache behavior:**

Changing the `PC2NUTS_EXTRA_SOURCES` list invalidates the SQLite cache automatically on the next startup, triggering a full rebuild.

## Project structure

```
app/
├── main.py              # FastAPI app, endpoints (/lookup, /pattern, /health)
├── config.py            # Settings (env vars, country list, NUTS version derived from URL)
├── settings.json        # Countries, confidence map, approximate thresholds
├── data_loader.py       # TERCET download, parsing, SQLite cache, three-tier lookup
├── models.py            # Pydantic response models
├── postal_patterns.py   # Pattern loading + extract_postal_code()
└── postal_patterns.json # Per-country regex patterns and examples
scripts/
└── import_estimates.py  # CLI: import pre-computed estimates into SQLite DB
```

Postal patterns and confidence settings are stored in JSON files (`postal_patterns.json`, `settings.json`) for easy editing without touching Python code. The `countries` list can still be overridden via the `PC2NUTS_COUNTRIES` environment variable.

## Data source

[GISCO TERCET flat files](https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units/postal-codes) ([download](https://gisco-services.ec.europa.eu/tercet/flat-files)), &copy; European Union &ndash; GISCO, licensed [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
