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
