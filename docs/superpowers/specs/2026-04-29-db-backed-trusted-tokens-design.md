# DB-backed trusted-tokens — design

**Status:** approved (brainstorming complete)
**Date:** 2026-04-29
**Issue:** [#61](https://github.com/bk86a/PostalCode2NUTS/issues/61)
**Predecessor:** [v0.16.0 auth-token bypass](2026-04-29-auth-token-bypass-design.md) — env-var-based v1 of this feature.
**Scope:** move trusted-token storage from `PC2NUTS_TRUSTED_TOKENS` (env var, v1) to a managed SQLite-compatible HTTP database (v2), with the env var preserved as a disaster-recovery fallback. Adds an operator CLI (`python -m scripts.tokens`) for issuance, listing, and revocation.

---

## 1. Goals and non-goals

### Goals

- Eliminate the v1 brittleness around env-var-driven token rotation (programmatic env-var changes through the hosting provider's container API have destructive side effects on adjacent configuration).
- Token rotation does not require a container restart.
- Operator workflow is a single CLI command per action: `python -m scripts.tokens add --label "..."`, `list`, `revoke <id>`.
- Auth on-request hot path stays in-memory and constant-time — `is_trusted` lookup remains a frozenset membership check; DB reads happen out-of-band on a periodic refresh.
- Soft migration from v1: env var continues to work as a union with DB-loaded tokens. No forced cutover.
- DB outage is non-fatal — the service keeps serving anonymous traffic; bypass simply degrades.

### Non-goals

- Per-token quotas, scopes, or expiry. (v3 candidates if usage demands.)
- An HTTP admin endpoint (`POST /admin/tokens`). Rejected in the v1 brainstorming as well.
- `last_used_at` tracking. Either a per-request write hotspot or async batching — defer.
- Automated migration / deprecation of `PC2NUTS_TRUSTED_TOKENS`. The env var stays a supported channel until a future issue removes it.
- Moving the primary lookup data (the ~830K-row TERCET cache) into the same database. Different concern — can be its own issue if ever needed.

---

## 2. Operational runbook (consult when issuing or revoking)

This section is the operator's reference. It is duplicated near-verbatim into `README.md` so the running system can be administered without reading source.

### Initial setup (one-time, per environment)

```bash
# On your laptop, with PC2NUTS_TOKEN_DB_URL set in the environment:
export PC2NUTS_TOKEN_DB_URL='<connection string from the database provider's dashboard>'
python -m scripts.tokens init
# → creates the trusted_tokens table and index. Idempotent.
```

### Issue a token

```bash
python -m scripts.tokens add --label "alice-batch-2026-04"
# Generated: e3a1f2...d4
# Inserted id=3, label='alice-batch-2026-04', token_id=9a29f07a

# Hand the printed token to the consumer over a confidential channel
# (1Password, Signal, encrypted email — not Slack, not GitHub issues).
```

The new token becomes active in the running service within `PC2NUTS_TOKEN_REFRESH_SECONDS` (default 60 s) — **no container restart needed**.

### Migrate an existing v1 env-var token (preserves the audit token_id)

```bash
python -m scripts.tokens add --label "perf-test-2026-04-29" --value "<the existing 48-hex token>"
# → inserts the existing value, preserving its sha256 prefix used in audit logs.
```

### List active and revoked tokens

```bash
python -m scripts.tokens list
# id  label                       created_at           status
# 1   perf-test-2026-04-29        2026-04-29T12:50:00  active
# 2   alice-batch-2026-04         2026-04-29T15:20:00  active
# 3   bob-research                2026-04-15T09:10:00  revoked@2026-04-28T14:00:00
```

The `list` output never includes the raw `value` column — only id, label, created_at, revoked status.

### Revoke a token

```bash
python -m scripts.tokens revoke 3
# Token id=3 revoked at 2026-04-29T16:00:00.
```

The revocation takes effect within `PC2NUTS_TOKEN_REFRESH_SECONDS`. Anyone using that token starts receiving 401.

### Find the token id of a logged request

Same as v1 — the access-log `token_id=<8hex>` field is `sha256(token)[:8]`:

```bash
echo -n "<token>" | sha256sum | cut -c1-8
```

The CLI's `add` command also prints the `token_id` at issuance time, so the audit prefix is in operator hands from the start.

### Disable the DB-backed feature entirely

Unset `PC2NUTS_TOKEN_DB_URL`. Behaviour reverts to v1 (env-var only).

### Disaster recovery — DB unreachable

If the database is unreachable, the service continues serving traffic; only the bypass feature degrades. Two scenarios:

- **You have tokens in `PC2NUTS_TRUSTED_TOKENS`** (treated as a DR fallback): bypass continues using the env-var tokens until the DB recovers.
- **You don't**: bypass is off until the DB recovers. Anonymous traffic is unaffected.

To cover yourself: keep one or two emergency tokens in `PC2NUTS_TRUSTED_TOKENS` even after migrating most issuance to the DB. Document them as DR-only.

---

## 3. Data model

### Schema

```sql
CREATE TABLE trusted_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    value       TEXT NOT NULL UNIQUE,
    label       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at  TEXT
);
CREATE INDEX idx_trusted_tokens_active
    ON trusted_tokens (value)
    WHERE revoked_at IS NULL;
```

| Column | Notes |
|---|---|
| `id` | Stable identifier; surfaced in `list` and `revoke` UX. |
| `value` | The opaque 48-hex bearer token. **Never logged**. |
| `label` | Operator-supplied free-text. Used by `list` and audit. May be NULL but the CLI requires it on `add`. |
| `created_at` / `revoked_at` | ISO-8601 strings via SQLite `datetime('now')`. `revoked_at IS NULL` ⇒ active. |

Active set query (used by the runtime refresh and by `list --active`):

```sql
SELECT value, id, label, created_at FROM trusted_tokens WHERE revoked_at IS NULL;
```

---

## 4. Configuration

| Env var | Default | Read by | Purpose |
|---|---|---|---|
| `PC2NUTS_TOKEN_DB_URL` | `""` (unset) | service + CLI | Connection string for the SQLite-compatible managed database. Empty → DB feature disabled, behaviour reverts to v1. |
| `PC2NUTS_TOKEN_REFRESH_SECONDS` | `60` | service | How often the background task reloads the active set into memory. |
| `PC2NUTS_TRUSTED_TOKENS` | `""` (unset) | service | Existing v1 env var. Continues to work as a union with DB tokens; serves as DR fallback when DB is unreachable. No deprecation in this release. |

The CLI reads `PC2NUTS_TOKEN_DB_URL` from the operator's environment, or accepts `--db-url <connection-string>` as an override.

---

## 5. Architecture

### 5.1 Provider-agnostic database client (`app/token_db.py` — new)

A small (~100 LOC) HTTP client for a SQLite-compatible managed database. Exposes:

```python
class TokenDB:
    def __init__(self, url: str): ...
    def execute(self, sql: str, params: list[Any] | None = None) -> list[dict]:
        """Send a SQL statement. Returns a list of row dicts (empty for writes)."""
    def init_schema(self) -> None:
        """Idempotent — run the CREATE TABLE / CREATE INDEX statements."""
    def list_active(self) -> list[dict]:
        """SELECT id, value, label, created_at FROM trusted_tokens WHERE revoked_at IS NULL."""
    def add(self, value: str, label: str) -> int:
        """INSERT INTO trusted_tokens(value, label) VALUES (?, ?). Returns new id."""
    def list_all(self) -> list[dict]:
        """SELECT id, label, created_at, revoked_at FROM trusted_tokens (no value)."""
    def revoke(self, token_id: int) -> bool:
        """UPDATE … SET revoked_at = datetime('now') WHERE id = ? AND revoked_at IS NULL."""
```

The module is named `token_db.py`, not after the provider — it speaks a generic SQLite-over-HTTP contract and could be repointed at a different backend with a connection-string change.

Connection-string parsing and authentication are concentrated in this module. `httpx` (already a project dependency for TERCET downloads) is used for the HTTP client.

### 5.2 Auth layer changes (`app/auth.py`)

Two changes, both small:

1. **`_db_tokens` cache** — a module-level `frozenset[str]` populated by a background refresh task. Initially empty.

2. **`_get_trusted_tokens()` returns the union**:

   ```python
   def _get_trusted_tokens() -> frozenset[str]:
       return _db_tokens | settings.trusted_tokens
   ```

   The existing test seam is preserved. Tests that monkey-patch this function (Tasks 4–7 of the v1 plan) keep working unchanged.

3. **Refresh task** — started from `lifespan` in `app/main.py` if `PC2NUTS_TOKEN_DB_URL` is set:

   ```python
   async def _refresh_tokens_loop():
       while True:
           try:
               rows = await asyncio.to_thread(token_db.list_active)
               _db_tokens = frozenset(r["value"] for r in rows)
               _token_db_stale = False
           except Exception as exc:
               logger.warning("token DB refresh failed: %s", exc)
               _token_db_stale = True
               # Keep previous _db_tokens unchanged.
           await asyncio.sleep(settings.token_refresh_seconds)
   ```

   The first iteration runs at startup. If it fails, `_db_tokens` stays empty (the default) and the env-var union covers any active tokens.

### 5.3 Health endpoint extension

`/health` gains one optional field:

```python
"token_db_stale": True | False  # only present when PC2NUTS_TOKEN_DB_URL is configured
```

`True` means the most recent refresh attempt failed (the in-memory set is stale relative to the DB but still in effect). Monitoring tools can alert on this.

### 5.4 CLI (`scripts/tokens.py` — new)

Standard `argparse` with subcommands. Imports `TokenDB` from `app/token_db.py`.

```
python -m scripts.tokens init            # idempotent schema bootstrap
python -m scripts.tokens add --label LABEL [--value VALUE]
python -m scripts.tokens list [--all]    # default: active only
python -m scripts.tokens revoke ID
```

Argument details:

- `add` generates a 48-hex token via `secrets.token_hex(24)` unless `--value` is given (used for v1→v2 migration to preserve `token_id` continuity). Prints the raw token, the new id, and the audit `token_id`. Exits non-zero on UNIQUE-violation.
- `list` defaults to active tokens; `--all` includes revoked. Output is a fixed-column table (id, label, created_at, status). The raw `value` column is **never** printed.
- `revoke` is idempotent. If the id is already revoked, prints "already revoked" and exits 0.
- All subcommands accept `--db-url URL` to override `PC2NUTS_TOKEN_DB_URL`.

### 5.5 Files affected

| File | Status | Responsibility |
|---|---|---|
| `app/token_db.py` | **new** (~100 LOC) | Provider-agnostic SQLite-over-HTTP client. Init/list/add/revoke methods. |
| `app/auth.py` | modify | `_db_tokens` cache, refresh task entry point, union in `_get_trusted_tokens`. |
| `app/main.py` | modify | Spawn `_refresh_tokens_loop` from `lifespan` when DB URL is set; expose `token_db_stale` on `/health`. |
| `app/config.py` | modify | New settings: `token_db_url`, `token_refresh_seconds`. |
| `scripts/tokens.py` | **new** (~150 LOC) | Operator CLI. |
| `tests/test_token_db.py` | **new** | Unit tests for the HTTP client (mock httpx). |
| `tests/test_tokens_cli.py` | **new** | Unit tests for the CLI subcommands. |
| `tests/test_auth.py` | modify | Tests for the union behaviour, refresh task, DB-down at startup. |
| `tests/test_api.py` | modify | `/health` integration test for `token_db_stale` field. |
| `README.md` | modify | Replace v1 runbook with the v2 runbook from §2; keep DR fallback note. |
| `CHANGELOG.md` | modify | New `### Added` entry under `[Unreleased]`. |
| `app/settings.json` | unchanged | (Connection strings live in env vars, not committed JSON.) |

---

## 6. Security considerations

- **Token storage at rest** is the database provider's responsibility. The connection string is a credential and lives only in `PC2NUTS_TOKEN_DB_URL` (operator's env / production env config). It is never committed.
- **Token values in `list` output** are deliberately omitted. Once issued, the operator is expected to store the raw token in their own secret manager. The DB is the registry, not the recovery channel.
- **CLI logging** never emits the raw token to stdout/stderr beyond the single `add` line that hands it to the operator. No DEBUG mode prints values.
- **Constant-time comparison** stays unchanged — `hmac.compare_digest` against the union frozenset, same as v1.
- **DB compromise** — if the DB is breached, all live tokens are exposed. Mitigation: rotate by `revoke` + reissue. Same risk model as the env var, but with audit trail.
- **Revocation latency** is `PC2NUTS_TOKEN_REFRESH_SECONDS` worst case (default 60 s). For an emergency, the operator can additionally restart the container to force an immediate refresh.

---

## 7. Tests

### Unit (`tests/test_token_db.py`)

1. `init_schema` issues the expected `CREATE TABLE` and `CREATE INDEX`; idempotent on re-run.
2. `add(value, label)` issues parameterised INSERT; returns the new id.
3. `add` with duplicate `value` raises a UNIQUE-violation error.
4. `list_active` filters out revoked rows.
5. `list_all` returns both active and revoked, never the `value` column.
6. `revoke(id)` sets `revoked_at`, idempotent on already-revoked ids.
7. HTTP error (5xx, network) raises a typed exception (`TokenDBError`) — caller can catch.

### Unit (`tests/test_tokens_cli.py`)

8. `add` with `--label` only: generates a 48-hex token, prints id + token + token_id.
9. `add` with `--value` accepts the supplied value, preserves the audit prefix.
10. `add` exits non-zero on duplicate value.
11. `list` (default) shows only active rows, no `value` column.
12. `list --all` includes revoked rows.
13. `revoke <id>` prints success.
14. `revoke <already-revoked-id>` prints "already revoked", exits 0.
15. Missing `PC2NUTS_TOKEN_DB_URL` and no `--db-url` → exits with a helpful error.

### Integration (extends `tests/test_auth.py`)

16. `_get_trusted_tokens` returns DB tokens ∪ env-var tokens.
17. Refresh task pulls the active set on tick.
18. Refresh failure does not clear the previous in-memory set.
19. Revocation in the DB takes effect within `PC2NUTS_TOKEN_REFRESH_SECONDS` (test-config: 1 second).
20. DB unreachable at startup → service still starts, anonymous traffic works, env-var tokens still work.

### Endpoint (extends `tests/test_api.py`)

21. `/health` includes `token_db_stale: false` when DB URL configured and refresh succeeds.
22. `/health` includes `token_db_stale: true` after a forced refresh failure.
23. `/health` omits `token_db_stale` when DB URL is unset.

### Regression

24. All 122 existing v0.16.0 tests stay green.

---

## 8. Documentation updates

### `README.md`

Replace the existing **Authentication & rate-limit bypass → Operator runbook** subsections (v1, env-var) with the v2 runbook from §2 of this spec. Keep:

- The behaviour summary table (unchanged).
- The disable-the-bypass-entirely note (now: unset `PC2NUTS_TOKEN_DB_URL` AND `PC2NUTS_TRUSTED_TOKENS`).
- Security notes (extend with the DR-fallback recommendation).

Add a small section explaining the union semantic: "DB-loaded tokens and `PC2NUTS_TRUSTED_TOKENS` env-var tokens both work; either can be empty; the env var serves as DR when the DB is unreachable."

### `CHANGELOG.md`

`[Unreleased]` → `### Added`:

> - **DB-backed trusted tokens** (#61): trusted-token storage moved from `PC2NUTS_TRUSTED_TOKENS` env var to a managed SQLite-compatible HTTP database. Configure with `PC2NUTS_TOKEN_DB_URL`. Tokens issued via `python -m scripts.tokens add --label "..."` take effect within ~60 s (configurable via `PC2NUTS_TOKEN_REFRESH_SECONDS`) — no container restart required. The env var continues to work as a union with the DB and serves as a disaster-recovery fallback when the DB is unreachable. New `/health` field `token_db_stale` flags refresh failures.

---

## 9. Out of scope (explicit)

- Per-token quotas, scopes, expiry.
- HTTP admin endpoint for token CRUD (operator CLI only).
- `last_used_at` write-back tracking.
- Forced deprecation / removal of `PC2NUTS_TRUSTED_TOKENS` (keep as supported channel).
- Migrating the primary TERCET lookup cache to the same database.
- Multi-region replication of the token DB beyond what the provider offers by default.
- Changing the on-the-wire HTTP API of `/lookup` or `/pattern`.

---

## 10. Decisions log (from brainstorming)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| Q1 | Database backend | Managed SQLite-compatible HTTP DB (option A) | Same provider as the rest of the deployment; SQLite-compatible mental model; negligible cost. |
| Q2 | Operator workflow | Bundled CLI (option B): `python -m scripts.tokens` | Single command per action; tests possible; ergonomic `list`/`revoke`. |
| Q3 | DB-down behaviour | Graceful degrade (option B), with env-var union as automatic DR | Reuses the existing "empty token set = bypass off" code path; env var becomes opt-in DR. |
| Q4 | Runtime refresh failure | Keep previous in-memory set; expose `token_db_stale` on `/health` | Standard cached-resource discipline. |
| Q5 | Refresh interval | 60 s default, configurable | Operator-friendly without log-spam. |
| Q6 | `last_used_at` | Skip in v2 | Either write hotspot or async batching — defer. |
| Q7 | Migration from v1 | Soft, no forced cutover | The union semantic makes "do nothing" a valid migration starting point. |
