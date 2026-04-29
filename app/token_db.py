"""Thin HTTP client for the configured SQLite-compatible managed database.

See docs/superpowers/specs/2026-04-29-db-backed-trusted-tokens-design.md.

The wire shape (POST /query with {sql, params}, response {rows, rowsAffected,
lastInsertRowId}) is the working assumption; adjust `execute` if the
configured provider differs.
"""

from __future__ import annotations

from typing import Any

import httpx


class TokenDBError(Exception):
    """Raised when the token database returns an error or is unreachable."""


class TokenDB:
    """Minimal HTTP client over a SQLite-compatible managed database.

    All methods are blocking. Callers running inside an asyncio loop should
    wrap calls in asyncio.to_thread().
    """

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def execute(self, sql: str, params: list[Any] | None = None) -> list[dict]:
        """Send a SQL statement. Returns the `rows` list from the response.

        Writes (INSERT/UPDATE/DELETE/CREATE) typically return an empty list
        unless the SQL uses RETURNING.
        """
        payload = {"sql": sql, "params": list(params) if params else []}
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(f"{self.url}/query", json=payload, headers={})
                resp.raise_for_status()
                body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TokenDBError(f"DB request failed: {exc}") from exc
        return body.get("rows") or []

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
