# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

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
