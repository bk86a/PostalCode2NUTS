"""Tests for scripts/tokens.py — operator CLI."""

import pytest


# Fake TokenDB used in place of the real one for CLI tests
class FakeTokenDB:
    def __init__(self, url: str = "fake://"):
        self.url = url
        self.calls: list[tuple[str, tuple, dict]] = []
        self.next_id = 1
        self.rows: list[dict] = []
        self.added: list[dict] = []
        self.revoked_ids: list[int] = []
        self.fail_with: Exception | None = None

    def init_schema(self):
        self.calls.append(("init_schema", (), {}))

    def add(self, value, label):
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append(("add", (value, label), {}))
        row_id = self.next_id
        self.next_id += 1
        self.added.append({"id": row_id, "value": value, "label": label})
        return row_id

    def list_active(self):
        return [r for r in self.rows if not r.get("revoked_at")]

    def list_all(self):
        return list(self.rows)

    def revoke(self, token_id):
        self.revoked_ids.append(token_id)
        for r in self.rows:
            if r["id"] == token_id and not r.get("revoked_at"):
                r["revoked_at"] = "2026-04-29T16:00:00"
                return True
        return False


@pytest.fixture
def fake_db(monkeypatch):
    from scripts import tokens

    fake = FakeTokenDB()
    monkeypatch.setattr(tokens, "_make_db", lambda url: fake)
    monkeypatch.setenv("PC2NUTS_TOKEN_DB_URL", "fake://")
    return fake


# ── init subcommand ────────────────────────────────────────────────────────


def test_init_calls_init_schema(fake_db, capsys):
    from scripts.tokens import main

    rc = main(["init"])
    assert rc == 0
    assert ("init_schema", (), {}) in fake_db.calls


# ── add subcommand ─────────────────────────────────────────────────────────


def test_add_generates_token_when_no_value(fake_db, capsys):
    from scripts.tokens import main

    rc = main(["add", "--label", "alice"])
    assert rc == 0
    out = capsys.readouterr().out
    assert len(fake_db.added) == 1
    generated = fake_db.added[0]["value"]
    assert len(generated) == 48  # 24 bytes hex
    assert all(c in "0123456789abcdef" for c in generated)
    assert generated in out
    assert "id=1" in out
    assert "token_id=" in out


def test_add_with_value_uses_provided(fake_db, capsys):
    from scripts.tokens import main

    rc = main(["add", "--label", "perf-test", "--value", "deadbeef" * 6])
    assert rc == 0
    assert fake_db.added[0]["value"] == "deadbeef" * 6


def test_add_failure_exits_non_zero(fake_db, capsys):
    from app.token_db import TokenDBError
    from scripts.tokens import main

    fake_db.fail_with = TokenDBError("UNIQUE constraint failed")
    rc = main(["add", "--label", "dup"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "UNIQUE" in err or "fail" in err.lower()


# ── list subcommand ────────────────────────────────────────────────────────


def test_list_default_active_only(fake_db, capsys):
    fake_db.rows = [
        {"id": 1, "label": "active-alpha-XXX", "created_at": "2026-01-01", "revoked_at": None},
        {"id": 2, "label": "revoked-beta-YYY", "created_at": "2026-01-02", "revoked_at": "2026-01-03"},
    ]
    from scripts.tokens import main

    rc = main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "active-alpha-XXX" in out
    assert "revoked-beta-YYY" not in out


def test_list_all_includes_revoked(fake_db, capsys):
    fake_db.rows = [
        {"id": 1, "label": "active-alpha-XXX", "created_at": "2026-01-01", "revoked_at": None},
        {"id": 2, "label": "revoked-beta-YYY", "created_at": "2026-01-02", "revoked_at": "2026-01-03"},
    ]
    from scripts.tokens import main

    main(["list", "--all"])
    out = capsys.readouterr().out
    assert "active-alpha-XXX" in out
    assert "revoked-beta-YYY" in out
    assert "revoked" in out.lower()


def test_list_never_prints_value(fake_db, capsys):
    fake_db.rows = [
        {"id": 1, "label": "a", "created_at": "2026-01-01", "revoked_at": None},
    ]
    from scripts.tokens import main

    main(["list"])
    out = capsys.readouterr().out
    import re

    assert re.search(r"[0-9a-f]{48}", out) is None


# ── revoke subcommand ──────────────────────────────────────────────────────


def test_revoke_calls_db(fake_db, capsys):
    fake_db.rows = [{"id": 5, "label": "x", "created_at": "2026-01-01", "revoked_at": None}]
    from scripts.tokens import main

    rc = main(["revoke", "5"])
    assert rc == 0
    assert 5 in fake_db.revoked_ids


def test_revoke_already_revoked_exits_zero(fake_db, capsys):
    fake_db.rows = [
        {"id": 5, "label": "x", "created_at": "2026-01-01", "revoked_at": "2026-04-28"}
    ]
    from scripts.tokens import main

    rc = main(["revoke", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "already" in out.lower()


# ── missing config ─────────────────────────────────────────────────────────


def test_missing_db_url_errors(monkeypatch, capsys):
    monkeypatch.delenv("PC2NUTS_TOKEN_DB_URL", raising=False)
    from scripts.tokens import main

    rc = main(["list"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "PC2NUTS_TOKEN_DB_URL" in err


def test_db_url_arg_overrides_env(monkeypatch, capsys):
    monkeypatch.delenv("PC2NUTS_TOKEN_DB_URL", raising=False)
    from scripts import tokens
    from scripts.tokens import main

    captured_urls: list[str] = []
    monkeypatch.setattr(
        tokens,
        "_make_db",
        lambda url: (captured_urls.append(url), FakeTokenDB(url))[1],
    )
    rc = main(["--db-url", "https://override.example", "init"])
    assert rc == 0
    assert captured_urls == ["https://override.example"]
