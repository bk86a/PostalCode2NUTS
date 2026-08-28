"""Narrowed /pattern regexes for territories that are both in NUTS and postal-coded.

Two conditions, and only both together:
  1. the territory is linked to NUTS (`in_nuts`), so it resolves to a code of its own;
  2. it has postal codes at all (`has_postal_system`).

Only then does /pattern advertise the territory's own range instead of the
administering country's whole numbering space.
"""

import re

import pytest

from app import territories
from app.postal_patterns import POSTAL_PATTERNS, narrow_to_ranges

# (iso, parent, an in-range code, an out-of-range parent code)
QUALIFYING = [
    ("GP", "FR", "97110", "75001"),
    ("MQ", "FR", "97200", "75001"),
    ("GF", "FR", "97300", "75001"),
    ("RE", "FR", "97400", "75001"),
    ("YT", "FR", "97600", "75001"),
    ("SJ", "NO", "9170", "0150"),
]


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """The limiter's 120/minute budget is per-IP and every TestClient shares the
    'testclient' host, so a module this endpoint-heavy would otherwise spend the
    whole suite's allowance and 429 whichever test happens to run last."""
    from app.limiter import limiter

    limiter.reset()
    yield


def _iso_territories():
    return [t for t in territories._registry if t.iso]


def test_exactly_six_iso_coded_territories_meet_both_conditions():
    qualifying = {t.iso for t in _iso_territories() if t.in_nuts and t.has_postal_system}
    assert qualifying == {iso for iso, *_ in QUALIFYING}


@pytest.mark.parametrize("iso,parent,in_range,out_of_range", QUALIFYING)
def test_qualifying_territory_gets_its_own_range(client, iso, parent, in_range, out_of_range):
    r = client.get("/pattern", params={"country": iso})
    assert r.status_code == 200
    body = r.json()
    assert body["country_code"] == iso
    assert re.match(body["regex"], in_range), f"{iso} must accept its own code {in_range}"
    assert not re.match(body["regex"], out_of_range), (
        f"{iso} must reject {out_of_range}, which is {parent}'s but not {iso}'s"
    )
    # The example must be a code the pattern itself accepts.
    assert re.match(body["regex"], body["example"].split(",")[0].strip())


@pytest.mark.parametrize("iso,parent,in_range,_out", QUALIFYING)
def test_qualifying_territory_still_accepts_the_parents_country_prefixes(client, iso, parent, in_range, _out):
    """Narrowing restricts the digits, not the accepted `F-` / `NO-` prefixes."""
    rx = client.get("/pattern", params={"country": iso}).json()["regex"]
    for prefixed in (in_range, f"{parent[0]}-{in_range}", f"{parent}-{in_range}"):
        assert re.match(rx, prefixed), f"{iso} must still accept {prefixed}"


def test_territory_failing_either_condition_keeps_the_parent_pattern(client):
    """Fails condition 1 (not in NUTS) -> unchanged, even though it has postal codes."""
    for iso, parent in (("NC", "FR"), ("GL", "DK"), ("MF", "FR"), ("BL", "FR")):
        t = territories.get_by_iso(iso)
        assert t.has_postal_system and not t.in_nuts
        body = client.get("/pattern", params={"country": iso}).json()
        assert body["regex"] == client.get("/pattern", params={"country": parent}).json()["regex"]


def test_territory_with_no_postal_system_has_no_range_to_narrow_to():
    """Fails condition 2 -> nothing to narrow. (The endpoint's own answer for these
    is covered in test_territory_api.py; this pins the rule, not the error shape.)"""
    for iso in ("AW", "CW", "SX", "BQ"):
        t = territories.get_by_iso(iso)
        assert not t.has_postal_system
        assert not t.exact and not t.prefixes
        assert narrow_to_ranges(POSTAL_PATTERNS["FR"], t.exact, t.prefixes) is None


def test_ordinary_country_patterns_are_untouched(client):
    for cc in ("FR", "NO", "DK", "DE", "PL"):
        assert (
            client.get("/pattern", params={"country": cc}).json()["regex"] == (POSTAL_PATTERNS[cc]["regex"])
        )


class TestNarrowToRanges:
    def test_exact_codes_and_prefixes_combine(self):
        out = narrow_to_ranges(POSTAL_PATTERNS["NO"], ("8099",), ("917",))
        assert "(8099|917[0-9])" in out["regex"]

    def test_returns_none_without_ranges(self):
        assert narrow_to_ranges(POSTAL_PATTERNS["FR"], (), ()) is None

    def test_returns_none_for_a_two_group_parent_pattern(self):
        # PT splits its code across two groups; narrowing is not attempted.
        assert narrow_to_ranges(POSTAL_PATTERNS["PT"], (), ("95",)) is None

    def test_narrowed_pattern_keeps_the_parents_other_keys(self):
        out = narrow_to_ranges(POSTAL_PATTERNS["FR"], (), ("974",))
        assert out["expected_digits"] == POSTAL_PATTERNS["FR"]["expected_digits"]
