# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

## [3.0.0] - 2026-08-28

Absence of data is no longer an HTTP error. A well-formed query against a served
route always answers `200`, carrying a `found` flag and a human-readable
`message`. `404` now means only that the requested URL is not a route of this API.

### Changed

- **BREAKING** `/lookup` no longer returns `404` for an unmapped postal code, a
  code outside a named territory, or a malformed territory code, and no longer
  returns `400` for a country this instance does not serve. All four answer `200`
  with `found: false`, a `message` explaining why, and every data field `null`.
- **BREAKING** `/pattern` no longer returns `404` for a country with no pattern or
  for a territory with no postal system. Both answer `200` with `found: false`,
  `regex: null`, `example: null` and a `message`.
- **BREAKING** `/resolve` no longer returns `400` for an unserved country; it
  answers `200` with `found: false`, `resolved_via: "none"` and
  `geocode.status: "not_attempted"`.
- **BREAKING** `NUTSResult`, `PatternResponse` and `ResolveResponse` gain `found`
  (bool) and `message` (string or null). `PatternResponse.regex` and
  `PatternResponse.example` are now nullable.
- **BREAKING** the `territory` block on `/lookup` and `/resolve` is renamed to
  `context`, and the `TerritoryInfo` model to `ContextInfo`. The block covers three
  kinds of place - outermost regions (integral parts of a Member State), overseas
  countries and territories (several of them constituent countries) and other
  European areas such as the Crown Dependencies - so naming it for one of them
  described the other two wrongly. Field names inside the block are unchanged,
  `status` included; only the key and the model name move.
- `422` (unparseable parameters), `429` (rate limit) and `401` (invalid token) are
  unchanged - they describe the request, not the data.
- OpenAPI `responses` for the three endpoints updated to document the 200-only
  contract.
- `scripts/enrich_via_api.py` branches on `found` and still accepts the old
  `404`/`400` shapes, so it works against a 2.x server.

- `GET /pattern` now returns a regex narrowed to the territory's own postal range
  for the six ISO-coded territories that are both linked to NUTS and postal-coded:
  `GP`, `MQ`, `GF`, `RE`, `YT` and `SJ`. Previously it echoed the administering
  country's pattern verbatim, so `/pattern?country=RE` validated Paris's `75001`
  as a Réunion code while `/lookup?country=RE&postal_code=75001` correctly refused
  it. Only the digits are restricted - the parent's accepted country prefixes
  (`F-`, `FR-`, `NO-`) still validate - and the `example` field is rebuilt around a
  code from the territory's own range.
- No `/lookup` behaviour change: it already gated on the registry's own ranges.
  This closes a reporting gap, not a validation gap.
- Territories that fail either condition are unchanged: the OCTs and Saint-Martin
  have postal codes but no NUTS link, and the Crown Dependencies have no prefix
  range to narrow to.

### Security

- **Rate limiting could be bypassed by any caller.** The image ran uvicorn with
  `--forwarded-allow-ips '*'`, so uvicorn trusted `X-Forwarded-For` from any peer
  and took its leftmost entry — the value the client sent. Rotating the header
  gave a fresh per-IP bucket on every request (and forged the client IP in the
  access log), and this held even behind a reverse proxy that appends the header
  correctly. The flag is now `--forwarded-allow-ips "${PC2NUTS_FORWARDED_ALLOW_IPS:-127.0.0.1}"`.
  **Operators must set `PC2NUTS_FORWARDED_ALLOW_IPS` to the address or CIDR of
  their reverse proxy** — otherwise the proxy is untrusted and all traffic is
  rate-limited under the proxy's own IP as a single bucket.
- Trusted tokens shorter than 32 characters are refused at startup. An invalid
  token is rejected before the rate limiter runs, so a short one could be guessed
  unmetered. The operator CLI already enforced this floor; `PC2NUTS_TRUSTED_TOKENS`
  did not. Tokens from the DB registry are unaffected.
- Settings validation failures now exit with their own message instead of raising
  through pydantic, whose `ValidationError` rendering includes the settings input
  dict — printing `PC2NUTS_TRUSTED_TOKENS` and `PC2NUTS_TOKEN_DB_AUTH_TOKEN`
  verbatim into container logs on an unrelated misconfiguration.
- The TERCET directory listing (`_discover_zip_urls`) and the Photon geocoder
  response are capped too. The listing is fetched first on a cold cache, before
  any of the capped ZIP downloads; the geocoder body is buffered on every
  `/resolve` request.
- Every remote body the worker buffers is now capped: `PC2NUTS_MAX_DOWNLOAD_MB`
  (default 512) for TERCET/NSPL zips, NUTS polygons and the names CSV,
  `PC2NUTS_MAX_ESTIMATES_DOWNLOAD_MB` (default 64) for the estimates refresh URL.
  A hijacked or misbehaving upstream previously had no ceiling. Zip *members*
  were already capped; the outer download was not.
- `/admin/refresh-estimates` is out of the public OpenAPI schema, as
  `/admin/memory` already was. Both remain gated on a trusted token.
- CORS pins `allow_credentials=False` explicitly, and the README now flags that
  the wildcard default suits the public API but should be narrowed for internal
  deployments.
