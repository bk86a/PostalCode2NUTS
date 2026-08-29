"""Registry of outermost regions, OCTs and other non-NUTS European territories."""

import pytest

from app import territories


@pytest.fixture(autouse=True)
def loaded():
    territories.load_territories()


def test_iso_route_normalises_under_the_territory_scheme():
    # Extraction happens under DK, Greenland's scheme — not under "GL", which
    # has no pattern and would leave the country prefix in place.
    seen = []

    def spy(cc, value):
        seen.append(cc)
        return value.replace("DK-", "")

    cls = territories.classify("GL", "DK-3900", spy)
    assert seen == ["DK"]
    assert cls.postal_in_territory is True


def test_registry_has_twenty_six_entries():
    assert territories.count() == 26


def _raw(cc, value):
    """Extractor stub: the registry must not import postal_patterns."""
    return value.strip().upper()


def test_iso_route_finds_greenland():
    cls = territories.classify("GL", "3900", _raw)
    assert cls is not None
    assert cls.territory.id == "GL"
    assert cls.territory.status == "oct"
    assert cls.territory.in_nuts is False
    assert cls.postal_in_territory is True


def test_parent_route_finds_greenland_from_danish_code():
    cls = territories.classify("DK", "3900", _raw)
    assert cls is not None
    assert cls.territory.id == "GL"
    assert cls.postal_in_territory is True


def test_iso_route_rejects_code_outside_the_territory():
    # 2100 is a well-formed Danish code (Copenhagen) but is not Greenlandic.
    cls = territories.classify("GL", "2100", _raw)
    assert cls is not None
    assert cls.territory.id == "GL"
    assert cls.postal_in_territory is False


def test_exact_code_wins_over_a_longer_prefix_match():
    # 97133 is Saint-Barthélemy; it sits inside Guadeloupe's 971 prefix.
    assert territories.classify("FR", "97133", _raw).territory.id == "BL"
    assert territories.classify("FR", "97150", _raw).territory.id == "MF"
    assert territories.classify("FR", "97100", _raw).territory.id == "GP"


def test_reunion_cedex_blocks_are_reunion_not_the_caribbean_islands():
    # 977xx and 978xx are La Reunion CEDEX ranges. Saint-Barthelemy and
    # Saint-Martin each have exactly one postal code (97133, 97150); treating
    # the blocks as theirs mislabelled the Universite de La Reunion (97715),
    # the Rectorat (97743) and five Reunion lycees (978xx) as non-NUTS.
    for code in ("97715", "97743", "97705"):
        assert territories.classify("FR", code, _raw).territory.id == "RE"
    for code in ("97831", "97825", "97867"):
        assert territories.classify("FR", code, _raw).territory.id == "RE"
    # The islands keep their own single codes.
    assert territories.classify("FR", "97133", _raw).territory.id == "BL"
    assert territories.classify("FR", "97150", _raw).territory.id == "MF"


def test_island_iso_route_rejects_a_reunion_cedex_code():
    cls = territories.classify("BL", "97715", _raw)
    assert cls.territory.id == "BL" and cls.postal_in_territory is False
    cls = territories.classify("MF", "97831", _raw)
    assert cls.territory.id == "MF" and cls.postal_in_territory is False


def test_longest_prefix_wins():
    assert territories.classify("FR", "98800", _raw).territory.id == "NC"
    assert territories.classify("FR", "98713", _raw).territory.id == "PF"
    assert territories.classify("FR", "97400", _raw).territory.id == "RE"


def test_svalbard_exact_code_is_not_a_mainland_prefix():
    assert territories.classify("NO", "8099", _raw).territory.id == "SJ"
    assert territories.classify("NO", "9170", _raw).territory.id == "SJ"
    # 8000 is Bodø on the mainland — must not be captured by 8099.
    assert territories.classify("NO", "8000", _raw) is None


def test_monaco_is_not_a_territory():
    assert territories.classify("FR", "98000", _raw) is None


def test_mainland_codes_are_not_territories():
    assert territories.classify("FR", "75001", _raw) is None
    assert territories.classify("DK", "2100", _raw) is None
    assert territories.classify("ES", "28001", _raw) is None
    assert territories.classify("PT", "1000-001", _raw) is None


def test_island_prefixes_for_spain_and_portugal():
    assert territories.classify("ES", "35001", _raw).territory.id == "ES-CN"
    assert territories.classify("ES", "38001", _raw).territory.id == "ES-CN"
    assert territories.classify("PT", "9500-321", _raw).territory.id == "PT-20"
    assert territories.classify("PT", "9000-039", _raw).territory.id == "PT-30"


