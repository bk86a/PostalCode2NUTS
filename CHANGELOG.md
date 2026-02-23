# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

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
