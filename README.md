# PostalCode2NUTS

FastAPI microservice that maps postal codes to [NUTS codes](https://ec.europa.eu/eurostat/web/nuts) (Nomenclature of Territorial Units for Statistics) for EU, EFTA, and EU candidate countries using [GISCO TERCET](https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units/postal-codes) flat files.

Returns NUTS levels 1, 2, and 3 for any postal code across 36 countries and 26 overseas
territories, with confidence scores indicating how the result was determined. Where a
territory lies outside the NUTS classification — an EU overseas country or territory, for
instance — the service says so explicitly rather than approximating a European region.

Runs in two tiers, selected at deploy time: a low-cost **Lite** tier (postal code → NUTS only) and an optional **Full** tier that adds an address → geocode → NUTS fallback — self-hosted [Photon](https://github.com/komoot/photon) geocoding plus NUTS polygons — for the rows postal lookup alone can't resolve. See [Deployment tiers](#deployment-tiers).

> **Try it live** (rate-limited public instance, Full tier): interactive docs at **<https://api.datatoolset.eu/PostalCode2NUTS/documentation>**. Quick check:
>
> ```bash
> curl 'https://api.datatoolset.eu/PostalCode2NUTS/lookup?country=DE&postal_code=10115'
> ```

## Coverage

Based on GISCO TERCET correspondence tables. The NUTS version is determined automatically from the configured TERCET base URL (default: NUTS-2024).

**EU-27** (27 countries):
Austria (AT), Belgium (BE), Bulgaria (BG), Croatia (HR), Cyprus (CY), Czechia (CZ), Denmark (DK), Estonia (EE), Finland (FI), France (FR), Germany (DE), Greece (EL), Hungary (HU), Ireland (IE), Italy (IT), Latvia (LV), Lithuania (LT), Luxembourg (LU), Malta (MT), Netherlands (NL), Poland (PL), Portugal (PT), Romania (RO), Slovakia (SK), Slovenia (SI), Spain (ES), Sweden (SE)

**EFTA** (4 countries):
Iceland (IS), Liechtenstein (LI), Norway (NO), Switzerland (CH)

**EU candidate countries** (5):
Albania (AL), North Macedonia (MK), Montenegro (ME), Serbia (RS), Türkiye (TR)

> **Montenegro** is treated by Eurostat as a single nationwide unit at every NUTS level (`ME0` / `ME00` / `ME000`), and GISCO does not currently publish a TERCET file for it. Lookups for ME are served by the single-NUTS3 fallback (Tier 5) configured via `single_nuts3_fallback` in `app/settings.json`, returning `ME000` for any valid 5-digit code starting with `8`.

> **Albania** has a full NUTS hierarchy (`AL0`; `AL01` / `AL02` / `AL03`; 12 NUTS3 counties `AL011`–`AL035`) but Eurostat publishes no GISCO TERCET file for it. Coverage is provided by an authoritative postal-code **block resolver** (`app/albania_blocks.py`): Albanian codes are block-allocated by district — the first two digits identify one of ~33 postal districts, each belonging to one of the 12 NUTS3 qarks — so a code resolves to its qark by its district prefix. Codes whose prefix belongs to no district (i.e. that don't exist) return not-found rather than a fabricated region. Lookups return `match_type="estimated"` with `high` confidence — see [Estimates](#estimates).

### Overseas regions and territories

EU primary law splits the non-continental territories of France, Portugal, Spain, Denmark
and the Netherlands into two categories, and only one of them exists in NUTS:

- **Outermost regions** (Art. 349 TFEU) are part of the EU and part of NUTS. All nine are
  supported: Guadeloupe (`FRY10`), Martinique (`FRY20`), French Guiana (`FRY30`), Réunion
  (`FRY40`), Mayotte (`FRY50`), the Azores (`PT200`), Madeira (`PT300`), the Canary Islands
  (`ES703`–`ES709` at island level), and Saint-Martin — which is an outermost region with no
  NUTS code of its own.
- **Overseas countries and territories** (Part Four TFEU, Annex II) are associated with the
  EU but not part of it, and have no NUTS regions at all. Annex II lists thirteen; ISO 3166-1
  covers them with eleven codes, since Bonaire, Saba and Sint Eustatius share `BQ`.

A further six European territories sit outside the ordinary country set: Svalbard and Jan
Mayen (`SJ`, which *is* in NUTS as `NO0B1`/`NO0B2`), the Faroe Islands, Gibraltar, Jersey,
Guernsey and the Isle of Man.

Twenty-three territory ISO codes are accepted as the `country` parameter — `GP` `MQ` `GF` `RE`
`YT` `MF` `GL` `PF` `NC` `WF` `PM` `BL` `TF` `AW` `CW` `SX` `BQ` `SJ` `FO` `GI` `JE` `GG` `IM`
— alongside the administering country's own code. Twenty-two of them are new in 2.0.0; `FO`
was already supported, and 59 country codes are now accepted in total. The Canary Islands, Azores and Madeira have
no ISO alpha-2 code and are reached only through `ES` and `PT` postal codes.

Every result inside a listed territory carries a `territory` block; results elsewhere carry
`territory: null`. See [`docs/overseas_territories.md`](docs/overseas_territories.md) for the
complete lists and per-territory behaviour.

### United Kingdom (ITL)

The UK left the EU, so it is no longer part of NUTS. Its successor classification, **ITL (International Territorial Level)**, is published by the ONS and mapped to postcodes via the [National Statistics Postcode Lookup (NSPL)](https://geoportal.statistics.gov.uk/). When configured (see `PC2NUTS_NSPL_URL`), the service accepts UK postcodes (`country=UK`, or `country=GB` as an alias) and returns ITL1/2/3 codes in the same `nuts1/2/3` fields, with `code_system: "ITL"` to distinguish them.

ITL is **not** a drop-in for NUTS-2016 UK: it diverges at L2 (41 vs 40 regions) and L3 (179 vs 174), and ONS discontinued the bidirectional NUTS↔ITL lookups in 2023. Branch on `code_system` when comparing UK results against historical NUTS-UK data.

UK coverage is **optional and operator-configured** — the ~178 MB NSPL ZIP is not bundled. When `PC2NUTS_NSPL_URL` is unset (the default), UK is unsupported and returns the standard `400`. Outward-code-only input (e.g. `SW1A`) resolves to the majority ITL3 for that outward code with `estimated`/medium confidence.

> **Out of scope for ITL:** Crown Dependencies (Jersey JE, Guernsey GG, Isle of Man IM) and Gibraltar (GI) use UK-style postcodes but are not in ITL geography or NSPL. They are registered territories (see [Overseas regions and territories](#overseas-regions-and-territories)): a well-formed UK-pattern code returns `200` with a `territory` block and `nuts_coverage: "none"`, not an ITL result.

## Upgrading to 2.0.0

`match_type`, `nuts1`/`nuts2`/`nuts3`, their `_name` and `_confidence` companions are now
nullable. Clients with strict schemas must widen those types before upgrading; clients that
read JSON dynamically need only handle `null`.

Eight postal ranges that previously returned a European NUTS region now return `null` with a
`territory` block: French `975xx`, `977xx`, `978xx`, `984xx`, `986xx`, `987xx`, `988xx`, and
Danish `39xx`. Those answers were wrong — `FR/98800` in Nouméa resolved to Alpes-Maritimes —
so the correction may reduce your match rate while improving its accuracy.

The fabricated Faroe Islands codes `FO0`/`FO00`/`FO000` are gone. They were invented by this
project, not published by Eurostat. `FO` lookups now return a `territory` block with null NUTS.

## Deployment tiers

PostalCode2NUTS runs in one of two tiers, chosen at deploy time by a single config
value — one codebase, one image.

| | **Lite** (default) | **Full** |
|---|---|---|
| Selected by | `PC2NUTS_PHOTON_URL` unset | `PC2NUTS_PHOTON_URL` set |
| Resolution | postal code → NUTS (5-tier) | postal → NUTS, then address → geocode → NUTS fallback |
| Endpoints | `/lookup`, `/pattern`, `/health`, `/resolve`\* | same, with `/resolve` geocoding active |
| Extra infrastructure | none | komoot Photon + NUTS-2024 polygons (~160 MB, auto-cached) |
| Footprint | ~35 MB data | ~100× (dominated by the Photon index) |
| Best for | high throughput, low cost, exact postal coverage | maximum coverage, messy/partial addresses |

\* In Lite, `/resolve` still answers but only from the postal path; its
`geocode.status` is `geocoder_unavailable`. `GET /health` reports `pip_ready` and
`geocoder_configured` so you can confirm the active tier.

**Why Full?** On a 900k-row real-world dataset the geocoding fallback resolves an
extra **~8.5%** of rows that postal lookup alone cannot — and up to **~33%** in
countries with weak postal→NUTS coverage (Netherlands, Malta, Ireland).

Lite is the default and needs nothing beyond the base image. To enable Full, see
[Full tier](#full-tier) below.

## Testing

The service has been tested against **134 million real-world postal codes** from 34 countries, sourced from 8 publicly available European datasets (GeoNames, OpenAddresses, GLEIF, SIRENE, TED, OffeneRegister, FTS, and Erasmus+ ECHE). All are open data published under permissive licenses (CC BY 4.0, CC0, or Licence Ouverte v2.0).

After deduplication, **970,083 unique postal codes** were tested with an overall success rate of **99.3%**. The remaining failures are predominantly data quality issues in the source datasets (placeholders, cross-country codes, legacy formats) rather than gaps in NUTS coverage.

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

See [Docker deployment](#docker-deployment) below for persistent volumes and production configuration.

### Full tier

The Full tier adds an address → coordinate → NUTS fallback for rows the postal path
can't resolve. It needs two things beyond Lite:

1. **A komoot Photon geocoder.** Photon serves an OpenStreetMap-derived index. You
   can build/download either the full planet index (~89 GB) or a smaller
   country-subset extract — pick what matches the countries you serve. Run it bound
   to loopback and point the app at it:

   ```bash
   # Obtain photon.jar and an index directory (see https://github.com/komoot/photon),
   # then serve it:
   java -jar photon.jar serve -data-dir /path/to/photon -listen-ip 127.0.0.1 -listen-port 2322
   ```

2. **NUTS polygons.** On first Full-tier startup the app downloads the GISCO
   NUTS-2024 (1:1M) polygons into `PC2NUTS_DATA_DIR` and caches them. To avoid the
   download, pre-seed the file and set `PC2NUTS_NUTS_GEOJSON_PATH`.

> **Hardware & space (Full tier).** Budget disk for the Photon index — roughly
> **90 GB** extracted for the full planet, or a few hundred MB to a few GB for a
> country-subset extract — plus **~160 MB** for the cached NUTS polygons. Photon
> memory-maps its index, so throughput scales with available RAM: a single-country
> index runs comfortably on a small VM, while the full planet is happiest with an
> SSD and **~8–16 GB RAM**. The Lite tier needs none of this — just the ~35 MB
> SQLite dataset.

Enable Full mode by setting `PC2NUTS_PHOTON_URL`. With Docker Compose:

```bash
docker compose -f compose.yaml -f compose.full.yaml up -d
```

Confirm the tier is live:

```bash
curl -s localhost:8000/health | jq '{pip_ready, geocoder_configured}'
# → {"pip_ready": true, "geocoder_configured": true}
```

Addresses are sent to your Photon instance only; they are never logged.

## Endpoints

| Endpoint  | Description |
|-----------|-------------|
| `GET /lookup` | Look up NUTS 1/2/3 codes for a postal code + country |
| `GET /pattern` | Get the postal code regex pattern for a country |
| `GET /resolve` | Resolve NUTS with a geocoding fallback for weak postal results (Full tier) |
| `GET /health` | Health check with data statistics, including which tier is active |
| `GET /` | Service entry point — links to docs, health, and example requests |

Interactive API docs are available at `/documentation` (Swagger UI) and `/redoc`. To disable in production, set `PC2NUTS_DOCS_ENABLED=false`.

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
  "code_system": "NUTS",
  "match_type": "exact",
  "nuts1": "AT1",
  "nuts1_name": "Ostösterreich",
  "nuts1_confidence": 1.0,
  "nuts2": "AT13",
  "nuts2_name": "Wien",
  "nuts2_confidence": 1.0,
  "nuts3": "AT130",
  "nuts3_name": "Wien",
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
  "nuts1_name": "Ostösterreich",
  "nuts1_confidence": 0.98,
  "nuts2": "AT13",
  "nuts2_name": "Wien",
  "nuts2_confidence": 0.95,
  "nuts3": "AT130",
  "nuts3_name": "Wien",
  "nuts3_confidence": 0.9
}
```

**Example — UK postcode (ITL):**

```
GET /lookup?country=UK&postal_code=SW1A%202AA
```

```json
{
  "postal_code": "SW1A2AA",
  "country_code": "UK",
  "code_system": "ITL",
  "match_type": "exact",
  "nuts1": "TLI",
  "nuts1_name": "London",
  "nuts1_confidence": 1.0,
  "nuts2": "TLI3",
  "nuts2_name": "Inner London - East",
  "nuts2_confidence": 1.0,
  "nuts3": "TLI32",
  "nuts3_name": "Tower Hamlets and Newham",
  "nuts3_confidence": 1.0
}
```

`country=GB` is accepted as an alias for `UK`. See [United Kingdom (ITL)](#united-kingdom-itl) for the NUTS-vs-ITL distinction.

Every response includes:

| Field | Description |
|-------|-------------|
| `code_system` | Territorial scheme of the `nuts{1,2,3}` fields: `NUTS` for EU/EFTA/candidate data, `ITL` for UK data (see [United Kingdom (ITL)](#united-kingdom-itl)) |
| `match_type` | How the result was determined: `exact`, `estimated`, or `approximate` |
| `nuts{1,2,3}_name` | Human-readable region name (Latin script), or `null` if unavailable |
| `nuts{1,2,3}_confidence` | Confidence score (0.0–1.0) for each NUTS level |

See [Five-tier lookup](#five-tier-lookup) below for details on match types and confidence values.

#### The `territory` block

Present when the postal code lies in an overseas region or territory; `null` otherwise.

| Field | Meaning |
|---|---|
| `id` | Registry id — the ISO code where one exists, else `ES-CN`, `PT-20`, `PT-30` |
| `iso` | ISO 3166-1 alpha-2 code, or `null` for the three without one |
| `name` | Territory name |
| `status` | `outermost_region`, `oct`, or `other` |
| `administering_country` | ISO code of the administering country |
| `legal_basis` | Treaty provision, e.g. `TFEU Art. 349` |
| `note` | Plain-language explanation |
| `nuts_coverage` | `full`, `tercet_entry_only`, or `none` |

`nuts_coverage` tells you what to expect from the NUTS fields:

- **`full`** — Eurostat classifies the territory and the code resolved normally.
- **`tercet_entry_only`** — the territory is outside NUTS, but the GISCO TERCET file carries
  this exact code. The NUTS fields are populated from Eurostat's own row and flagged. Only
  Saint-Barthélemy's `97133` behaves this way.
- **`none`** — no NUTS code exists. `match_type` and all six NUTS fields are `null`, and the
  response is still `200`: the absence is the answer, not an error.

```bash
curl 'https://api.datatoolset.eu/PostalCode2NUTS/lookup?country=NC&postal_code=98800'
```

```json
{
  "postal_code": "98800",
  "country_code": "NC",
  "code_system": "NUTS",
  "match_type": null,
  "nuts1": null, "nuts1_name": null, "nuts1_confidence": null,
  "nuts2": null, "nuts2_name": null, "nuts2_confidence": null,
  "nuts3": null, "nuts3_name": null, "nuts3_confidence": null,
  "territory": {
    "id": "NC",
    "iso": "NC",
    "name": "New Caledonia",
    "status": "oct",
    "administering_country": "FR",
    "legal_basis": "TFEU Part Four, Annex II",
    "note": "Associated with the EU; outside the NUTS classification.",
    "nuts_coverage": "none"
  }
}
```

An outermost region resolves as usual and is labelled:

```json
{
  "postal_code": "97400",
  "country_code": "RE",
  "code_system": "NUTS",
  "match_type": "exact",
  "nuts1": "FRY", "nuts1_name": "RUP FR — Régions Ultrapériphériques Françaises", "nuts1_confidence": 1.0,
  "nuts2": "FRY4", "nuts2_name": "La Réunion", "nuts2_confidence": 1.0,
  "nuts3": "FRY40", "nuts3_name": "La Réunion", "nuts3_confidence": 1.0,
  "territory": {
    "id": "RE", "iso": "RE", "name": "Réunion",
    "status": "outermost_region", "administering_country": "FR",
    "legal_basis": "TFEU Art. 349", "note": null, "nuts_coverage": "full"
  }
}
```

#### Two routes to the same answer

A territory is reachable by its own ISO code or by the administering country's code, and both
return the same body apart from the echoed `country_code`: `GL/3900` = `DK/3900`,
`NC/98800` = `FR/98800`, `SJ/9170` = `NO/9170`.

Substitution is asymmetric, though. A code that is well-formed for the administering country
but does not belong to the named territory returns `404` rather than the mainland answer —
`GL/2100` is a valid Danish code for Copenhagen, but it is not Greenlandic, and `SJ/0150` is
Oslo, not Svalbard.

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
["AL", "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "EL", "ES", "FI", "FO", "FR", "HR", "HU", "IE", "IS", "IT", "LI", "LT", "LU", "LV", "ME", "MK", "MT", "NL", "NO", "PL", "PT", "RO", "RS", "SE", "SI", "SK", "TR"]
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

A territory code returns the administering country's pattern — `GET /pattern?country=RE`
echoes `country_code: "RE"` with FR's regex. Aruba, Curaçao, Sint Maarten and Bonaire/Saba/Sint
Eustatius (`AW`, `CW`, `SX`, `BQ`) use no postal codes at all, so this endpoint returns `404`
for them rather than a pattern.

### `GET /resolve`

Full tier. Resolves NUTS with a geocoding fallback: it runs the postal lookup first
and, only when that result is weak (`not_found`, or `nuts3_confidence` below
`PC2NUTS_RESOLVE_CONFIDENCE_THRESHOLD`, default `0.85`) **and** a street/city was
supplied, geocodes the address via Photon and maps the coordinate to a NUTS-3 region
by point-in-polygon. A geocode that lands just outside a (generalized) polygon is
snapped to the nearest same-country region within `PC2NUTS_PIP_SNAP_KM` (default
`2.0` km); cross-country snapping is refused.

**Query parameters:** `country` (2-letter, required), `postal_code` (required),
`street` (optional), `city` (optional).

```bash
curl -s "localhost:8000/resolve?country=NL&postal_code=1012AB&street=Dam&city=Amsterdam"
```

**Response fields:** `country_code`, `postal_code`, `resolved_via`
(`postal` | `geocode` | `none`), `match_type`, `nuts1..nuts3` (+ `_name`),
`nuts3_confidence`, `territory` (see [The `territory` block](#the-territory-block)), and a
`geocode` object: `status`, `lat`, `lon`, `nuts3`, `snap_km`.

`geocode.status` values:

| status | meaning |
|---|---|
| `not_attempted` | postal result was strong enough, or the code is inside a territory with `nuts_coverage: "none"`; geocoding skipped |
| `ok` | geocoded and the point fell inside a NUTS-3 region |
| `snapped` | point was just outside; snapped to the nearest same-country region (`snap_km`) |
| `pip_outside` | geocoded but the point is outside all NUTS regions and beyond the snap cap |
| `no_result` | the geocoder found no coordinate |
| `no_address` | postal result was weak but no street/city was supplied |
| `geocoder_unavailable` | Lite tier — no geocoder configured |

In Lite, `/resolve` returns the postal result with `geocode.status: geocoder_unavailable`.

A territory outside NUTS (`nuts_coverage: "none"`) skips the geocoder entirely, even in Full —
no NUTS polygon covers those territories, so there is nothing for point-in-polygon to resolve
against. `resolved_via` is `"none"` and `geocode.status` is `"not_attempted"`.

### `GET /health`

Returns service status and data statistics.

```json
{
  "status": "ok",
  "total_postal_codes": 830032,
  "total_estimates": 6477,
  "total_nuts_names": 2190,
  "nuts_version": "2024",
  "extra_sources": 0,
  "patterns_version": "1.2",
  "data_stale": false,
  "last_updated": "2025-01-15T12:00:00+00:00",
  "token_db_stale": null,
  "estimates_refresh_stale": null,
  "geocoder_configured": false,
  "pip_ready": false,
  "territories": 26
}
```

| Field | Description |
|-------|-------------|
| `status` | `ok` if data is loaded, `no_data` otherwise |
| `total_nuts_names` | Number of NUTS region names loaded (0 if names CSV unavailable) |
| `extra_sources` | Number of extra ZIP source URLs configured (0 when not using extra sources) |
| `patterns_version` | Version of the `postal_patterns.json` file |
| `data_stale` | `true` if serving expired cache after a failed TERCET refresh |
| `last_updated` | ISO 8601 timestamp of when TERCET data was last successfully loaded |
| `token_db_stale` | `true` if the DB-backed trusted-token registry failed its last refresh; `null` if `PC2NUTS_TOKEN_DB_URL` is unset |
| `estimates_refresh_stale` | `true` if the last periodic estimates refresh failed; `null` if `PC2NUTS_ESTIMATES_REFRESH_URL` is unset |
| `geocoder_configured` | `true` if `PC2NUTS_PHOTON_URL` is set (Full tier active) |
| `pip_ready` | `true` if NUTS polygons are loaded, i.e. point-in-polygon resolution is available for `/resolve` |
| `territories` | Number of entries in the territory registry (currently 26) |

## Error handling

The API uses standard HTTP status codes with human-readable error messages:

| Status | Meaning | When |
|--------|---------|------|
| **200** | Success | Lookup found, pattern returned, health OK, or resolve completed (even when unresolved — see `resolved_via: "none"`) |
| **400** | Bad request | Country code is not supported (lists available countries) |
| **404** | Not found | Postal code not found (shows expected format), no pattern for country, a postal code that is well-formed but does not belong to the named territory, or a `/pattern` request for a territory with no postal system. `/resolve` never 404s. |
| **422** | Validation error | Parameter format invalid (e.g. country code not 2 letters, contains digits) |
| **429** | Too many requests | Rate limit exceeded (configurable via `PC2NUTS_RATE_LIMIT`) |

**Examples:**

Unsupported country (400):
```json
{"detail": "Country 'XX' is not supported. Available countries: AT, BE, BG, CH, ..."}
```

Postal code not found (404) — includes expected format hint:
```json
{"detail": "No NUTS mapping found for postal code 'Traiskirchen' in country 'AT'. Expected format: 1010, A-1010, AT-1010"}
```

Code outside the named territory (404) — `GL/2100` is a valid Danish code, but not Greenlandic:
```json
{"detail": "Postal code '2100' is not a Greenland code. Greenland uses the DK postal scheme; codes outside its range belong to another territory or to DK itself."}
```

Territory with no postal system, on `/pattern` (404):
```json
{"detail": "Aruba uses no postal codes."}
```

Invalid parameter format (422):
```json
{"detail": [{"msg": "String should match pattern '^[A-Za-z]{2}$'", "input": "12"}]}
```

## Postal code input patterns

The service uses per-country regex patterns (`app/postal_patterns.py`) to handle real-world postal code input variations:

- **Input preprocessing** — fixes common data artifacts before regex matching (see below)
- **Country prefix stripping** — `A-1010`, `D-10115`, `B-1000` are stripped to their numeric postal codes before lookup
- **Internal formatting** — dashes in Polish codes (`00-950`), spaces in Dutch codes (`1012 AB`), etc. are normalized
- **Fallback** — if the input doesn't match the country pattern, generic normalization is applied (strip all non-alphanumeric, uppercase)

### Input preprocessing

Before regex matching, raw input is preprocessed to recover postal codes mangled by Excel, CSV exports, or database dumps:

| Step | Problem | Example | Result |
|------|---------|---------|--------|
| Remove dot thousands | Dot-as-thousand-separator formatting | `13.600` | `13600` |
| Strip `.0` suffix | Excel stores numbers as floats | `28040.0` | `28040` |
| Restore leading zeros | Excel strips leading zeros from numbers | `8461` (ES) | `08461` |

Leading-zero restoration uses the `expected_digits` metadata in `postal_patterns.json` and only triggers when the input is all-digit and exactly one digit short. Countries with non-numeric postal codes (IE, MT, NL) are excluded.

### Pattern extraction flow

```
User input: "28040.0" (country: ES)
  → preprocess: strip .0 → "28040"
  → regex match for ES: captures "28040"
  → lookup ("ES", "28040") in TERCET → NUTS result

User input: "8461" (country: ES)
  → preprocess: pad to expected_digits=5 → "08461"
  → regex match for ES: captures "08461"
  → lookup ("ES", "08461") in TERCET → NUTS result

User input: "A-5600"
  → preprocess: no changes (not numeric-only)
  → regex match for AT: captures "5600"
  → normalize: "5600"
  → lookup ("AT", "5600") in TERCET → NUTS result

User input: "00-950"
  → preprocess: no changes (contains dash)
  → regex match for PL: captures "00" + "950" (2 groups)
  → concatenate + normalize: "00950"
  → lookup ("PL", "00950") in TERCET → NUTS result

User input: "Traiskirchen"
  → preprocess: no changes (not numeric)
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
| FO | 3 digits (outside NUTS — returns a `territory` block, `nuts_coverage: "none"`) | FO- | `100`, `FO-100`, `FO 100` |
| FR | 5 digits | F- | `75001`, `F-75001` |
| HR | 5 digits | HR- | `10000`, `HR-10000` |
| HU | 4 digits | H- | `1011`, `H-1011` |
| IE | Eircode: letter + 2 digits (or 6W) + optional space + 4 alphanumerics; lookup uses routing key (first 3 chars) | — | `D02 X285`, `D02X285`, `A65 F4E2` |
| IS | 3 digits | IS- | `101`, `IS-101` |
| IT | 5 digits | I-, IT- | `00118`, `I-00118`, `IT-00118` |
| LI | 4 digits | FL- | `9490`, `FL-9490` |
| LT | 5 digits | LT- | `01100`, `LT-01100` |
| LU | 4 digits | L- | `1009`, `L-1009` |
| LV | 4 digits; TERCET key prefixed with "LV" | LV-, LV | `1010`, `LV-1010`, `LV 1010` |
| ME | 5 digits, must start with `8` (no GISCO TERCET data — resolves to `ME000` via Tier 5) | ME- | `81000`, `ME-81000`, `ME 85320` |
| MK | 4 digits | MK- | `1000`, `MK-1000` |
| MT | 2–3 letters + optional separator + 2–4 digits; lookup uses letter prefix only | — | `VLT 1010`, `MST1000`, `FNT-1010` |
| NL | 4 digits + optional space + 2 letters | NL- | `1012 AB`, `NL-1012AB` |
| NO | 4 digits | N- | `0150`, `N-0150` |
| PL | 2 digits + optional dash + 3 digits | PL- | `00-950`, `00950`, `PL-00-950` |
| PT | 4 digits + optional dash or space + 3 digits | — | `1000-001`, `1000 001`, `1000001` |
| RO | 6 digits | RO- | `010001`, `RO-010001` |
| RS | 5 digits | — | `11000` |
| SE | 3 digits + optional space + 2 digits | S-, SE- | `10005`, `100 05`, `S-10005`, `SE-10005` |
| SI | 4 digits | SI- | `1000`, `SI-1000` |
| SK | 3 digits + optional space + 2 digits | SK- | `81101`, `811 01`, `SK-81101` |
| TR | 5 digits | TR- | `06100`, `TR-06100`, `34000` |
| UK | 1–2 letters + digit + optional letter/digit + optional space + digit + 2 letters (ITL via NSPL; requires `PC2NUTS_NSPL_URL`) | GB accepted as alias | `SW1A 2AA`, `EC1A 1BB`, `M1 1AA`, `B33 8TH`, `SW1A` (outward only) |

## Configuration

All settings are overridable via environment variables prefixed with `PC2NUTS_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `PC2NUTS_TERCET_BASE_URL` | *(from `settings.json`, currently NUTS-2024)* | GISCO TERCET base URL. The NUTS version is derived from this URL. |
| `PC2NUTS_DATA_DIR` | `./data` | Cache directory for downloaded ZIPs and SQLite DB |
| `PC2NUTS_DB_CACHE_TTL_DAYS` | `30` | Days between automatic TERCET data refreshes. If the refresh fails, the service falls back to the previous data and sets `data_stale: true` in the health endpoint. |
| `PC2NUTS_ESTIMATES_CSV` | `./tercet_missing_codes.csv` | Path to the estimates CSV. Loaded automatically at startup if the file exists. |
| `PC2NUTS_EXTRA_SOURCES` | *(empty)* | Comma-separated list of ZIP URLs containing additional postal code data. Loaded after TERCET; entries overwrite TERCET data. |
| `PC2NUTS_NSPL_URL` | *(empty)* | URL to the latest [NSPL](https://geoportal.statistics.gov.uk/) ZIP from the ONS Open Geography Portal. Enables UK (ITL) support; when unset, UK is unsupported. The URL changes each quarterly release, so update it accordingly. |
| `PC2NUTS_UK_ITL_LOOKUP_URL` | *(empty — use bundled)* | Override for the bundled ONS LAD→ITL map (`app/uk_lad_itl.csv`). Point at a refreshed export in the same shape when ONS bumps the ITL vintage; empty uses the bundled map. |
| `PC2NUTS_RATE_LIMIT` | `120/minute` | Rate limit for `/lookup` and `/pattern` endpoints. Uses [slowapi](https://github.com/laurentS/slowapi) syntax (e.g. `100/minute`, `5/second`). `/health` is exempt. The default leaves comfortable headroom under the measured aggregate ceiling (~30 RPS) — see [`docs/performance.md`](docs/performance.md) for the rationale. |
| `PC2NUTS_RATE_LIMIT_HEADERS` | `true` | When `true`, `429` responses include `Retry-After` and `X-RateLimit-Limit` / `X-RateLimit-Remaining` headers. |
| `PC2NUTS_CACHE_MAX_AGE` | `3600` | `Cache-Control: public, max-age=<n>` (seconds) set on `/lookup`, `/pattern`, and `/` responses. |
| `PC2NUTS_STARTUP_TIMEOUT` | `300` | Maximum seconds allowed for initial data loading. If exceeded, the service starts with whatever data was loaded and sets `data_stale: true`. |
| `PC2NUTS_TRUSTED_TOKENS` | `""` (empty — bypass disabled) | Comma-separated list of opaque tokens that bypass the per-IP rate limit when sent via `Authorization: Bearer <token>`. Continues to work as a union with the DB-backed registry below; set this only as a disaster-recovery fallback or for env-var-only deployments. See [Authentication & rate-limit bypass](#authentication--rate-limit-bypass) for the operator runbook. |
| `PC2NUTS_TOKEN_DB_URL` | `""` (unset) | Connection string for the trusted-token database. Accepts both `https://…` and `libsql://…` (the latter is rewritten to `https://` automatically). Empty → DB-backed bypass disabled, falls back to env-var-only behaviour. |
| `PC2NUTS_TOKEN_DB_AUTH_TOKEN` | `""` (unset) | Bearer JWT presented to the trusted-token database. Required when the provider enforces auth. |
| `PC2NUTS_TOKEN_REFRESH_SECONDS` | `60` (min `1`) | How often the running service reloads the active trusted-token set from the DB. |
| `PC2NUTS_DOCS_ENABLED` | `true` | Set to `false` to disable Swagger UI (`/documentation`) and ReDoc (`/redoc`) in production. |
| `PC2NUTS_DOCS_URL` | `/documentation` | Path for the interactive API docs UI. |
| `PC2NUTS_CORS_ORIGINS` | `*` | Comma-separated list of allowed CORS origins. Set to a specific origin (e.g. `https://example.com`) to restrict cross-origin access. Empty string disables CORS middleware. |
| `PC2NUTS_ACCESS_LOG_FILE` | *(empty — stdout)* | Path to access log file. When set, logs are written to this file with automatic rotation. When empty, access logs go to stderr. |
| `PC2NUTS_ACCESS_LOG_MAX_MB` | `10` | Maximum size of each access log file in MB before rotation. |
| `PC2NUTS_ACCESS_LOG_BACKUP_COUNT` | `5` | Number of rotated access log files to keep (e.g. 5 x 10 MB = 50 MB max disk usage). |
| `PC2NUTS_ESTIMATES_REFRESH_URL` | *(empty — feature disabled)* | When set, the worker periodically fetches this URL and replaces the in-memory estimates table. Recommended value: `https://raw.githubusercontent.com/bk86a/PostalCode2NUTS/main/tercet_missing_codes.csv`. |
| `PC2NUTS_ESTIMATES_REFRESH_INTERVAL_SECONDS` | `86400` (24 h) | How often the periodic task fetches the URL. Set to `0` to disable the periodic loop while keeping the bootstrap fetch on startup. |
| `PC2NUTS_PHOTON_URL` | _(unset)_ | Photon geocoder base URL. **Unset ⇒ Lite tier.** Set ⇒ Full tier. |
| `PC2NUTS_PHOTON_TIMEOUT_SECONDS` | `5.0` | Request timeout (seconds) for calls to the Photon geocoder (Full tier). |
| `PC2NUTS_NUTS_GEOJSON_URL` | GISCO NUTS-2024 1M zip | Source for NUTS polygons (Full tier); auto-downloaded + cached. |
| `PC2NUTS_NUTS_GEOJSON_PATH` | _(unset)_ | Pre-seeded polygon file; skips the download when set. |
| `PC2NUTS_RESOLVE_CONFIDENCE_THRESHOLD` | `0.85` | Geocode only when the postal `nuts3_confidence` is below this. |
| `PC2NUTS_PIP_SNAP_KM` | `2.0` | Max distance to snap a just-outside point to the nearest same-country NUTS-3 region. `0` disables snapping. |

### Multi-worker deployment

By default the service runs a single uvicorn worker process. Throughput is
CPU-bound at ~30 RPS per worker (see [docs/performance.md](docs/performance.md)).
For higher RPS, set `PC2NUTS_WORKERS` to the number of worker processes you
want — the rough rule of thumb is one worker per CPU core, capped by the
available memory (~150-200 MB resident set per worker).

| Env var | Default | Effect |
|---|---|---|
| `PC2NUTS_WORKERS` | `1` | Number of uvicorn worker processes. |
| `PC2NUTS_RATE_LIMIT_STORAGE_URI` | (unset) | When unset, slowapi uses per-process in-memory storage (default). When set (e.g. `redis://host:6379/0`), counters are shared across workers so the published `rate_limit` cap stays accurate. |

When `PC2NUTS_WORKERS > 1`, `PC2NUTS_RATE_LIMIT_STORAGE_URI` MUST be set
to a reachable shared backend; the service refuses to start otherwise.
This guards against the per-IP rate limit silently loosening to
`PC2NUTS_WORKERS × rate_limit` per IP under multi-worker.

**Degraded mode.** If the configured storage backend becomes unreachable
at runtime, slowapi (`in_memory_fallback_enabled=True`) falls back to
per-process in-memory rate limiting and re-probes the primary storage
with exponential backoff. During the outage window the effective per-IP
cap is `PC2NUTS_WORKERS × rate_limit`. Recovery is automatic; one
WARNING log line is emitted at the start of the outage and one INFO line
on recovery.

## Authentication & rate-limit bypass

The service applies a per-IP rate limit (`120/minute` by default) to `/lookup` and `/pattern`. Trusted callers — operator-issued, manually distributed — can bypass this limit by presenting an `Authorization: Bearer <token>` header. `/health` stays anonymous.

### Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PC2NUTS_TRUSTED_TOKENS` | `""` (unset) | Comma-separated list of valid bypass tokens. Empty → bypass disabled (current default). Whitespace and empty entries between commas are tolerated. |

### Storage backend

By default, trusted tokens live in the `PC2NUTS_TRUSTED_TOKENS` env var (comma-separated; restart-to-apply). When `PC2NUTS_TOKEN_DB_URL` is configured, tokens are also loaded from a managed SQLite-compatible database; the active set is the **union** of DB-loaded tokens and env-var tokens. The DB is the primary registry; the env var serves as a disaster-recovery fallback for cases where the DB is unreachable at startup.

| Env var | Default | Purpose |
|---|---|---|
| `PC2NUTS_TOKEN_DB_URL` | `""` (unset) | Connection string for the token database. Accepts both `https://…` and `libsql://…` (the latter is rewritten to `https://` automatically). Empty → DB-backed feature disabled (v0.16.0 env-var-only behaviour). |
| `PC2NUTS_TOKEN_DB_AUTH_TOKEN` | `""` (unset) | Bearer JWT presented to the database in the `Authorization` header on every request. Required when the database enforces auth (most managed offerings do). |
| `PC2NUTS_TOKEN_REFRESH_SECONDS` | `60` (min `1`) | How often the running service reloads the active set from the DB. |

The wire protocol assumed by the client is **libsql / Hrana v2** (`POST /v2/pipeline` with statements wrapped as `{requests: [{type: "execute", stmt: {sql, args}}]}`); this matches Bunny Database, Turso, and any other libsql-compatible service. If your provider uses a different wire shape, only `TokenDB.execute` in `app/token_db.py` needs adjusting — the rest of the system is insulated by mock-at-the-boundary unit tests.

### Operator runbook — initial setup (one-time)

```bash
# On your laptop, with both DB env vars set:
export PC2NUTS_TOKEN_DB_URL='<libsql:// or https:// URL from the provider>'
export PC2NUTS_TOKEN_DB_AUTH_TOKEN='<JWT issued by the provider>'
python -m scripts.tokens init
# → idempotent. Safe to re-run.
```

Both values can also be passed per-invocation via `--db-url` / `--auth-token` flags if you prefer not to export them.

### Operator runbook — issue a token

```bash
python -m scripts.tokens add --label "alice-batch-2026-04"
# Generated: e3a1f2…d4
# Inserted id=3, label='alice-batch-2026-04', token_id=9a29f07a

# The token is active in the running service within ~PC2NUTS_TOKEN_REFRESH_SECONDS.
# Hand the printed token to the consumer over a confidential channel
# (1Password, Signal, encrypted email — not Slack, not GitHub issues).
```

### Operator runbook — list tokens

```bash
python -m scripts.tokens list           # active only (default)
python -m scripts.tokens list --all     # include revoked
```

The `value` column is never printed.

### Operator runbook — revoke a token

```bash
python -m scripts.tokens revoke 3
# Token id=3 revoked.
# (Already-revoked ids print "already revoked" and exit 0.)
```

Revocation takes effect within `PC2NUTS_TOKEN_REFRESH_SECONDS` (default 60 s). For an emergency revocation, restart the container to force an immediate refresh.

### Operator runbook — find the token id of a logged request

```bash
echo -n "<token>" | sha256sum | cut -c1-8
```

The CLI's `add` command also prints the `token_id` at issuance time.

### Operator runbook — migrate a v1 env-var token

To preserve the audit `token_id` of an existing env-var token when moving it to the DB:

```bash
python -m scripts.tokens add --label "perf-test-2026-04-29" --value "<the existing 48-hex token>"
```

Then remove that token from `PC2NUTS_TRUSTED_TOKENS` on the next config edit.

### Operator runbook — manually refresh estimates

When you've merged a new entry into `tercet_missing_codes.csv` on `main` and want to verify it's live in production without waiting up to 24 hours for the next periodic refresh:

```bash
curl -X POST -H "Authorization: Bearer $PC2NUTS_TRUSTED_TOKEN" \
  https://api.example.invalid/admin/refresh-estimates
```

Possible responses:

| HTTP | `status` | Meaning |
|---|---|---|
| 200 | `refreshed` | Upstream content changed, sanity guard passed, in-memory state updated. Body includes `previous_count` and `new_count`. |
| 200 | `unchanged` | Upstream content identical to current state (matched by SHA-256 hash or 304 Not Modified). |
| 401 | — | Missing or invalid `Authorization` header. |
| 409 | `rejected` | Sanity guard refused the candidate CSV (< 50 % of current row count). Live state is untouched. |
| 502 | `failed` | Upstream fetch or parse failed. Live state is untouched. |
| 503 | `disabled` | `PC2NUTS_ESTIMATES_REFRESH_URL` is unset on the deployed pod. |

`/health` exposes `estimates_refresh_stale: bool | None` — `null` when disabled, `false` after a successful most-recent refresh, `true` after a failed one.

### Behaviour summary

| Request | Result |
|---|---|
| No `Authorization` header | Per-IP `120/minute` cap, normal `200` / `429` |
| `Authorization: Bearer <valid_token>` | Rate limit fully bypassed; `token_id=<8hex>` appended to access log |
| `Authorization: Bearer <unknown_token>` | `401 Unauthorized` |
| `Authorization: <not Bearer>` or malformed | `400 Bad Request` |

### Disable the bypass entirely

Unset all three: `PC2NUTS_TOKEN_DB_URL`, `PC2NUTS_TOKEN_DB_AUTH_TOKEN`, and `PC2NUTS_TRUSTED_TOKENS`. All traffic falls back to the per-IP cap. The `Authorization` header is ignored entirely (no 400, no 401) when the feature is disabled. No code change needed.

If only the DB env vars are unset (`PC2NUTS_TRUSTED_TOKENS` still set), behaviour reverts exactly to v0.16.0 (env-var only).

### Security notes

- Tokens are **bearer credentials** — anyone holding the string can use the API at full rate. Treat them like passwords.
- Always send tokens over HTTPS. Never accept a bearer token over plain HTTP.
- Log lines contain only the 8-char SHA-256 prefix. Token values never appear in logs.
- Token comparison is constant-time (`hmac.compare_digest`).
- Revocation latency is bounded by container restart time (~30 s).

## Five-tier lookup

This section describes the **postal-code → NUTS resolution path** — the sole mechanism behind `GET /lookup`, and the first stage that `GET /resolve` always tries before falling back to geocoding. It is identical in both the Lite and Full tiers.

The service resolves postal codes using a five-tier fall-through strategy. Each tier adds coverage for codes not found by the tier above, and every result includes a `match_type` and per-level confidence scores so consumers can decide how much to trust the result.

Before any tier runs, the territory registry classifies the input. For a territory outside
NUTS only Tier 1 may run — every approximation tier is unreachable, which is what stops an
OCT postal code from being answered with a neighbouring European region.

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

### UK outward-code lookup (`match_type: "estimated"`) — UK only

For UK postcodes (loaded from NSPL — see [United Kingdom (ITL)](#united-kingdom-itl)), when the full postcode is not an exact match, the service looks up the **outward code** — everything before the final three characters, e.g. `SW1A` for `SW1A 2AA`. An index built at load time maps each outward code to the majority-vote ITL3 among all postcodes sharing it. This runs ahead of the generic prefix approximation below because the outward code is the meaningful UK boundary. It also handles outward-only input (`SW1A` submitted alone). Confidence uses the medium tier (NUTS1 0.90 / NUTS2 0.80 / NUTS3 0.70), since one outward code can straddle two adjacent ITL3 regions in dense urban areas.

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

5. If confidence at NUTS1 level is below 0.1, the result is discarded entirely and falls through to the next tier.

### Tier 4: Country-level majority vote (`match_type: "approximate"`)

For countries where all postal codes map to the same NUTS1 and NUTS2 region but NUTS3 has a dominant winner, the service returns an approximate result using the country-wide distribution. This catches codes that fail prefix matching (e.g. digit-only MT codes like `1043` where TERCET keys are alphabetic).

- NUTS1/NUTS2 confidence: **1.0** (unanimous across all postal codes).
- NUTS3 confidence: agreement ratio capped at **0.80**.
- Currently applies to MT (MT0/MT00/MT001 at ~77% of postal codes).

### Tier 5: Single-NUTS3 country (`match_type: "estimated"`)

For countries that have only a single NUTS3 region (e.g. LI, CY, LU), any postal code in that country is mapped to the sole NUTS3 code.

- Confidence: **1.0** at all NUTS levels.
- The set is auto-detected from the loaded TERCET data and additionally seeded from the `single_nuts3_fallback` map in `app/settings.json`. The latter covers countries Eurostat treats as a single nationwide unit but for which GISCO publishes no TERCET file (currently Montenegro → `ME000`).

### No match (404)

If all five tiers fail, the service returns a 404 with a format hint for the expected postal code pattern.

## How it works

On first startup the service downloads TERCET flat files (one ZIP per country), parses CSV/TSV contents, and builds an in-memory dict for O(1) lookups. Parsed data is then persisted to a SQLite cache so subsequent startups load in ~1 second instead of re-downloading and re-parsing.

The SQLite cache is scoped by the NUTS version derived from the base URL (e.g. `postalcode2nuts_NUTS-2024.db`), TTL-checked, and written atomically. Changing the base URL to a new NUTS version automatically creates a separate cache.

**Stale data fallback:** When the cache TTL expires and the service attempts a fresh download from TERCET, a failure (network error, server down) no longer results in empty data. Instead, the service falls back to the expired cache and continues serving lookups. The `/health` endpoint reports `data_stale: true` so monitoring systems can detect the condition. On the next restart the service will try to refresh again.

At startup the service also loads any pre-computed estimates from the DB, removes estimates that now have exact TERCET matches (revalidation), and builds a prefix index over all TERCET codes for runtime approximation.

## Estimates

### Why estimates are needed

The GISCO TERCET correspondence tables are the authoritative source for postal-code-to-NUTS mappings, but they do not cover every postal code in active use. Gaps arise from newly issued codes, country-specific sub-ranges (e.g. French CEDEX codes), codes used only by specific postal operators, or simple omissions. The estimates table fills these gaps with pre-computed NUTS assignments so that real-world postal codes return a result instead of a 404.

### How missing codes were identified

Missing codes were identified by testing the service against **134 million real-world postal codes** drawn from 8 publicly available European datasets (GeoNames, OpenAddresses, GLEIF, SIRENE, TED, OffeneRegister, FTS, and Erasmus+ ECHE). Any code that passed the country's regex validation but had no TERCET match was flagged as a candidate for estimation.

### How estimates are computed

Each missing code is assigned a NUTS region by analysing its **neighbouring codes** — TERCET entries that share the same leading digits (or letters):

1. **Prefix grouping** — The missing code's leading characters are matched against all known postal codes for that country. For example, if `1012` is missing in Austria, all TERCET codes starting with `101x`, `10xx`, and `1xxx` are collected as neighbours.

2. **NUTS region agreement** — The NUTS3 assignments of those neighbours are compared. If all neighbours with the longest common prefix map to the same NUTS3 region (e.g. all Austrian `10xx` codes → AT130 Wien), the missing code is assigned that region.

3. **Geographic cross-check** — Where prefix analysis is ambiguous (e.g. codes near a NUTS boundary), assignments are verified against postal geography references (national postal operator data, OpenStreetMap boundaries, or Eurostat regional maps).

### Confidence labels

Each estimate is assigned a confidence label based on how strongly the neighbouring codes agree:

| Label    | Meaning | Typical case |
|----------|---------|--------------|
| **high** | All neighbours with the longest common prefix agree on the same NUTS3 region | Code well inside a single NUTS3 area (e.g. Austrian `1012` → AT130, since all `10xx` codes are in Wien) |
| **medium** | Most neighbours agree, but there is some divergence at NUTS3 level | Code near a NUTS3 boundary where the prefix spans two adjacent regions |
| **low** | Neighbours disagree significantly; assignment is plausible but uncertain | Code in a border area or where the postal numbering scheme does not align well with NUTS geography |

These labels map to numerical confidence scores per NUTS level. Coarser levels receive higher scores because neighbouring codes are more likely to share the same NUTS1 region than the same NUTS3 region:

| Label  | NUTS1 | NUTS2 | NUTS3 |
|--------|-------|-------|-------|
| high   | 0.98  | 0.95  | 0.90  |
| medium | 0.90  | 0.80  | 0.70  |
| low    | 0.70  | 0.55  | 0.40  |

### Current coverage

The estimates file contains **7,143 entries** across 32 countries, with the following confidence distribution:

| Confidence | Count | Share |
|------------|-------|-------|
| high       | 5,257 | 73.6% |
| medium     | 1,439 | 20.1% |
| low        |   447 |  6.3% |

Countries with the most estimates: TR (1,778), LT (1,231), FR (526), DE (500), EL (387), CZ (361), RO (358).

### Revalidation

When new TERCET data is published and loaded, the service automatically removes any estimate whose postal code now has an exact TERCET match. This ensures estimates never shadow official data.

### Configuration

The service loads estimates automatically at startup from the CSV file configured via `PC2NUTS_ESTIMATES_CSV` (default: `./tercet_missing_codes.csv`). No manual import step is needed — just update the CSV and restart.

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

## Deployment notes

- **Data refresh:** The service loads data once at startup and serves it for the lifetime of the process. To refresh data, restart the service. The SQLite cache ensures fast restarts; a full re-download only happens when the cache expires (default: 30 days) or is missing.
- **HTTPS:** The service serves plain HTTP. Place it behind a TLS-terminating reverse proxy (nginx, cloud load balancer) in production.
- **Docker:** The container starts briefly as root, the entrypoint chowns `/app/data` to `appuser`, then drops privileges via `gosu` before launching uvicorn. This means a freshly-mounted persistent volume (typically root-owned by the platform) "just works" — no operator-side `chown` required. Pre-computed estimates are included in the image. If you prefer to launch the container as a non-root user (`docker run --user appuser …`), the entrypoint detects that and skips the chown — you're then responsible for ensuring `/app/data` is writable by that UID.
- **Reverse proxies:** The image runs uvicorn with `--proxy-headers --forwarded-allow-ips '*'`, so `X-Forwarded-Proto`, `X-Forwarded-For`, and `X-Forwarded-Host` are honoured for any TLS-terminating proxy in front of the service (CDN, K8s ingress, nginx, etc.). The `/` info route's link URLs and rate-limit per-IP keying both depend on this.
- **Rate limiting:** Limits are per-client IP (`X-Forwarded-For` aware). Behind a reverse proxy, ensure the proxy sets this header correctly.
- **Access logging:** Every request is logged with client IP, method, path, status code, and duration. Set `PC2NUTS_ACCESS_LOG_FILE` to write to a rotating file instead of stderr.

## Docker deployment

### Quick start (no clone needed)

```bash
docker build -t postalcode2nuts https://github.com/bk86a/PostalCode2NUTS.git
docker run -p 8000:8000 -v postalcode2nuts-data:/app/data postalcode2nuts
```

### Build from local clone

```bash
docker build -t postalcode2nuts .
docker run -p 8000:8000 postalcode2nuts
```

On first start the service downloads TERCET data for the 34 countries with GISCO coverage (~2-5 minutes depending on network); Montenegro (single-NUTS3 fallback) and Albania (resolved in-code via the postal-code block map) need no download. After that everything is cached in a SQLite database for instant restarts.

### Persistent data volume

Without a volume, the cache is lost when the container stops and TERCET data must be re-downloaded on every restart. Add a named volume to keep the cache:

```bash
docker run -p 8000:8000 -v postalcode2nuts-data:/app/data postalcode2nuts
```

### Production example

```bash
docker run -d --name postalcode2nuts \
  -p 8000:8000 \
  -v postalcode2nuts-data:/app/data \
  -e PC2NUTS_DOCS_ENABLED=false \
  -e PC2NUTS_CORS_ORIGINS=https://mysite.com \
  -e PC2NUTS_RATE_LIMIT=100/minute \
  -e PC2NUTS_ACCESS_LOG_FILE=/app/data/access.log \
  postalcode2nuts
```

This disables Swagger UI, restricts CORS to a single origin, increases the rate limit, and writes access logs to a rotating file inside the persistent volume.

### Docker Compose

The repository ships [`compose.yaml`](./compose.yaml) — a production-shape stack with the api, a Redis sidecar for shared rate-limit storage, a persistent named volume for `/app/data`, and `PC2NUTS_WORKERS=2`. Run anywhere Docker Compose is supported:

```bash
docker compose up           # build (or pull) and run
docker compose up -d        # detached
docker compose down         # stop and remove containers (volume preserved)
docker compose down -v      # also wipe the persistent volume
```

Or the equivalent Makefile shortcuts: `make compose-up`, `make compose-down`, `make compose-logs`.

The compose file is the canonical reference for the production deployment shape and is intentionally provider-agnostic. Drop in to any orchestrator with multi-container pod / task-definition semantics by translating the same three pieces:

| Piece | What it does | Translates to |
|---|---|---|
| `api` service with `PC2NUTS_WORKERS` and `PC2NUTS_RATE_LIMIT_STORAGE_URI` | The service itself, configured for multi-worker behind a shared rate-limit backend | Any container runtime that runs a single image with env vars and an exposed port |
| `redis` service in the same compose project | Co-located rate-limit counter store, reachable as `redis://redis:6379/0` | A sibling container in the same Kubernetes pod, ECS task, etc. — or an external managed Redis (just point the URI at it) |
| Named `data` volume on `/app/data` | Persists the SQLite cache and downloaded TERCET zips across restarts so cold-starts don't re-fetch from Eurostat | Any persistent-storage primitive: K8s PVC, EFS, hostPath, cloud disk, host bind mount |

For the simplest setup (single worker, no Redis, no persistence) drop both the `redis` service and the multi-worker env vars from the compose file — the startup validator at `app/config.py:42-50` rejects partial configurations to prevent the per-IP rate limit from silently loosening.

### What's in the image

| Item | Details |
|------|---------|
| Base image | `python:3.14-slim` |
| User at runtime | `appuser` (non-root, switched by the entrypoint via `gosu`) |
| Dependencies | Pinned via `requirements.lock` |
| Estimates CSV | Included (`tercet_missing_codes.csv`) |
| Entrypoint | `/usr/local/bin/docker-entrypoint.sh` (chowns `/app/data`, drops privileges) |
| uvicorn flags | `--proxy-headers --forwarded-allow-ips '*' --no-access-log` (proxy headers for any TLS-terminating proxy; access log disabled so `/resolve` street/city query params never reach stdout — use `PC2NUTS_ACCESS_LOG_FILE` for sanitized access logging) |
| Health check | Built-in (`/health`, 30s interval, 120s start period) |
| Port | 8000 |
| Volume | `/app/data` (SQLite cache + downloaded ZIPs) |

## Project structure

```
app/
├── main.py              # FastAPI app, endpoints (/lookup, /pattern, /resolve, /health, /)
├── config.py            # Settings (env vars, country list, NUTS version derived from URL)
├── settings.json        # Countries, confidence map, approximate thresholds
├── data_loader.py       # TERCET download, parsing, SQLite cache, five-tier lookup
├── models.py            # Pydantic response models
├── postal_patterns.py   # Pattern loading, preprocessing + extract_postal_code()
├── postal_patterns.json # Per-country regex patterns, examples, expected_digits
├── resolver.py          # /resolve cascade: postal lookup, then geocode fallback (Full tier)
├── photon_client.py     # Photon geocoder HTTP client (Full tier)
├── nuts_polygons.py     # Download/cache GISCO NUTS polygons (Full tier)
├── nuts_pip.py          # Point-in-polygon NUTS-3 resolution (Full tier)
├── auth.py              # Trusted-token rate-limit bypass middleware
├── token_db.py          # DB-backed trusted-token registry client (libsql/Hrana)
├── estimates_refresh.py # Periodic remote refresh of the estimates table
└── limiter.py           # slowapi rate-limit setup
tests/
├── conftest.py          # Shared fixtures with mock TERCET data
├── test_postal_patterns.py
├── test_data_loader.py
├── test_api.py
├── test_resolve_endpoint.py
├── test_resolver.py
├── test_photon_client.py
├── test_nuts_pip.py
├── test_auth.py
├── test_token_db.py
└── ...                  # full suite also covers estimates refresh, rate limiting, the Albania block resolver, etc.
scripts/
├── import_estimates.py  # CLI: import pre-computed estimates into SQLite DB
└── tokens.py            # CLI: manage trusted-token DB (init/add/list/revoke)
tercet_missing_codes.csv # Pre-computed NUTS estimates for codes missing from TERCET
Makefile                 # Standard targets: lint, format, test, run, docker-build
```

Postal patterns and confidence settings are stored in JSON files (`postal_patterns.json`, `settings.json`) for easy editing without touching Python code. The `countries` list can still be overridden via the `PC2NUTS_COUNTRIES` environment variable.

## Development

```bash
pip install -r requirements-dev.txt   # runtime + dev/test dependencies
make test                             # run pytest suite
make lint                             # ruff check
make format                           # ruff format (auto-fix)
```

Pre-commit hooks are available via [pre-commit](https://pre-commit.com/):

```bash
pip install pre-commit
pre-commit install
```

## Adding a new country

If GISCO publishes TERCET data for a new country, the service discovers and loads it automatically on restart — no code changes needed. Lookups work immediately via the fallback normalizer.

For full support (prefix stripping, format hints, URL guessing fallback), edit three JSON files:

### 1. `app/settings.json` — add country code

```json
"countries": ["AT", "BE", ..., "XX"]
```

This enables URL guessing (Strategy 2) if the TERCET directory listing is unavailable.

### 2. `app/postal_patterns.json` — add regex pattern

```json
"XX": {
    "regex": "^(?:XX-?)?(\\d{5})$",
    "example": "12345, XX-12345"
}
```

The regex should handle optional country prefixes and capture the postal code digits. See existing patterns for reference. Patterns may have 0, 1, or 2 capture groups.

Optional `expected_digits` field for countries with fixed-length all-numeric postal codes (enables leading-zero restoration during preprocessing):

```json
"XX": {
    "regex": "^(?:XX-?)?(\\d{5})$",
    "example": "12345, XX-12345",
    "expected_digits": 5
}
```

Optional `tercet_map` field for countries where the TERCET key differs from the extracted code:

```json
"XX": {
    "regex": "^(?:XX-?)?(\\d{4})$",
    "example": "1234, XX-1234",
    "tercet_map": "prepend:XX"
}
```

Supported `tercet_map` actions: `truncate:N`, `prepend:XX`, `keep_alpha`, `outward_only` (marks a country for outward-code fallback, as used by UK).

### 3. `README.md` — update coverage section

Add the country to the appropriate group (EU, EFTA, or candidate) and add a row to the supported patterns table.

### Optional

- `tercet_missing_codes.csv` — add estimates for postal codes missing from TERCET
- Delete the SQLite cache (`data/*.db`) to force a full rebuild on next restart

No Python code changes are required.

> **Non-GISCO sources** (currently only the UK via NSPL) are different: they require a dedicated loader path and configuration (a source ZIP URL), not just a JSON edit — and must **not** be added to `settings.json` `countries`, or the GISCO loader would waste requests guessing non-existent TERCET URLs. See `_load_nspl` and `_load_uk_itl_bridge` in `app/data_loader.py` for the NSPL precedent.

## Data sources & attribution

**Postal code → NUTS (both tiers).** [GISCO TERCET flat files](https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units/postal-codes) ([download](https://gisco-services.ec.europa.eu/tercet/flat-files)), &copy; European Union &ndash; GISCO, licensed [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Albanian NUTS3 assignments come from the country's official postal-code block-allocation scheme (Posta Shqiptare), cross-validated against [GeoNames](https://www.geonames.org/) admin1 tagging ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)).

**UK postal code → ITL (optional).** [ONS National Statistics Postcode Lookup (NSPL)](https://geoportal.statistics.gov.uk/) and the ONS LAD→ITL lookup (bundled as `app/uk_lad_itl.csv`), &copy; Crown copyright and database right, licensed under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). Contains OS data &copy; Crown copyright and database right. NSPL is loaded only when `PC2NUTS_NSPL_URL` is configured; a postcode is resolved to its ITL3 (`TL…`) code via its Local Authority District (NSPL `lad25cd`) through the ONS LAD→ITL map.

The [EU Open Data Portal dataset](https://data.europa.eu/data/datasets/postcodes-and-nuts-nomenclature-of-territorial-units-for-statistics) was also considered as a data source. However, its refresh cycle lags behind the GISCO TERCET flat files, so direct sourcing from GISCO was chosen for more up-to-date coverage.

**Address → geocode → NUTS (Full tier only).** The optional geocoding tier relies on:

- **[Photon](https://github.com/komoot/photon)** by komoot — the self-hosted geocoder, licensed [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0).
- **OpenStreetMap** — Photon's search index is built from OpenStreetMap data, &copy; [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the [Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/). **If you deploy the Full tier, ODbL requires you to credit "© OpenStreetMap contributors" wherever you present geocoded results.**
- **[GISCO NUTS boundaries](https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units/territorial-units-statistics)** — the reference polygons used for point-in-polygon resolution; administrative boundaries &copy; EuroGeographics, redistributed by European Union &ndash; GISCO.

This attribution covers only the datasets the service consumes; the project's own code is licensed separately (see [Licence](#licence)).

## Contributing

Contributions are welcome! Please read the [contributing guidelines](CONTRIBUTING.md) before getting started. For security vulnerabilities, see the [security policy](SECURITY.md).

## Licence

Licensed under the [European Union Public Licence (EUPL) v. 1.2](LICENCE).