def test_territories_without_a_postal_system_accept_any_code():
    cls = territories.classify("AW", "", _raw)
    assert cls.territory.id == "AW"
    assert cls.territory.has_postal_system is False
    assert cls.postal_in_territory is True


def test_whole_country_territories_are_iso_only():
    assert territories.classify("FO", "100", _raw).territory.id == "FO"
    assert territories.classify("JE", "JE23XP", _raw).territory.id == "JE"
    # No Danish or UK postal code routes to them via the parent.
    assert territories.classify("DK", "100", _raw) is None


def test_unlisted_country_returns_none():
    assert territories.classify("DE", "10115", _raw) is None
    assert territories.classify("ZZ", "1234", _raw) is None


def test_iso_index_excludes_entries_without_an_iso_code():
    assert territories.get_by_iso("RE").id == "RE"
    assert territories.get_by_iso("ES-CN") is None
    codes = territories.territory_iso_codes()
    assert len(codes) == 23  # 26 entries minus Canarias, Açores and Madeira
    assert "ES-CN" not in codes
    assert {"GP", "MQ", "GF", "RE", "YT", "MF"} <= codes
    assert {"GL", "PF", "NC", "WF", "PM", "BL", "TF", "AW", "CW", "SX", "BQ"} <= codes
    assert {"SJ", "FO", "GI", "JE", "GG", "IM"} <= codes


def _oct_table_rows() -> list[list[str]]:
    """The (Territory, ISO) cells of the OCT table in docs/overseas_territories.md.

    Scoped to the table under the OCT heading, not the whole document: every ISO
    code and territory name also appears in the surrounding prose, so a document
    -wide search would pass even with every row deleted.
    """
    from pathlib import Path

    doc = (Path(__file__).resolve().parent.parent / "docs" / "overseas_territories.md").read_text(
        encoding="utf-8"
    )
    section = doc.split("## Overseas countries and territories", 1)[1].split("\n## ", 1)[0]
    lines = [ln.strip() for ln in section.splitlines()]
    header = next(i for i, ln in enumerate(lines) if ln.startswith("| Territory | ISO |"))
    rows = []
    for ln in lines[header + 2 :]:  # skip the header and its |---| separator
        if not ln.startswith("|"):
            break
        cells = [c.strip() for c in ln.strip("|").split("|")]
        rows.append(cells)
    return rows


def test_the_oct_table_lists_all_thirteen_annex_ii_territories():
    """Annex II counts 13 OCTs; ISO 3166-1 covers them with 11 codes. The table is
    per territory, so it must carry 13 rows - one each for Bonaire, Saba and Sint
    Eustatius, which share `BQ` and would otherwise collapse into a single row."""
    rows = _oct_table_rows()
    assert len(rows) == 13, f"expected 13 OCT rows, found {len(rows)}"

    octs = {t.iso: t for t in territories._registry if t.status == "oct"}
    assert len(octs) == 11

    listed = [(name, iso) for name, iso, *_ in rows]
    for iso, t in octs.items():
        if iso == "BQ":
            continue  # covered by the three-name check below
        matches = [n for n, i in listed if i.startswith(f"`{iso}`")]
        assert len(matches) == 1, f"{t.name} ({iso}) must have exactly one row, found {len(matches)}"
        assert matches[0].startswith(t.name), f"row for {iso} is '{matches[0]}', expected {t.name}"

    bq = [n for n, i in listed if i.startswith("`BQ`")]
    assert len(bq) == 3, f"BQ covers three Annex II OCTs, found {len(bq)} rows"
    for name in ("Bonaire", "Saba", "Sint Eustatius"):
        assert any(n.startswith(name) for n in bq), f"{name} is an Annex II OCT with no table row"


def test_the_oct_table_matches_the_registry_postal_ranges():
    """Postal-code cells are transcribed from app/territories.json - keep them true."""
    rows = {iso.split("`")[1]: codes for _, iso, codes, *_ in _oct_table_rows()}
    for t in (t for t in territories._registry if t.status == "oct"):
        cell = rows[t.iso]
        if not t.has_postal_system:
            assert cell == "none", f"{t.iso} has no postal system but the table says '{cell}'"
            continue
        for prefix in t.prefixes:
            assert f"`{prefix}" in cell, f"{t.iso} prefix {prefix} missing from '{cell}'"
        for code in t.exact:
            assert f"`{code}`" in cell, f"{t.iso} exact code {code} missing from '{cell}'"
