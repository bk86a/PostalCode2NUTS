"""Operator CLI for the trusted-token registry.

Reads the database URL from PC2NUTS_TOKEN_DB_URL (or --db-url override).
Subcommands: init | add | list | revoke.

See docs/superpowers/specs/2026-04-29-db-backed-trusted-tokens-design.md.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from typing import Sequence

from app.token_db import TokenDB, TokenDBError


def _make_db(url: str) -> TokenDB:
    """Indirection seam for tests."""
    return TokenDB(url)


def _token_id(token: str) -> str:
    """Audit prefix — first 8 hex chars of sha256(token)."""
    return hashlib.sha256(token.encode()).hexdigest()[:8]


def _resolve_db_url(args_url: str | None) -> str | None:
    if args_url:
        return args_url
    env = os.environ.get("PC2NUTS_TOKEN_DB_URL", "").strip()
    return env or None


def _cmd_init(db: TokenDB) -> int:
    db.init_schema()
    print("Schema initialised (idempotent).")
    return 0


_HEX_CHARS = frozenset("0123456789abcdef")


def _cmd_add(db: TokenDB, label: str, value: str | None) -> int:
    if value is not None:
        if len(value) < 32 or not all(c in _HEX_CHARS for c in value.lower()):
            print(
                "ERROR: --value must be at least 32 hex chars (lowercase). "
                "Did you mean to omit --value to generate a fresh 48-hex token?",
                file=sys.stderr,
            )
            return 2
        token = value.lower()
    else:
        token = secrets.token_hex(24)
    try:
        new_id = db.add(token, label)
    except TokenDBError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Generated: {token}")
    print(f"Inserted id={new_id}, label={label!r}, token_id={_token_id(token)}")
    return 0


def _cmd_list(db: TokenDB, show_all: bool) -> int:
    rows = db.list_all() if show_all else db.list_active()
    if not rows:
        print("(no tokens)")
        return 0
    print(f"{'id':>4}  {'label':30}  {'created_at':24}  status")
    print("-" * 80)
    for r in rows:
        rid = r.get("id", "?")
        label = (r.get("label") or "")[:30]
        created = r.get("created_at", "")
        revoked = r.get("revoked_at")
        status = f"revoked@{revoked}" if revoked else "active"
        print(f"{rid:>4}  {label:30}  {created:24}  {status}")
    return 0


def _cmd_revoke(db: TokenDB, token_id_arg: int) -> int:
    changed = db.revoke(token_id_arg)
    if changed:
        print(f"Token id={token_id_arg} revoked.")
    else:
        print(f"Token id={token_id_arg} already revoked (or does not exist).")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.tokens", description=__doc__)
    parser.add_argument("--db-url", help="Override PC2NUTS_TOKEN_DB_URL")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Create the trusted_tokens table (idempotent)")

    p_add = sub.add_parser("add", help="Issue a new token")
    p_add.add_argument("--label", required=True, help="Human-readable label")
    p_add.add_argument(
        "--value",
        help="Use the provided 48-hex value instead of generating one. "
        "Used for migrating existing v1 tokens.",
    )

    p_list = sub.add_parser("list", help="List tokens (active by default)")
    p_list.add_argument("--all", action="store_true", help="Include revoked tokens")

    p_revoke = sub.add_parser("revoke", help="Revoke a token by id")
    p_revoke.add_argument("id", type=int, help="Token id to revoke")

    args = parser.parse_args(argv)

    url = _resolve_db_url(args.db_url)
    if not url:
        print(
            "ERROR: PC2NUTS_TOKEN_DB_URL is not set. Provide --db-url or set the environment variable.",
            file=sys.stderr,
        )
        return 2

    db = _make_db(url)

    if args.cmd == "init":
        return _cmd_init(db)
    if args.cmd == "add":
        return _cmd_add(db, args.label, args.value)
    if args.cmd == "list":
        return _cmd_list(db, args.all)
    if args.cmd == "revoke":
        return _cmd_revoke(db, args.id)

    parser.error(f"unknown subcommand: {args.cmd}")  # unreachable due to required=True
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
