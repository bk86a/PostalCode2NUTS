# Periodic refresh of `tercet_missing_codes.csv` — design

**Issue:** [#44](https://github.com/bk86a/PostalCode2NUTS/issues/44)
**Date:** 2026-05-01
**Status:** Approved (for plan + implementation)

## Problem

`tercet_missing_codes.csv` is the source of pre-computed NUTS estimates for postal codes that GISCO TERCET doesn't cover. Today the running service only loads it once at startup (or when a full `load_data()` rebuild fires). When a new estimate row is merged into the CSV on `main`, deployed pods don't pick it up until they're redeployed (which redeploys the whole image and forces a cold-start). Operators currently have no in-band way to push a CSV change to production short of bumping the image.

## Goals

- A running pod can pick up CSV changes from upstream `main` without a redeploy.
- A new pod that comes up immediately reflects the latest upstream CSV, not whatever was baked into the image at build time.
- An operator can confirm "the row I just merged is live" without waiting up to a full refresh interval.
- A bad upstream fetch (empty, half-truncated, parse error) cannot wipe out the in-memory estimates table.

## Non-goals

- Periodic refresh of GISCO TERCET (the 30-day SQLite cache TTL is the lever for that; out of scope here).
- Persisting refreshed estimates back to the SQLite cache or to disk. The remote CSV is the runtime source of truth when configured; the SQLite `estimates` table remains a snapshot from the most recent full `load_data()` and serves as a fallback.
- Cross-pod coordination of refresh timing. Each pod refreshes independently. With autoScaling.max=1 today this is moot.

## Configuration

Two new env vars, both opt-in. Defaults preserve current behaviour byte-for-byte.

| Env var | Default | Effect |
|---|---|---|
| `PC2NUTS_ESTIMATES_REFRESH_URL` | *(unset)* | When set, the worker periodically fetches this URL and replaces `_estimates` with the parsed contents. When unset, no remote fetch happens — current single-source-of-truth behaviour from the bundled `tercet_missing_codes.csv`. Recommended value: `https://raw.githubusercontent.com/bk86a/PostalCode2NUTS/main/tercet_missing_codes.csv`. |
| `PC2NUTS_ESTIMATES_REFRESH_INTERVAL_SECONDS` | `86400` (24 h) | Refresh cadence. Set to `0` to disable the periodic task while still running the bootstrap fetch on startup (rare; mostly useful for debugging). |

Validation: if `PC2NUTS_ESTIMATES_REFRESH_URL` is set, the value must parse as a URL. Anything else is left to the HTTP client to reject at fetch time.

## Approach (full replace, with sanity guard)

On each refresh tick (and once on worker startup, before lifespan-ready):

1. **Fetch.** `GET PC2NUTS_ESTIMATES_REFRESH_URL` with a 10 s timeout. Send `If-None-Match` and `If-Modified-Since` headers from the previous successful fetch when available. On 304 Not Modified, skip the parse + swap entirely — log nothing.
2. **Hash.** SHA-256 of the response body. Compare to the hash of the previous successful fetch (kept in module-scoped state). If unchanged, skip — log at DEBUG only.
3. **Parse.** Feed the body bytes through the existing CSV-loading logic (refactored to accept a stream, see Components below) into a temporary `dict` (not the live `_estimates`). Skipped rows (unknown confidence label, malformed) are counted, not fatal.
4. **Sanity guard.** If `len(new_dict) < 0.5 * len(_estimates)` *and* the live state is non-empty, refuse the swap. Log a WARNING with both counts, the new content's first parsing error if any, and the URL. Mark the most-recent refresh as failed. Re-probe next interval.
5. **Swap.** Acquire `_data_lock`, replace `_estimates` with `new_dict`, run `_revalidate_estimates()` (drops entries that already have an exact TERCET match), release lock. Log INFO with the new count and the diff vs the previous count.
6. **Update freshness flag.** Module-scoped `_estimates_refresh_stale = False` after a successful swap (or 304); `True` after any error path; `None` if the feature is disabled (URL unset).

The lock window in step 5 covers a swap of ~7 020 entries plus a revalidate pass over them. Sub-millisecond on local tests; bounded by the size of `_estimates`, which is small relative to `_lookup` (830 K).

## Components

A new module **`app/estimates_refresh.py`**. Self-contained because (a) it has its own state — last-fetch hash, last-fetch ETag/Last-Modified, the staleness flag — and (b) it shouldn't bloat `data_loader.py`, which is already large.

```
app/estimates_refresh.py
├── _last_etag, _last_modified, _last_hash, _stale       # module state
├── fetch_remote_csv(client) -> bytes | None             # GET with conditional headers
├── parse_csv_bytes(body) -> dict                        # parse into temp dict
├── refresh_estimates_once() -> RefreshResult            # one full tick
├── refresh_estimates_loop()                             # asyncio loop
└── get_refresh_stale() -> bool | None                   # for /health
```

Refactoring out of `data_loader.py`:

- The existing `_load_estimates_from_csv(path: Path)` reads from a file path. Extract a helper `_parse_estimates_rows(reader: csv.DictReader)` that operates on an already-opened reader; both the old loader and the new `parse_csv_bytes` reuse it. Avoids duplicate parsing logic.

Wiring:

- **`app/main.py` lifespan**: when `settings.estimates_refresh_url` is set, do one bootstrap call to `refresh_estimates_once()` after `load_data()` completes (so revalidation has the lookup table to compare against). If the bootstrap fetch fails, log a WARNING but continue startup — the bundled CSV's content is already loaded, so the worker is still serviceable.
- **`app/main.py` lifespan**: schedule `asyncio.create_task(refresh_estimates_loop())` on startup; cancel on shutdown.
- **`app/main.py` /health handler**: new `estimates_refresh_stale` field on `HealthResponse`, populated from `get_refresh_stale()`.
- **`app/main.py`**: new `POST /admin/refresh-estimates` route (see below).

## Manual refresh endpoint

`POST /admin/refresh-estimates` — synchronous, returns when the refresh completes.

- **Auth**: requires `Authorization: Bearer <token>` against the existing trusted-token registry (the same mechanism that bypasses the per-IP rate limit). No token, or unknown token, → 401.
- **Body**: empty.
- **Success response (200)**:
  ```json
  {
    "status": "refreshed",
    "previous_count": 7020,
    "new_count": 7042,
    "skipped_rows": 0,
    "source_url": "https://raw.githubusercontent.com/bk86a/PostalCode2NUTS/main/tercet_missing_codes.csv"
  }
  ```
  When upstream is unchanged (304/identical hash), `status: "unchanged"` and `previous_count == new_count`.
- **Failure response (502)** when the fetch or parse fails: `{"status": "failed", "reason": "..."}`. Live state is unchanged.
- **Sanity-guard rejection (409)** when the new CSV is < 50 % of current row count: `{"status": "rejected", "reason": "...", "previous_count": 7020, "candidate_count": 12}`. Live state is unchanged.
- **Disabled-feature response (503)** when `PC2NUTS_ESTIMATES_REFRESH_URL` is unset: `{"status": "disabled"}`.

The endpoint is **included in the OpenAPI schema** (no `include_in_schema=False`) so it's discoverable in `/docs`. It is rate-limited like the rest of the API (anonymous unauthenticated callers hit 401 long before they consume rate budget).

## Failure handling

| Failure | Behaviour |
|---|---|
| Network error / DNS / timeout | `_estimates_refresh_stale = True`, log WARNING (once per outage, debounced like slowapi's), retry next interval. Live state unchanged. |
| HTTP 4xx (404/403/etc.) | Same — stale flag set, log WARNING with status code. Specifically: 404 means the URL is wrong; we want this loud. |
| HTTP 5xx | Same — stale flag set, log WARNING with status. |
| Parse error / decoding error | Same — stale flag set, log WARNING with first traceback frame. |
| Sanity guard rejection | Stale flag set, log WARNING with both counts. |
| 304 Not Modified | Stale flag stays `False`. Log at DEBUG only — the ordinary case. |

The "log once per outage" debounce is achieved by tracking the previous success/failure state and only emitting a WARNING on the *transition* from success → failure (and an INFO on failure → success). Successive failures are silent at INFO/WARNING; DEBUG-level lines stay on every tick.

## /health changes

`HealthResponse` (in `app/models.py`) gains:

```python
estimates_refresh_stale: bool | None = None
```

Semantics:
- `None`: feature disabled (URL unset).
- `False`: most recent refresh attempt succeeded (or 304).
- `True`: most recent refresh attempt failed.

Existing `total_estimates` field is unchanged — it's still the in-memory count regardless of where those rows came from.

## Multi-worker considerations

Each worker runs its own `refresh_estimates_loop()`. With current production at `PC2NUTS_WORKERS=2`, this means:

- 2× the GitHub fetches per cadence (and per manual refresh, if a load balancer routes the operator's POST to one worker — the *other* worker is still on its old state until its next interval).
- Brief per-IP-worker divergence after an upstream change: one worker swaps in the new state seconds before the other. A client request might see the new estimate from one worker and the old from the other in the seconds-long window between worker swaps. Acceptable: estimates are by definition approximate, and the divergence converges within one tick.

This matches the existing TokenDB refresh pattern (`app/auth.py`) byte-for-byte. No new infra dependency. If we ever scale to many pods/many workers and 60/h GitHub raw rate limits become a concern, a Redis-backed leader (`SETNX` with TTL = interval) is a drop-in retrofit — but YAGNI today.

## Testing

Per the project's existing test patterns (`tests/test_*.py`, mock-at-the-boundary, no network in unit tests):

1. `tests/test_estimates_refresh.py` — new file:
   - `parse_csv_bytes` correctly parses well-formed CSV.
   - `parse_csv_bytes` skips unknown-confidence rows, returns the rest.
   - Sanity guard rejects a parsed dict whose count is < 50 % of current.
   - Sanity guard accepts a parsed dict whose count is ≥ 50 % of current.
   - Sanity guard accepts any count when current `_estimates` is empty (bootstrap path).
   - `refresh_estimates_once` with a mocked `httpx.AsyncClient`:
     - 200 OK with new content → swap, hash updated, stale=False.
     - 304 Not Modified → no swap, hash unchanged, stale=False.
     - 200 OK with identical content (matched by hash) → no swap, stale=False.
     - HTTP 5xx → no swap, stale=True.
     - Network error → no swap, stale=True.
     - Parse error → no swap, stale=True.
     - Sanity guard rejection → no swap, stale=True.
2. `tests/test_api.py` — add a `TestAdminRefreshEstimatesEndpoint` class:
   - 401 with no `Authorization` header.
   - 401 with an invalid bearer.
   - 200 with a valid bearer, mocked refresh returns new count.
   - 503 when the URL setting is unset.
   - 502 when the refresh raises.
   - 409 when the sanity guard rejects.
3. `tests/test_api.py` `TestHealthEndpoint`:
   - `estimates_refresh_stale` is `None` when URL unset.
   - `estimates_refresh_stale` is `False` after a successful (mocked) refresh.

## Operator runbook (`README.md` addition)

Brief subsection under existing "Configuration" / "Authentication & rate-limit bypass" sections:

```bash
# Force-refresh the estimates table on a deployed pod (after merging an update
# to tercet_missing_codes.csv on main):
curl -X POST -H "Authorization: Bearer $TOKEN" \
  https://api.example.invalid/admin/refresh-estimates
```

Plus the env-var table extended with the two new variables.

## Migration / backward compatibility

- Defaults (URL unset) preserve the current single-source behaviour. Existing deployments don't pick up the feature until the operator sets `PC2NUTS_ESTIMATES_REFRESH_URL`.
- The bundled `tercet_missing_codes.csv` continues to be loaded at startup as the bootstrap state. If the URL is set and the bootstrap fetch succeeds before the worker is ready, the bundled CSV's content is overwritten in-memory before any client request lands. If the bootstrap fetch fails, the worker comes up serving the bundled state and retries on the next tick.
- The `HealthResponse` schema gains a new optional field; existing JSON consumers that ignore unknown fields (the contract) are unaffected.
- No changes to the Dockerfile, no changes to the `compose.yaml` defaults — feature stays opt-in.

## Out of scope / future work

- Persisting refreshed estimates back to the SQLite `estimates` table on the volume. Not needed: warm-restart workers will refresh from upstream within their bootstrap fetch window.
- Cross-pod / cross-worker leader election. Not needed at current scale.
- Watching the GitHub repo for push events (webhooks) instead of polling. More moving parts; daily polling is more than fresh enough.
- Diff-based partial updates (apply only changed rows). The full-replace path is simpler and the dataset is small; YAGNI.
- A `/admin/refresh-tercet` or `/admin/reload-data` endpoint to force the full GISCO re-download. Out of scope for this issue.