- The CI `security` job runs bandit over `scripts/` as well as `app/`.

### Added

- `PC2NUTS_FORWARDED_ALLOW_IPS`, `PC2NUTS_MAX_DOWNLOAD_MB` and
  `PC2NUTS_MAX_ESTIMATES_DOWNLOAD_MB` settings (see the configuration table).

### Migration

Replace `if r.status_code == 404` (and `== 400`) with `if not r.json()["found"]`,
and read `body["context"]` where you read `body["territory"]`.
A partial hit - a recognised code in a territory outside NUTS, e.g. `FO/100` - keeps
`found: true` and carries a `message` explaining the null `nuts*` fields, so a client
that only checks `found` behaves as before for those.

### Changed (dependencies)

- `requirements.lock` regenerated after the grouped Dependabot production bump
  (#158): python-dotenv 1.2.3, uvicorn 0.52.4, and the transitive httpx2 2.12.0
  / httpcore2 2.12.0 / idna 3.19. Dependabot only edits `requirements.txt`, so
  the lockfile - which the CI `security` gate audits - is regenerated
  separately.

## [2.0.0] - 2026-08-26

Territory awareness. The service now states whether a postal code lies in an EU
outermost region or an overseas country or territory, and no longer fabricates a
NUTS code for territories Eurostat does not classify.

### Added

- Territory registry (`app/territories.py`, `app/territories.json`): 26 entries
  covering the 9 outermost regions, the 13 Annex II OCTs (11 ISO codes) and 6 other
  European territories.
- `territory` block on `/lookup` and `/resolve` responses, carrying `id`, `iso`,
  `name`, `status`, `administering_country`, `legal_basis`, `note` and
  `nuts_coverage`. `null` for ordinary lookups.
- 22 new territory ISO codes accepted as the `country` parameter: `GP` `MQ` `GF`
  `RE` `YT` `MF` `GL` `PF` `NC` `WF` `PM` `BL` `TF` `AW` `CW` `SX` `BQ` `SJ` `GI`
  `JE` `GG` `IM`. They previously returned `400`. The registry holds 23 ISO codes
  in total — `FO` was already supported — bringing accepted country codes to 59
  (60 with UK/ITL configured via `PC2NUTS_NSPL_URL`).
- Svalbard and Jan Mayen coverage: `SJ/8099` → `NO0B1`, `SJ/9170`–`9178` → `NO0B2`.
- `territories` count on `/health`.

### Changed

- **Breaking.** `match_type`, `nuts1`/`nuts2`/`nuts3` and their `_name` and
  `_confidence` companions are nullable.
- **Breaking.** Postal codes in territories outside NUTS return `200` with
  `nuts_coverage: "none"` and null NUTS fields instead of an approximated region.
  Affects French `975xx`, `977xx`, `978xx`, `984xx`, `986xx`, `987xx`, `988xx` and
  Danish `39xx`. `FR/98800` (Nouméa) previously returned `FRL03` Alpes-Maritimes at
  0.40 via a prefix chain onto Monaco's `98000`; `DK/3900` (Nuuk) previously
  returned `DK013` Nordsjælland at 0.20.
- **Breaking.** `FR/97150` Saint-Martin no longer returns `FRY10` at 0.60. It is an
  outermost region with no NUTS code of its own.
- `FR/97133` Saint-Barthélemy keeps `FRY10` at 1.0 but is now flagged
  `nuts_coverage: "tercet_entry_only"` — the code comes from Eurostat's own FR file
  even though the territory is an OCT.
- A postal code submitted under a territory ISO code it does not belong to returns
  `404` naming the territory, rather than answering from the administering
  country's data. `GL/2100` and `SJ/0150` are the motivating cases.
- `/resolve` skips geocoding entirely when `nuts_coverage` is `none`; no NUTS
  polygon covers those territories.
- `/pattern` resolves territory codes to the administering country's pattern, and
  returns `404` for the four territories that use no postal codes.
- The CI `publish` job also runs on `v*` tags and pushes semver-tagged images.

### Removed

- **Breaking.** Tier 6, the synthetic single-region fallback, and with it the
  fabricated Faroe Islands codes `FO0`/`FO00`/`FO000`. Those codes were invented by
  this project, not published by Eurostat. The `synthetic_nuts_fallback` key in
  `app/settings.json` and the matching `config.settings` property are gone.
  Montenegro's `ME000` and Albania's block-resolved codes are unaffected — both are
  genuine Eurostat codes that merely lack a TERCET file.

## [1.1.2] - 2026-08-14

Maintenance release: dependency currency and an HTTP-client migration. No API
changes — no new or altered routes, response fields, or configuration.

### Changed

- **HTTP client migrated from `httpx` to `httpx2`** (#156). starlette 1.6.0
  deprecates using `httpx` with `starlette.testclient`, which now imports
  `httpx2` first and warns on the fallback. `httpx2` is the successor from the
  same author and is API-compatible for everything used here, so the migration
  is an import change only.
  - **Operational note for self-hosters:** `httpx2` replaces `certifi` with
    [`truststore`](https://pypi.org/project/truststore/), moving TLS
    verification to the **OS trust store** rather than a bundled CA bundle. The
    published image is unaffected (verified: real HTTPS fetch succeeds and an
    expired certificate is still rejected), but a custom base image must have
    system CA certificates installed or outbound HTTPS will fail at runtime.
- Dependencies brought current: fastapi 0.141.1, starlette 1.6.0, uvicorn
  0.52.3, pydantic-settings 2.15.0, shapely 2.1.2, numpy 2.5.2, plus ruff 0.16.2
  for development.
- Dependabot version updates are now **grouped** into a single weekly PR per
  ecosystem (#148), separating production from development bumps because only
  the former also require `requirements.lock` regeneration.

### Security

- **The outbound-request log guard survives the client swap.** `app/main.py`
  quiets the HTTP client's request logging because that logger emits full URLs
  at INFO, and `/resolve` passes street/city to the geocoder as query
  parameters. `httpx2` logs under the logger name `httpx2`, so the previous
  `getLogger("httpx")` call would have gone on silencing a logger nothing writes
  to, and those addresses would have begun appearing in logs — with nothing
  failing to signal it. The logger name is now derived from the imported module
  so a future client swap cannot reopen this, and `tests/test_http_logging.py`
  asserts both the level and the absence of the address from captured output.
  This was caught and closed before release; no published version logged
  geocoder query parameters.

## [1.1.1] - 2026-07-04

### Fixed

- **UK/ITL resolution now works against the real NSPL dataset** (#7). 1.1.0
  assumed NSPL's `itl` column held Eurostat `TL…` codes (e.g. `TLI32`); the real
  column (`itl25cd`) holds ONS **GSS entity codes** (e.g. `S30000026`), so UK
  loaded **0 rows**. UK is now resolved **postcode → LAD (NSPL `lad25cd`) → ITL3**
  through the ONS LAD→ITL lookup, **bundled** as `app/uk_lad_itl.csv`, yielding
  clean `TL…` codes (`TLC31` → `TLC3` → `TLC`) plus region names. Verified to load
  ~1.79M live UK postcodes against NSPL May 2026.
  - New override `PC2NUTS_UK_ITL_LOOKUP_URL` for a refreshed LAD→ITL export when
    ONS bumps the ITL vintage; **replaces** `PC2NUTS_ITL_NAMES_URLS` (removed —
    names now come from the bundled map). Crown Dependencies (JE/GG/IM) and
    Gibraltar (GI) remain out of scope (their NSPL rows have no ITL LAD and are
    skipped).

## [1.1.0] - 2026-07-03

### Added

- **United Kingdom (ITL) support** (#7): the service can now resolve UK postcodes
  to [ITL](https://www.ons.gov.uk/methodology/geography/ukgeographies/eurostat)
  (International Territorial Level) codes — the UK's post-Brexit successor to
  NUTS. Sourced from the ONS [National Statistics Postcode Lookup
  (NSPL)](https://geoportal.statistics.gov.uk/), loaded only when
  `PC2NUTS_NSPL_URL` is configured (the ~178 MB dataset is not bundled). UK is
  treated as a parallel data channel: it reuses the same in-memory lookup, SQLite
  cache, and waterfall as TERCET, and an NSPL failure never blocks TERCET serving.
  - New response field **`code_system`** (`"NUTS"` | `"ITL"`) on `/lookup`
    (additive, non-breaking) marks which scheme the `nuts1/2/3` fields carry.
    ITL diverges from NUTS-2016 UK at L2/L3, so consumers should branch on it.
  - **`country=GB` accepted** as an alias for `UK` (like `GR → EL`).
  - **Outward-code lookup**: outward-only input (e.g. `SW1A`) or an unlisted
    full postcode resolves to the majority-vote ITL3 for that outward code with
    `match_type="estimated"` and medium confidence.
  - New config: `PC2NUTS_NSPL_URL`, `PC2NUTS_ITL_NAMES_URLS`. `patterns_version`
    bumped to `1.3`. Crown Dependencies (JE/GG/IM) and Gibraltar (GI) are out of
    scope and return `400`.
- **Albania coverage completeness** (#118): AL postal codes now resolve via the
  official postal-code block-allocation scheme (`app/albania_blocks.py`) instead
  of the incomplete GeoNames estimates. A code maps to its NUTS3 region by its
  allocated district prefix — codes GeoNames omitted (e.g. Tirana 1055, and whole
  districts like Gramsh 33xx / Peqin 35xx / Tepelenë 63xx / Përmet 64xx) no
  longer 404, while codes whose prefix belongs to no district (non-existent) return
  not-found rather than a fabricated region. Validated to reproduce all 489
  previously-shipped codes identically. Because the map is code, not data, AL
  coverage is now immune to the `PC2NUTS_ESTIMATES_REFRESH_URL` full-replace clobber.

## [1.0.0] - 2026-07-03

### Added

- **`GET /resolve` — address → geocode → NUTS cascade (Full tier).** When the
  postal result is weak (`not_found`, or `nuts3_confidence` below
  `PC2NUTS_RESOLVE_CONFIDENCE_THRESHOLD`) and a street/city is supplied, the
  address is geocoded via a self-hosted komoot
  [Photon](https://github.com/komoot/photon) instance and the coordinate mapped
  to a NUTS-3 region by point-in-polygon over GISCO NUTS-2024 polygons. Includes
  a country-guarded nearest-polygon **snap** for coastline/border points
  (`PC2NUTS_PIP_SNAP_KM`) and **postal-code sanitization** that recovers a valid
  code from a messy `POSTAL_CODE` field before falling back to geocoding.
- **Lite / Full deployment tiers**, selected at deploy time by
  `PC2NUTS_PHOTON_URL` (unset → Lite, postal-only; set → Full). New
  `compose.full.yaml` override; `/health` reports `pip_ready` and
  `geocoder_configured`. New config: `PC2NUTS_PHOTON_URL`,
  `PC2NUTS_NUTS_GEOJSON_URL` / `_PATH`, `PC2NUTS_RESOLVE_CONFIDENCE_THRESHOLD`,
  `PC2NUTS_PIP_SNAP_KM`.

### Changed

- **NUTS polygons load only in the Full tier** (gated on `PC2NUTS_PHOTON_URL`),
  so Lite deployments no longer download or hold the ~160 MB polygon set.
- **uvicorn access log disabled by default** (`--no-access-log`) so `/resolve`
  street/city query parameters never reach stdout; use `PC2NUTS_ACCESS_LOG_FILE`
  for sanitized access logging.
- **`patterns_version` bumped to 1.2** (`app/postal_patterns.json` `_meta`):
  catch-up bump covering the Faroe Islands (#55) and Albania (#54) entries,
  both of which were added without updating `_meta`, which had been stuck at
  `1.1` / `2026-04-29` since Montenegro (#53). Additive-only — no existing
  pattern was altered. Exposed via `/health` `patterns_version`.

### Fixed

- **Cross-border geocode guard on `/resolve`**: a mis-geocode that lands inside
  a neighboring country's polygon no longer returns a wrong-country NUTS — it
  falls back to the postal best-effort result instead of being trusted or
  snapped into a same-country border region.

## [0.21.0] - 2026-07-02

### Added

- **Albania (AL) support** (#54): Albania has a full NUTS hierarchy (`AL0`;
  `AL01`/`AL02`/`AL03`; 12 NUTS3 counties `AL011`–`AL035`) but Eurostat
  publishes no postal-code↔NUTS correspondence (TERCET) file for it. Coverage
  is therefore provided through the Tier-2 estimates layer: each Albanian
  4-digit postal code is mapped to its NUTS3 county via GeoNames' admin1
  (qark) tagging, which corresponds 1:1 to the NUTS3 regions. Lookups return
  `match_type="estimated"` with `high` confidence. Data is generated by
  `scripts/build_albania_estimates.py` and bundled in
  `tercet_missing_codes.csv` (~489 postal codes).

### Fixed

- **Estimate-only countries are now resolvable through `/lookup`**:
  `get_loaded_countries()` previously excluded countries present solely in the
  estimates table (it only counted TERCET + single-NUTS3 + synthetic
  fallbacks). Albania is the first such country; without this fix `/lookup`
  returned `400 Country not supported` before reaching the Tier-2 resolver.

## [0.20.2] - 2026-07-02

### Changed

- **Dependency bumps** via Dependabot:
  - `fastapi` >=0.138.0 → >=0.139.0 (#115)
  - `ruff` >=0.15.19 → >=0.15.20 (#114, dev)
- **Lockfile regeneration** floated a single production pin: `fastapi`
  0.138.0 → 0.139.0 (no transitive pins shifted). Dependabot edits only
  `requirements.txt`, so the bump reaches production — which builds from
  `requirements.lock` — by regenerating the lockfile.

## [0.20.1] - 2026-06-29

### Fixed

- **Faroe Islands (FO) 2-digit inputs no longer resolve to `FO000`** (#55): the FO
  pattern declared `expected_digits: 3`, which opted FO into the generic
  leading-zero recovery — so `_preprocess` padded a bare 2-digit value (e.g. `10`)
  to `010`, and the Tier 6 format guard then accepted it, returning the synthetic
  result instead of the documented 404. Real FO codes are 100–970 and never carry
  a leading zero, so `expected_digits` is dropped from the FO pattern; non-3-digit
  input now correctly returns 404.

## [0.20.0] - 2026-06-29

### Added

- **Faroe Islands (FO) support** (#55): the Faroe Islands has no NUTS coverage,
  so lookups now resolve via a new synthetic single-region fallback (Tier 6).
  Any well-formed 3-digit FO code returns `FO0` / `FO00` / `FO000` with
  `match_type="approximate"` and capped confidence (`0.90` / `0.85` / `0.80`).
  The code is fabricated (not a real NUTS code), configured via a new
  `synthetic_nuts_fallback` key in `app/settings.json`. Distinct from
  Montenegro's `single_nuts3_fallback` (Tier 5), whose `ME000` is genuine.

## [0.19.5] - 2026-06-25

### Security

- **`pydantic-settings` bumped to 2.14.2** to clear **GHSA-4xgf-cpjx-pc3j** (fixed in 2.14.2). The CI `security` gate audits `requirements.lock`, where `pydantic-settings` was still pinned at 2.14.1; Dependabot only edits `requirements.txt`, so the fix lands by regenerating the lockfile. This single stale lock pin was failing the `security` check on every open Dependabot PR (#105–#110), not just the `pydantic-settings` one.

### Changed

- **Dependency bumps** via Dependabot (bundled in #111, superseding #105, #106, #107, #108, #109, #110):
  - `fastapi` >=0.136.3 → >=0.138.0 (#107)
  - `slowapi` >=0.1.9 → >=0.1.10 (#108)
  - `pydantic-settings` >=2.14.1 → >=2.14.2 (#110)
  - `ruff` >=0.15.17 → >=0.15.19 (#106, dev)
  - `pytest` >=9.1.0 → >=9.1.1 (#109, dev)
  - `actions/checkout` v6 → v7 (#105, CI)
- **Lockfile regeneration** also floated transitive pins: `anyio` 4.14.0 → 4.14.1, `click` 8.4.1 → 8.4.2, `fastapi` 0.137.2 → 0.138.0, `wrapt` 2.2.1 → 2.2.2.

## [0.19.4] - 2026-06-19

### Security

- **`starlette` bumped to 1.3.1** to clear **CVE-2026-54282** (fixed in 1.3.0) and **CVE-2026-54283** (fixed in 1.3.1). `starlette` is pulled in transitively via `fastapi`; the CI `security` gate audits `requirements.lock`, so the fix is a `starlette==1.3.1` pin there, reached by regenerating the lockfile. Dependabot does not open PRs for undeclared transitive dependencies, so this was picked up as part of the lockfile regeneration.

### Changed

- **Dependency bumps** via Dependabot (bundled in #103, superseding #96, #98, #99, #101, #102):
  - `uvicorn` >=0.48.0 → >=0.49.0 (#96)
  - `idna` >=3.16 → >=3.18 (#98)
  - `pip-audit` >=2.10.0 → >=2.10.1 (#99, dev)
  - `pytest` >=9.0.3 → >=9.1.0 (#101, dev)
  - `ruff` >=0.15.14 → >=0.15.17 (#102, dev)
- **Lockfile regeneration** also floated transitive pins: `anyio` 4.14.0, `certifi` 2026.6.17, `fastapi` 0.137.2, `redis` 7.4.1, `slowapi` 0.1.10.

### Fixed

- **CI now republishes the container image on bundled-data changes** (#95): `tercet_missing_codes.csv` and `docker-entrypoint.sh` are `COPY`'d into the image but were missing from the `changes` path filter, so a data-only change (e.g. #93) merged without rebuilding `ghcr.io/.../:latest`. Both are now treated as code-relevant. Adds a `workflow_dispatch` trigger so manual rebuilds no longer need an empty commit.
- Removed a pre-existing unused import in `tests/test_estimates_refresh.py` surfaced by the `ruff` bump.

## [0.19.3] - 2026-05-28

### Security

- **`starlette` bumped to 1.1.0** to clear **PYSEC-2026-161** (fixed in 1.0.1). `starlette` is pulled in transitively via `fastapi`; the CI `security` gate audits `requirements.lock`, so the fix is a `starlette==1.1.0` pin there. `fastapi` 0.136.3 declares `starlette>=0.46.0` with no upper bound, so the 1.x bump is in-range. Dependabot does not open PRs for undeclared transitive dependencies, so this was pinned directly as part of the lockfile regeneration.

### Changed

- **Dependency bumps** via Dependabot (bundled, superseding #89, #90, #91, #92):
  - `fastapi` 0.136.1 → 0.136.3 (#89) — stricter underscore-header validation when `convert_underscores=True`
  - `uvicorn` >=0.47.0 → >=0.48.0 (#91) — `ssl_ciphers` defaults to OpenSSL, `ProxyHeadersMiddleware` ignores duplicate forwarding headers
  - `idna` >=3.15 → >=3.16 (#90) — floor raised to match the lockfile pin already in place from #87
  - `pytest-asyncio` 1.3.0 → 1.4.0 (#92, dev) — deprecates overriding the `event_loop_policy` fixture in favour of the new `pytest_asyncio_loop_factories` hook; current test suite does not override it

## [0.19.2] - 2026-05-22

### Security

- **`idna` bumped to 3.16** (#87) to clear **CVE-2026-45409** (fixed in 3.15). `idna` is pulled in transitively via `httpx`; the CI `security` gate audits `requirements.lock`, so the fix is a `idna==3.16` pin there plus an `idna>=3.15,<4` floor in `requirements.txt` to keep future lockfile regenerations clear. Dependabot does not open PRs for undeclared transitive dependencies, so this was pinned directly.

### Changed

- **Dependency bumps** via Dependabot:
  - `uvicorn` >=0.45.0 → >=0.47.0 (#86)
  - `pydantic-settings` 2.14.0 → 2.14.1 (#84)
  - `ruff` 0.15.12 → 0.15.13 (#85, dev)

## [0.19.1] - 2026-05-07

### Changed

- **Dependency bumps** via Dependabot:
  - `fastapi` 0.136.0 → 0.136.1 (#80)
  - `pydantic` 2.13.3 → 2.13.4 (#81)
  - `limits` >=2.3 → >=5.8.0 (#77) — used transitively via `slowapi`; no API surface in this repo touches `limits` directly.
  - `pytest-asyncio` 0.23 → 1.3.0 (#78, dev) — `asyncio_mode = "auto"` config remains supported.
  - `pytest` 8 → 9.0.3 (#79, dev) — required the `pytest-asyncio` 1.x bump first to avoid the `'Package' object has no attribute 'obj'` collection error in `pytest-asyncio` 0.23 under pytest 9.

## [0.19.0] - 2026-05-03

### Added

- **`/` root endpoint** returns service metadata and pointers to `/openapi.json`, `/docs`, `/redoc`, `/health`, and example `/lookup` and `/pattern` URLs. Replaces the previous `{"detail":"Not Found"}` response on the bare hostname. Marked `include_in_schema=False` so it doesn't clutter the OpenAPI document.
- **Persistent-volume support** via a new `docker-entrypoint.sh`: container starts as root, `chown appuser:appuser /app/data` (idempotent — no-op on warm starts), then `exec gosu appuser "$@"` to drop privileges before uvicorn starts. `Dockerfile` installs `gosu` and replaces `USER appuser` with `ENTRYPOINT`. Lets a freshly-provisioned platform persistent volume (initially root-owned) be mounted at `/app/data` without breaking the SQLite cache build. Cold-start cache survives pod recreates and redeploys; subsequent restarts skip the GISCO TERCET re-download until the configured TTL expires.
- **Provider-agnostic deployment**: new `compose.yaml` at the repo root demonstrates the canonical multi-worker production pattern (api + redis sidecar + persistent volume + multi-worker env vars) in a way that runs unmodified anywhere Docker Compose is supported and translates 1:1 to Kubernetes pods, ECS task definitions, or any orchestrator with multi-container semantics. New `compose-up`/`compose-down`/`compose-logs` Makefile targets. README "Docker deployment" section rewritten to point at it and to call out the swap-out points for switching providers.
- **Periodic refresh of `tercet_missing_codes.csv`** (#44): when `PC2NUTS_ESTIMATES_REFRESH_URL` is set, a per-worker asyncio task fetches the URL on every `PC2NUTS_ESTIMATES_REFRESH_INTERVAL_SECONDS` tick (default 24 h), parses the body, and full-replaces the in-memory estimates table if the content has changed and passes a 50 %-of-current sanity guard. Workers also do a synchronous bootstrap fetch before reporting ready, so a fresh pod immediately reflects upstream rather than waiting up to one interval. New `POST /admin/refresh-estimates` endpoint (trusted-token auth) lets operators force a refresh without waiting. New `/health` field `estimates_refresh_stale: bool | None`. Defaults preserve the current single-source-of-truth behaviour from the bundled `tercet_missing_codes.csv`.
- **`/admin/memory` diagnostic endpoint** (#75, #76): operator-only `GET` (trusted-token auth, `include_in_schema=False`) returning module-scoped dict sizes (`_lookup`, `_estimates`, `_prefix_index`, slowapi `_storage.*`, `auth._db_tokens`, ...), `/proc/self/status` counters (`VmRSS` / `VmHWM` / `RssAnon` / `RssFile` / `Threads`), file-descriptor count, asyncio task count + sample, and a top-30 `gc.get_objects()` type histogram. The histogram walk runs in `asyncio.to_thread` so the GIL releases during the pure-Python iteration and other coroutines on the worker can interleave; `gc.collect()` is intentionally omitted because on a multi-GB heap it costs seconds and holds the GIL throughout. Built for in-process leak investigations — diff two snapshots to localise the growing class.
- **85 new postal-code estimates** (#74) added to `tercet_missing_codes.csv` by the automated `postal_code_monitor.py`, covering codes that were either absent from TERCET (404) or only had approximate matches under the runtime estimator. Codes derived from neighbouring postal-code lookups via the API.

### Changed

- **uvicorn now runs with `--proxy-headers --forwarded-allow-ips '*'`** in the Dockerfile CMD, so `X-Forwarded-Proto`, `X-Forwarded-For`, and `X-Forwarded-Host` are honoured for any TLS-terminating proxy in front of the service (CDN, K8s ingress, nginx, Cloudflare). Concretely, the new `/` route's link URLs now return `https://` when behind a TLS proxy, and rate-limit per-IP keying correctly identifies the real client IP rather than the proxy's.
- **`docker-entrypoint.sh` is now safe to launch as a non-root user.** When started with `--user appuser` (or any non-root UID), the entrypoint skips the chown branch and just `exec`s the CMD as the current user — operators who pre-prepared `/app/data` ownership get the same behaviour as a fresh root start.

### Documentation

- **Performance re-baseline under multi-worker** (#68): `docs/performance.md` updated with the post-#68 numbers and a new rate-limit shared-storage verification subsection. Realistic-corpus knee at 35-40 RPS (vs ~30 single-worker), hot-key plateau at ~50 RPS, p99 at the old knee dropped from 4.5 s to 150 ms. Recommended operating point unchanged at 27 RPS — the win is headroom, not the operating point itself. The Redis sidecar shared-storage path is verified end-to-end: 130 anonymous requests against the published `120/minute` cap produced exactly 120 × `200` + 10 × `429`, ruling out per-worker counter divergence.

### Fixed

- **Concurrency: refreshes now serialised** (#44 follow-up): added a module-level `asyncio.Lock` around `refresh_estimates_once`. Without it, two overlapping calls (the periodic task and the admin endpoint) could resolve their fetches in non-monotonic order and overwrite newer state with older content. Codex flagged the race on the original PR (#72); fix is internal, no API change.
- **`scripts/perf_test.sh` `run_warm`**: indexing the vegeta target file by raw line number landed on a blank line half the time, crashing the script under `set -e`. Now extracts only the GET URLs into an array first.
- **`__version__` was stale at `0.14.0`** since the v0.14 release; openapi.json and FastAPI's `version` field have been reporting the wrong number for every release since then. Bumped to `0.18.0`. Future releases need to update `app/__init__.py` alongside the CHANGELOG until version derivation is automated.

## [0.18.0] - 2026-05-01

### Added

- **Multi-worker deployment** (#68): set `PC2NUTS_WORKERS` to launch N uvicorn worker processes. Multi-worker mode requires `PC2NUTS_RATE_LIMIT_STORAGE_URI` (e.g. a Redis URL) so the published per-IP rate limit stays accurate across workers; the service refuses to start otherwise. Transient backend unavailability is tolerated via slowapi's `in_memory_fallback_enabled` — falls back to per-process in-memory rate limiting and re-probes with exponential backoff, with one WARNING log per outage and one INFO log on recovery.

## [0.17.1] - 2026-04-29

### Fixed

- **TokenDB wire protocol** (#61): the v0.17.0 client assumed a generic `POST /query` body shape; the actual deployment target speaks libsql/Hrana v2 (`POST /v2/pipeline` with statements wrapped as `{requests: [{type: "execute", stmt: {sql, args}}]}` and rows returned as arrays of typed value objects). `TokenDB.execute` now speaks Hrana correctly, automatically rewrites `libsql://` URLs to `https://`, and accepts a Bearer auth token via the new `PC2NUTS_TOKEN_DB_AUTH_TOKEN` env var (and matching `--auth-token` CLI flag). Verified end-to-end against a real database instance.

## [0.17.0] - 2026-04-29

### Added

- **DB-backed trusted tokens** (#61): trusted-token storage moved from `PC2NUTS_TRUSTED_TOKENS` env var to a managed SQLite-compatible HTTP database. New env vars: `PC2NUTS_TOKEN_DB_URL` (connection string), `PC2NUTS_TOKEN_REFRESH_SECONDS` (default `60`). Tokens are issued via `python -m scripts.tokens add --label "..."` and take effect within ~60 s — no container restart required. The env var continues to work as a union with the DB and serves as a disaster-recovery fallback when the DB is unreachable. New `/health` field `token_db_stale` flags refresh failures.
- **`scripts/tokens.py` operator CLI** with subcommands `init`, `add`, `list`, `revoke`. `add --value <existing-token>` lets operators migrate v1 env-var tokens while preserving their audit `token_id`.

## [0.16.0] - 2026-04-29

### Added

- **Auth-token bypass** (#60): trusted callers can bypass the per-IP rate limit by presenting `Authorization: Bearer <token>`. Tokens are managed via the new `PC2NUTS_TRUSTED_TOKENS` comma-separated env var. Invalid tokens return `401`; malformed `Authorization` headers return `400`. Audit lines log a non-reversible 8-char SHA-256 prefix only — token values never appear in logs. See README "Authentication & rate-limit bypass" for the operator runbook.

## [0.15.0] - 2026-04-29

### Added

- **Montenegro (ME) support** (#53): postal-code lookups for Montenegro return `ME000` / `ME00` / `ME0` via the existing single-NUTS3 fallback (Tier 5). Eurostat treats Montenegro as a single nationwide unit at every NUTS level, and GISCO publishes no TERCET file for it; ME is therefore served entirely from the new `single_nuts3_fallback` map in `app/settings.json` (no external data download). Pattern: 5 digits starting with `8`, optional `ME-` / `ME ` prefix accepted.
- **`single_nuts3_fallback` settings field**: data-driven seed for the Tier 5 single-NUTS3 set, allowing countries with no GISCO TERCET coverage but a single nationwide NUTS3 unit to be added via configuration alone. Auto-detected single-NUTS3 entries derived from real data take precedence on conflict.

### Changed

- **`patterns_version` bumped to 1.1** (additive change — new ME entry, no existing pattern altered).
- **`get_loaded_countries()`** now includes countries served only via the single-NUTS3 fallback, so `/lookup` accepts them without a 400.

## [0.13.0] - 2026-02-23

### Added

- **Automated test suite** (#25): 69 pytest tests covering `postal_patterns.py` (preprocessing, tercet_map, extraction), `data_loader.py` (normalize functions, all 5 lookup tiers), and FastAPI endpoints (`/lookup`, `/pattern`, `/health`). CI now runs tests before publish.
- **Makefile** (#24): standard targets for `lint`, `format`, `test`, `run`, `docker-build`, `docker-run`.
- **Pre-commit hooks** (#24): ruff lint + format via `.pre-commit-config.yaml`.
- **`requirements-dev.txt`** (#22): dev/test dependencies (ruff, bandit, pip-audit, pytest).
- **`ruff format` CI check** (#24): enforces consistent code formatting in CI.

### Changed

- **Centralized duplicated logic** (#22): `normalize_country()` replaces duplicate GR→EL blocks, `_db_connection()` context manager replaces 6 manual SQLite connect/close patterns, `_build_result()` helper replaces repetitive result dict construction across all lookup tiers.
- **Narrowed exception handling** (#23): 9 bare `except Exception` blocks in `data_loader.py` replaced with specific types (`sqlite3.Error`, `httpx.RequestError`, `OSError`, etc.). Silent catch in `import_estimates.py` now logs a message.
- **Return type hints** added to `dispatch()` and `_rate_limit_handler()` in `main.py`.

## [0.12.0] - 2026-02-23

### Fixed

- **MT regex** (#14): separator between alpha prefix and digits is now optional (`MST1000` accepted alongside `MST 1000` and `MST-1000`). Previously, codes without a space failed regex extraction and fell to approximate matching with lower confidence.

### Added

- **Country-level majority-vote fallback**: new Tier 4 in the lookup chain for countries where all postal codes map to the same NUTS1/NUTS2 but NUTS3 has a dominant winner. Returns `match_type: "approximate"` with NUTS1/NUTS2 confidence 1.0 and NUTS3 confidence based on agreement ratio (capped at 0.80). Naturally captures MT (MT0/MT00/MT001 at ~77%). Digit-only MT codes like `1043` that previously returned 404 now get a valid approximate result.

## [0.11.0] - 2026-02-23

### Added

- **FR CEDEX estimates** (#8): ~511 French CEDEX postal codes (enterprise/university mail routing) added to `tercet_missing_codes.csv` with high-confidence département→NUTS3 mappings.
- **FR DOM-TOM estimates** (#9): 15 French overseas territory postal codes (Guadeloupe, Martinique, Guyane, La Réunion, Mayotte) added with high-confidence mappings. French Polynesia (987xx) and New Caledonia (988xx) excluded — these are OCTs with no valid NUTS mapping.
- **NL missing code estimates** (#13): 8 Dutch postal codes for major cities (Amsterdam, The Hague, Utrecht, Maastricht, Arnhem, Apeldoorn, Zwolle) added with high-confidence mappings. Willemstad (3059) excluded — belongs to Curaçao, not the Netherlands.

## [0.10.1] - 2026-02-23

### Fixed

- **Preprocessing order**: dot thousand-separator removal now runs before `.0` stripping, so locale-formatted codes like `13.000` correctly become `13000` instead of `13`.
- **IE regex** (#10): space between Eircode routing key and identifier is now optional (`D02X285` accepted alongside `D02 X285`).
- **PT regex** (#12): space is now accepted as a separator between digit groups (`1000 001` alongside `1000-001` and `1000001`).

### Notes

- **#11 (NO lowercase prefix)**: already handled — all regexes are compiled with `re.IGNORECASE` and input is uppercased before matching. Closed as resolved.

## [0.10.0] - 2026-02-23

### Added

- **Input preprocessing** for postal codes mangled by Excel, CSV exports, or database dumps. Three country-agnostic steps are applied before regex matching:
  1. **Strip trailing `.0`** — Excel float coercion (`28040.0` → `28040`)
  2. **Remove dot thousand-separators** — (`13.600` → `13600`)
  3. **Restore leading zeros** — using per-country `expected_digits` metadata (`8461` → `08461` for ES)
- `expected_digits` field in `postal_patterns.json` for 30 countries with fixed-length all-numeric postal codes. Countries with non-numeric formats (IE, MT, NL) are excluded.

### Notes

- **Backward compatible**: preprocessing is transparent — correctly formatted postal codes are passed through unchanged. No regex patterns were modified.
- **Closes #16** (generic preprocessing for Excel artifacts and postal code mangling). Also subsumes #15 (ES-specific fixes).

## [0.9.0] - 2026-02-20

### Added

- **NUTS region names** in `/lookup` responses: `nuts1_name`, `nuts2_name`, `nuts3_name` fields provide human-readable region names (Latin script) alongside NUTS codes. Names are sourced from the [GISCO NUTS CSV](https://gisco-services.ec.europa.eu/distribution/v2/nuts/csv/) distribution.
- `total_nuts_names` field in `/health` endpoint showing how many region names are loaded.
- NUTS names are cached in the SQLite DB (`nuts_names` table) for fast restarts.

### Notes

- **Backward compatible**: name fields default to `null` when names are unavailable. Existing clients that ignore unknown fields are unaffected.
- **Graceful degradation**: if the NUTS names CSV cannot be downloaded, all name fields are `null` but lookups continue to work normally. Pre-0.9.0 SQLite caches (without the `nuts_names` table) remain fully valid.

## [0.8.0] and earlier

Prior changes were not tracked in this changelog.
