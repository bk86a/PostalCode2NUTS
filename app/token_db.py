"""Thin HTTP client for a libsql / SQLite-over-HTTP managed database.

See docs/superpowers/specs/2026-04-29-db-backed-trusted-tokens-design.md.

Wire protocol: Hrana v2 (libsql HTTP). Endpoint is ``POST {base}/v2/pipeline``
where {base} is the database URL with ``libsql://`` rewritten to ``https://``.
The single-statement request is wrapped in ``{requests: [{type: "execute",
stmt: {sql, args}}]}``; rows are returned as arrays of typed value objects
``{type, value}`` with one row per query result, alongside a ``cols`` list
that gives column names. This adapter converts back and forth between
Python-native types and the typed-value envelope so callers get plain dicts.

Authentication: a Bearer token in the Authorization header. Both URL and
token come from environment configuration; no values are committed.
"""

from __future__ import annotations

from typing import Any

import httpx


class TokenDBError(Exception):
    """Raised when the token database returns an error or is unreachable."""


def _to_libsql_arg(v: Any) -> dict:
    """Encode a Python value as a libsql typed value object."""
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):
        # libsql has no native bool; SQLite stores 0/1
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    if isinstance(v, (bytes, bytearray)):
        import base64

        return {"type": "blob", "base64": base64.b64encode(bytes(v)).decode("ascii")}
    return {"type": "text", "value": str(v)}


def _from_libsql_value(cell: dict) -> Any:
    """Decode a libsql typed value object back to a Python value."""
    t = cell.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(cell["value"])
    if t == "float":
        return float(cell["value"])
    if t == "blob":
        import base64

        return base64.b64decode(cell["base64"])
    # text and unknown types fall through as strings
    return cell.get("value")


class TokenDB:
    """Minimal HTTP client over a libsql managed database.

    All methods are blocking. Callers running inside an asyncio loop should
    wrap calls in asyncio.to_thread().
    """

    def __init__(self, url: str, auth_token: str = "") -> None:  # nosec B107 — empty default means "no auth", not a hardcoded credential
        # libsql://host/ → https://host (drop trailing slash, swap scheme)
        u = url.strip()
        if u.startswith("libsql://"):
            u = "https://" + u[len("libsql://") :]
        self.url = u.rstrip("/")
        self.auth_token = auth_token

    def execute(self, sql: str, params: list[Any] | None = None) -> list[dict]:
        """Send a SQL statement. Returns the result rows as a list of dicts.

        Each dict maps column name → Python-native value. Writes (INSERT/UPDATE/
        DELETE/CREATE) typically return an empty list unless the SQL uses RETURNING.

        Raises TokenDBError on transport failure or libsql-reported error.
        """
        stmt: dict = {"sql": sql}
        if params:
            stmt["args"] = [_to_libsql_arg(p) for p in params]
        payload = {"requests": [{"type": "execute", "stmt": stmt}]}
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(f"{self.url}/v2/pipeline", json=payload, headers=headers)
                resp.raise_for_status()
                body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TokenDBError(f"DB request failed: {exc}") from exc

        results = body.get("results") or []
        if not results:
            return []
        first = results[0]
        if first.get("type") != "ok":
            err = (first.get("error") or {}).get("message", "unknown error")
            raise TokenDBError(f"DB statement failed: {err}")
        result = (first.get("response") or {}).get("result") or {}
        cols = [c.get("name") for c in (result.get("cols") or [])]
        rows = result.get("rows") or []
        return [{cols[i]: _from_libsql_value(cell) for i, cell in enumerate(row)} for row in rows]

    # ── Schema ──────────────────────────────────────────────────────────────

    def init_schema(self) -> None:
        """Idempotently create the trusted_tokens table and index."""
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS trusted_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                value       TEXT NOT NULL UNIQUE,
                label       TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                revoked_at  TEXT
            )
            """
        )
        self.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trusted_tokens_active
                ON trusted_tokens (value)
                WHERE revoked_at IS NULL
            """
        )

    # ── Mutations ───────────────────────────────────────────────────────────

    def add(self, value: str, label: str) -> int:
        """Insert a new trusted token. Returns the new row id.

        Raises TokenDBError on uniqueness violation or transport failure.
        """
        rows = self.execute(
            "INSERT INTO trusted_tokens (value, label) VALUES (?, ?) RETURNING id",
            [value, label],
        )
        if not rows:
            raise TokenDBError("INSERT did not return an id")
        return int(rows[0]["id"])

    def revoke(self, token_id: int) -> bool:
        """Mark a token as revoked. Idempotent — returns False if already revoked."""
        rows = self.execute(
            "UPDATE trusted_tokens "
            "SET revoked_at = datetime('now') "
            "WHERE id = ? AND revoked_at IS NULL "
            "RETURNING id",
            [token_id],
        )
        return bool(rows)

    # ── Queries ─────────────────────────────────────────────────────────────

    def list_active(self) -> list[dict]:
        """Return all rows where revoked_at IS NULL."""
        return self.execute(
            "SELECT id, value, label, created_at FROM trusted_tokens WHERE revoked_at IS NULL"
        )

    def list_all(self) -> list[dict]:
        """Return all rows, never including the raw value column."""
        return self.execute("SELECT id, label, created_at, revoked_at FROM trusted_tokens ORDER BY id")
