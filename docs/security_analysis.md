# Production Readiness Analysis — PostalCode2NUTS

*Generated 2026-02-17 against v0.5.0*

---

## 1. Input Validation

### ~~No `max_length` on `postal_code`~~ — RESOLVED (v0.6.0)

**Fixed:** `max_length=20` added to the `postal_code` Query parameter in `/lookup`.

### ReDoS — OK

All 34 regex patterns are anchored (`^...$`) with finite quantifiers. No catastrophic backtracking risk. Safe.

### SQL Injection — OK

All SQLite queries use parameterized `?` placeholders. No string interpolation in SQL. Safe.

### XSS — OK

API returns JSON only. User input in error messages is JSON-encoded by FastAPI. No HTML rendering.

---

## 2. Denial of Service (DoS)

### ~~No rate limiting~~ — RESOLVED (v0.6.0)

**Fixed:** Rate limiting added via `slowapi` middleware. Default: 60 requests/minute per client IP on `/lookup` and `/pattern`. Configurable via `PC2NUTS_RATE_LIMIT`. `/health` is exempt.

### ~~Startup blocks indefinitely~~ — RESOLVED (v0.6.0)

**Fixed:** Startup timeout added (default: 300 seconds, configurable via `PC2NUTS_STARTUP_TIMEOUT`). When the deadline is exceeded, loading stops and the service starts with whatever data was loaded. Falls back to stale DB if available.

### ~~ZIP bomb via extra sources~~ — RESOLVED (v0.6.0)

**Fixed:** `ZipInfo.file_size` is checked before extraction. Files exceeding 100 MB uncompressed are skipped with a warning.

### Prefix index memory — LOW

`_build_prefix_index()` stores every prefix of every postal code, with lists of matching NUTS3 codes. For ~830K codes of average 5 characters, this creates ~3.3M list entries. Estimated ~100-200MB RAM on top of the lookup table. Not dangerous, but significant for constrained environments.

---

## 3. Server-Side Request Forgery (SSRF)

### Env-var-controlled URLs — MEDIUM (accepted risk)

Both `PC2NUTS_TERCET_BASE_URL` and `PC2NUTS_EXTRA_SOURCES` cause the server to make HTTP requests to arbitrary URLs. Current validation for extra sources only checks scheme (`http://`/`https://`) and `.zip` extension.

An attacker with env var access (CI/CD, shared hosting, orchestration misconfiguration) could target internal services. However, environment variable access already implies server access, making this a low-priority concern. Documented as a known limitation.

---

## 4. Information Disclosure

### ~~Swagger UI exposed at `/docs`~~ — RESOLVED (v0.6.0)

**Fixed:** Docs are now configurable via `PC2NUTS_DOCS_ENABLED` (default: `true`). Set to `false` to disable `/docs` and `/redoc` in production.

### Version exposed in OpenAPI spec — LOW

`version=__version__` is visible in the API schema. Allows attackers to look up known vulnerabilities for that exact version.

### Error messages reveal system state — LOW

- 400 responses list all loaded country codes
- Health endpoint exposes `data_stale` (tells attacker the system is degraded)
- These are intentional but worth noting for threat model

---

## 5. File System Security

### Cache filename from URL — OK

`url.rsplit("/", 1)[-1]` extracts only the last path component. Path traversal via URL crafting is not possible. ZIP contents are read into memory, never extracted to disk. No zip-slip risk.

### Disk full handling — LOW

If the disk is full during `cached.write_bytes()` or SQLite operations, OSError propagates. For ZIPs, this fails silently per-country (other countries still load). For DB save, the `except Exception` handler cleans up the temp file. Adequate but not logged at ERROR level.

---

## 6. Concurrency & Thread Safety

### ~~Mutable globals~~ — RESOLVED (v0.6.0)

**Fixed:** `_data_lock = threading.Lock()` added. `load_data()` body is wrapped in the lock. Read paths remain lock-free (read-only after startup, CPython GIL makes dict reads atomic).

### Multi-worker deployment — LOW

With `uvicorn --workers N`, each worker loads data independently: N x memory, N x TERCET downloads on cold start. No shared cache between workers.

---

## 7. Docker & Deployment

### ~~Container runs as root~~ — RESOLVED (v0.6.0)

**Fixed:** Dockerfile now creates and runs as non-root `appuser`. Data directory is owned by `appuser`.

### ~~Estimates CSV missing from image~~ — RESOLVED (v0.6.0)

**Fixed:** Dockerfile now copies `tests/tercet_missing_codes.csv` into the image.

### No HTTPS — deployment concern

