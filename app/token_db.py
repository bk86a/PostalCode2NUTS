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
