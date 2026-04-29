# Auth-token bypass — design

**Status:** approved (brainstorming complete)
**Date:** 2026-04-29
**Issue:** [#60](https://github.com/bk86a/PostalCode2NUTS/issues/60)
**Scope:** allow trusted callers to bypass the per-IP `60/minute` rate limit by presenting an opaque token via `Authorization: Bearer`. Operator manages a small static set of tokens via an environment variable; container restart applies changes.

---

## 1. Goals and non-goals

### Goals

- Bypass the per-IP `60/minute` cap for callers presenting a recognised token.
- Keep the per-IP cap as the default for unauthenticated traffic.
- Issue tokens manually to a small set of known callers (operator-driven, no self-service).
- Revoke a specific token by removing it from the env var and restarting the container.
- Log audit lines for trusted requests with a non-reversible token id; never log raw tokens.

### Non-goals

- Per-token quotas. Trusted tokens are *fully* exempt in v1.
- OAuth, JWT, signatures, asymmetric crypto.
- Self-service signup, rotation automation, expiry, scopes, multi-tenant pricing.
- Encryption at rest — the env var is the trust boundary; operator owns secret hygiene.
- Authenticated `/health` — `/health` stays anonymous (useful for monitoring).
- Per-token Prometheus metrics — possible later, not v1.

---

## 2. Operational runbook (consult this when issuing or revoking a token)

This section is the operator's reference. It is duplicated near-verbatim in `README.md` so the running system can be administered without reading source.

### Issue a new token

```bash
# 1. Generate locally (48 hex chars / 192 bits)
openssl rand -hex 24

# 2. Add the printed token to PC2NUTS_TRUSTED_TOKENS in the production deployment's
#    environment configuration. Multiple tokens are comma-separated; whitespace and
#    empty entries are tolerated.
#
#    Example value with two active tokens:
#    PC2NUTS_TRUSTED_TOKENS=9e7a3f...d2,4b1c8e...77

# 3. Restart the service container to load the new env value.
#    The SQLite postal-code cache survives the restart, so cold-start is ~30 s.

# 4. Verify the new token bypasses the rate limit:
curl -i -H "Authorization: Bearer <new_token>" \
     "https://<service-host>/lookup?country=DE&postal_code=10115"
# → 200, no X-RateLimit-* headers consumed; audit log shows token_id=<first 8 hex>

# 5. Hand the raw token to the consumer over a confidential channel
#    (1Password, Signal, encrypted email — not Slack, not GitHub issues).
```

### Revoke a token

```bash
# 1. Remove the token entry from PC2NUTS_TRUSTED_TOKENS in the env config.
# 2. Restart the container.
# 3. Verify the revoked token is rejected:
curl -i -H "Authorization: Bearer <revoked_token>" \
     "https://<service-host>/lookup?country=DE&postal_code=10115"
# → 401 Unauthorized
```

### Find the token id of a logged request

```bash
# Audit log lines contain token_id=<8-hex>. To match a token to its id locally:
echo -n "<token>" | sha256sum | cut -c1-8
```

### Disable the bypass entirely

Unset or empty `PC2NUTS_TRUSTED_TOKENS`. All traffic falls back to the per-IP cap. No code change needed.

---

## 3. Configuration

| Env var | Default | Description |
|---|---|---|
| `PC2NUTS_TRUSTED_TOKENS` | `""` (unset) | Comma-separated list of valid bypass tokens. Empty / unset → bypass disabled; behaviour identical to today. Whitespace and empty entries between commas are stripped. |

`app/config.py` exposes a `trusted_tokens` property that parses the env var into a deduplicated `frozenset[str]` at startup. The set is read once at startup; runtime mutation is not supported (matches Approach A from the brainstorming session).

`app/settings.json` does **not** carry token values (avoids accidental commit). It optionally documents the env var via a comment-style `_meta` entry; not strictly required.

---

## 4. Runtime behaviour

| Request | Result | HTTP status |
|---|---|---|
| No `Authorization` header | Existing per-IP `60/minute` cap | `200` or `429` |
| `Authorization: Bearer <valid_token>` | **Rate limit fully bypassed.** Audit log emitted. | `200` (or any natural endpoint code) |
| `Authorization: Bearer <unknown_token>` | Reject — wrong tokens fail loudly | `401 Unauthorized` |
| `Authorization: <scheme other than Bearer>` | Reject — caller intent unclear | `400 Bad Request` |
| Malformed header (e.g. `Bearer` with no value, missing space) | Reject | `400 Bad Request` |

`/health` is unaffected (no `@limiter.limit`, anonymous).

---

## 5. Implementation

### 5.1 New module: `app/auth.py`

Single-purpose module, < 80 lines. Exposes:

```python
def extract_bearer(request: Request) -> str | None:
    """Return the bearer token from the Authorization header, or None if absent.
    Raises HTTPException(400) on malformed Authorization headers
    (e.g. wrong scheme, empty value)."""

def is_trusted(token: str) -> bool:
    """Constant-time membership test against settings.trusted_tokens
    using hmac.compare_digest in a loop. Returns False for any falsy input."""

def token_id(token: str) -> str:
    """First 8 chars of sha256(token).hexdigest(). Used in audit logs."""

def is_trusted_request(request: Request) -> bool:
    """Composed predicate: extract_bearer → is_trusted. Used as slowapi exempt_when.
    Re-raises HTTPException(401) when a token is present but unknown — this surfaces
    as the 401 in the response, not as an exempt-or-not boolean."""
```

### 5.2 slowapi integration

`@limiter.limit(...)` accepts an `exempt_when` callable. `app/main.py` imports `is_trusted_request` and passes it on the two existing decorators:

```python
@limiter.limit(settings.rate_limit, exempt_when=lambda: is_trusted_request(request_var.get()))
def lookup_postal_code(request: Request, ...): ...

@limiter.limit(settings.rate_limit, exempt_when=lambda: is_trusted_request(request_var.get()))
def get_pattern(request: Request, ...): ...
```

`exempt_when` does not receive the `Request`, so we propagate it via a `contextvars.ContextVar` set by a tiny middleware that runs before the limiter.

If `exempt_when=`'s lack of a request parameter turns out to make this fragile, the fallback is to inline the bypass check in the route handler and call `@limiter.exempt` conditionally — slightly clunkier, no functional change.

### 5.3 Audit logging

Existing `AccessLogMiddleware` (in `app/main.py`) gains an extra trailing field per line:

```
2026-04-29 09:15:10,420  127.0.0.1 GET /lookup 200 1.6ms token_id=9e7a3f12
```

The token id is computed once per request and attached to the log record. Anonymous requests get no `token_id=` field (omitted, not empty). Token values themselves never enter the logger.

### 5.4 401 vs 400 plumbing

`extract_bearer` raises `HTTPException(400, "malformed Authorization header")` when the header is present but unparseable. `is_trusted_request` raises `HTTPException(401, "invalid token")` when a syntactically valid token is unknown. Both propagate through FastAPI's normal exception handler.

This means the error path is *not* "exempt or not" — it short-circuits with the right status before slowapi runs.

### 5.5 Constant-time comparison

The membership test uses `hmac.compare_digest` against each known token, not `in` on a set. Reason: the token list is tiny (≤ ~10 entries) and timing-safe equality avoids leaking whether a candidate matched the first byte of any registered token.

```python
def is_trusted(candidate: str) -> bool:
    if not candidate:
        return False
    return any(hmac.compare_digest(candidate, t) for t in settings.trusted_tokens)
```

### 5.6 Files affected

| File | Change |
|---|---|
| `app/auth.py` | **new** — extract / validate / token-id helpers |
| `app/config.py` | new `trusted_tokens` property parsing `PC2NUTS_TRUSTED_TOKENS` |
| `app/main.py` | import auth helpers; pass `exempt_when` on `@limiter.limit`; small middleware to propagate `Request` via contextvar; extend access log line |
| `tests/test_auth.py` | **new** — unit tests for `app/auth.py` |
| `tests/test_api.py` | new endpoint tests for the four behaviour rows in §4 |
| `tests/conftest.py` | optional fixture parametrising `settings.trusted_tokens` |
| `README.md` | new "Authentication & rate-limit bypass" section, including the operational runbook from §2 |
| `CHANGELOG.md` | new entry |
| `app/settings.json` | no functional change (token values stay in the env var, not in JSON) |

---

## 6. Security considerations

- **Tokens are bearer credentials.** Anyone holding the string can use the API at full rate. Treat them like passwords.
- **Transport.** The deployment runs over HTTPS; the rate-limit-bypass token is therefore protected in transit. Never accept a bearer token over plain HTTP — relying on the deployment's TLS termination.
- **At rest.** Tokens live in the deployment's env config (admin-only access). Not encrypted at rest beyond what the env-var store provides.
- **Logs.** Only the 8-char SHA-256 prefix appears in logs. Even a full log dump cannot recover the original token (preimage of an 8-hex prefix has ~2^32 candidates × the unknown remainder of the SHA — not exploitable).
- **Timing.** `hmac.compare_digest` used for all comparisons.
- **Revocation latency.** Bound by container restart time (~30 s). For an emergency leak: remove all tokens, restart, then re-issue good ones. The `/lookup` endpoint stays available throughout (anonymous traffic continues; only bypass capability is interrupted).
- **Audit retention.** The existing rotating access log applies; no new retention policy added.

---

## 7. Tests (TDD, written before implementation)

### Unit (`tests/test_auth.py`)

1. `extract_bearer` — header absent → `None`.
2. `extract_bearer` — `Bearer <token>` → token.
3. `extract_bearer` — `bearer <token>` (lowercase scheme) → token (case-insensitive scheme per RFC 7235).
4. `extract_bearer` — `Basic <creds>` → raises `HTTPException(400)`.
5. `extract_bearer` — `Bearer ` (no value) → raises `HTTPException(400)`.
6. `extract_bearer` — `Bearer  multiple words` → raises `HTTPException(400)`.
7. `is_trusted` — match against a single configured token → `True`.
8. `is_trusted` — match against one of several configured tokens → `True`.
9. `is_trusted` — unknown token → `False`.
10. `is_trusted` — empty string → `False`.
11. `is_trusted` — empty configured set → `False` for any input.
12. `token_id` — deterministic, 8 hex chars, stable across calls.
13. `token_id` — different tokens → different ids.

### Endpoint (`tests/test_api.py`)

14. No `Authorization` header → existing rate-limit behaviour preserved (the existing test set still passes).
15. `Authorization: Bearer <valid>` → `>60` requests in a minute all return `200`.
16. `Authorization: Bearer <invalid>` → `401`.
17. `Authorization: Basic <creds>` → `400`.
18. `Authorization: Bearer ` (no value) → `400`.
19. Empty `PC2NUTS_TRUSTED_TOKENS` → `Authorization: Bearer anything` is ignored; rate limit still applies (header has no privileged effect when bypass is disabled).
20. Audit log line contains `token_id=<8-hex>` for trusted requests, no `token_id=` field for anonymous.
21. `/health` is unaffected by the header in any of its forms.

### Notes

- Test (15) requires either lowering the rate limit for the test (e.g. via env var) or making >60 requests in the test — both are practical; prefer the env-var override to keep tests fast.
- Test (20) reads from the configured access log handler; the existing test infra captures via `caplog`.

---

## 8. Documentation updates

### `README.md`

A new top-level section **Authentication & rate-limit bypass** placed after **Configuration** and before **Five-tier lookup**. Contents:

1. Why the feature exists (one paragraph).
2. The full **operator runbook** from §2 of this spec — verbatim, so the README is the canonical reference for issuance.
3. The four behaviour rows from §4 as a small table.
4. Pointers to the env var in the existing Configuration table.

### `CHANGELOG.md`

`[Unreleased]` entry under `### Added`:

> - **Auth-token bypass** (#60): trusted callers can bypass the per-IP rate limit by presenting `Authorization: Bearer <token>`. Tokens are managed via the new `PC2NUTS_TRUSTED_TOKENS` comma-separated env var. Invalid tokens return `401`; malformed headers return `400`. Audit lines log a non-reversible token id only.

### `docs/postal_code_format_analysis.md`

No changes (unrelated to the feature).

---

## 9. Out of scope (explicit)

- Per-token quotas, scopes, expiry, or rotation hooks. (v2 candidates if usage patterns change.)
- A `/admin/tokens` HTTP endpoint for runtime management. (Approach C from brainstorming — rejected for v1.)
- File-mounted tokens with live reload. (Approach B — rejected for v1; `~30 s` restart is acceptable at the expected cadence of "a few times ever".)
- HMAC-signed tokens, JWT, asymmetric signing.
- Token usage metrics / Prometheus counters per token id.
- IP allow-listing as a separate orthogonal feature.
- Authenticating `/health`.

---

## 10. Decisions log (from brainstorming session)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| Q1 | Operational style | Approach A — env var + restart | Cadence is "a few times ever"; simplest viable design pays off. |
| Q2 | Token format | Plain opaque hex (`openssl rand -hex 24`) | No embedded info needed at this scale; prefixed format buys little when only ~handful of tokens exist. |
| Q3 | Header scheme | `Authorization: Bearer` | Standard, idiomatic, well-supported by clients. (Resolved during issue split.) |
| Q4 | Rate-limit treatment | Full exemption | Trusted tokens are batch/research callers; quota would re-introduce friction. (Resolved during issue split.) |
| Q5 | Wrong token behaviour | `401`, not silent fallthrough | Sending a token is deliberate; failing loudly is friendlier than appearing to be rate-limited. |
| Q6 | Audit | SHA-256 prefix only | Reversibility risk vs operator's need to identify tokens — prefix balances both. |
| Q7 | `/health` auth | Anonymous | Monitoring tools shouldn't carry secrets. |