The app serves plain HTTP. Must be behind a TLS-terminating reverse proxy (nginx, cloud load balancer) in production.

### ~~No CORS~~ — RESOLVED (v0.6.0)

**Fixed:** CORS middleware added, configurable via `PC2NUTS_CORS_ORIGINS` (default: `*`). Allows `GET` methods only.

### ~~No Docker health check~~ — RESOLVED (v0.6.0)

**Fixed:** `HEALTHCHECK` added to Dockerfile (30s interval, 120s start period, uses Python `urllib` against `/health`).

---

## 8. Dependency & Supply Chain

### ~~Unpinned dependencies~~ — RESOLVED (v0.6.0)

**Fixed:** `requirements.lock` with exact pinned versions. Dockerfile uses `requirements.lock` for reproducible builds. `requirements.txt` retained with ranges for development.

### No CI/CD pipeline visible — LOW

No GitHub Actions, no automated tests, no linting, no security scanning in the repo.

---

## 9. Configuration Robustness

### `nuts_version` "unknown" not handled — LOW

If `PC2NUTS_TERCET_BASE_URL` doesn't match `NUTS-\d{4}`, the version is "unknown". URL guessing generates invalid URLs (`NUTS-unknown`), discovery may still work, but the DB file is `postalcode2nuts_NUTS-unknown.db`. No validation or warning at startup.

### No validation on `db_cache_ttl_days` — LOW

A value of 0 or negative causes the cache to always appear expired. No explicit bounds checking.

### JSON file corruption — LOW

If `settings.json` or `postal_patterns.json` are malformed, the app crashes at import time with a `json.JSONDecodeError`. This is fail-fast (good), but the error message won't be very user-friendly.

---

## 10. Data Integrity

### No data refresh without restart — MEDIUM (by design)

Once loaded, data is static. To refresh, restart the service. This is intentional to keep the service simple and stateless. The SQLite cache ensures fast restarts.

### TERCET completely down on first start — MEDIUM

If TERCET is unreachable on first startup and no SQLite cache exists, the app starts with zero data. The health endpoint reports `"no_data"` but the app is "up". All `/lookup` requests return 400. There's no automatic retry after startup.

### Estimates not revalidated after extra sources — LOW

`_revalidate_estimates()` removes estimates that overlap with exact matches. But if extra sources overwrite a TERCET entry with a different NUTS3 code, existing estimates for that postal code are not updated to reflect the new mapping.

---

## Summary by severity

| Severity | Issue | Section | Status |
|----------|-------|---------|--------|
| ~~**HIGH**~~ | ~~No rate limiting~~ | 2 | **RESOLVED v0.6.0** |
| ~~**HIGH**~~ | ~~No `max_length` on `postal_code` query parameter~~ | 1 | **RESOLVED v0.6.0** |
| ~~**HIGH**~~ | ~~Startup can block indefinitely if TERCET is slow~~ | 2 | **RESOLVED v0.6.0** |
| ~~**HIGH**~~ | ~~Docker container runs as root~~ | 7 | **RESOLVED v0.6.0** |
| ~~**MEDIUM**~~ | ~~ZIP bomb via extra sources~~ | 2 | **RESOLVED v0.6.0** |
| **MEDIUM** | SSRF via env-var-controlled URLs | 3 | Accepted risk |
| ~~**MEDIUM**~~ | ~~Swagger UI exposed in production~~ | 4 | **RESOLVED v0.6.0** |
| ~~**MEDIUM**~~ | ~~Estimates CSV missing from Docker image~~ | 7 | **RESOLVED v0.6.0** |
| ~~**MEDIUM**~~ | ~~No CORS middleware~~ | 7 | **RESOLVED v0.6.0** |
| ~~**MEDIUM**~~ | ~~Unpinned dependency versions~~ | 8 | **RESOLVED v0.6.0** |
| **MEDIUM** | No data refresh without restart | 10 | By design |
| **MEDIUM** | Zero-data startup when TERCET is down (no retry) | 10 | Open |
| ~~**MEDIUM**~~ | ~~Mutable globals without locking~~ | 6 | **RESOLVED v0.6.0** |
| **LOW** | Version exposed in OpenAPI spec | 4 | Open |
| ~~**LOW**~~ | ~~No Docker HEALTHCHECK~~ | 7 | **RESOLVED v0.6.0** |
| **LOW** | `nuts_version` "unknown" not validated | 9 | Open |
| **LOW** | No CI/CD or security scanning | 8 | Open |
| **LOW** | No access/audit logging | 4 | Open |
| **LOW** | Multi-worker redundant downloads | 6 | Open |
