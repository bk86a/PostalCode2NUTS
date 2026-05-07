# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
