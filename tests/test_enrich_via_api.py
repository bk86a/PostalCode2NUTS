"""Tests for scripts/enrich_via_api.py sanitization + geocode guard helpers.

Covers the two P4 follow-up fixes:
  * loose_extract_postal — pull a valid code out of a messy POSTAL_CODE field so a
    /lookup 422 (malformed input) can be retried instead of dropped to unresolved.
  * accept_geocode — reject cross-border geocode results (wrong-country NUTS3).
"""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "enrich_via_api", REPO / "scripts" / "enrich_via_api.py"
)
enrich = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(enrich)

# Real anchored regexes from app/postal_patterns.json for the countries that
# dominated the error_422 tail (ES, PT, IT, HU, FI, NL, HR).
IT = r"^(?:I[\s\-–—.]*|IT[\s\-–—.]*)?([0-9]{5})$"
PT = r"^(?:P[\s\-–—.]*|PT[\s\-–—.]*)?([0-9]{4})[\s\-]?([0-9]{3})$"
HU = r"^(?:H[\s\-–—.]*|HU[\s\-–—.]*)?([0-9]{4})$"
FI = r"^(?:FI(?:N)?[\s\-–—.]*)?([0-9]{5})$"
NL = r"^(?:NL[\s\-–—.]*)?(\d{4}\s?[A-Z]{2})$"
HR = r"^(?:HR[\s\-–—.]*)?([0-9]{5})$"


class TestLooseExtractPostal:
    def test_code_leads_the_field(self):
        # code at the start, trailing text (the common case)
        assert enrich.loose_extract_postal(IT, "37067 Valeggio Sul Mincio VR") == "37067"
        assert enrich.loose_extract_postal(HU, "1083 Budapest, Szigetvári utca 1.") == "1083"
        assert enrich.loose_extract_postal(FI, "00099 Helsingin kaupunki") == "00099"
        assert enrich.loose_extract_postal(HR, "42223 Varaždinske Toplice") == "42223"

    def test_hyphenated_pt_code(self):
        assert enrich.loose_extract_postal(PT, "4575-297 Paredes, PNF") == "4575-297"

    def test_alnum_code_trailing_in_field(self):
        # NL codes can trail the street; start-anchored fails, search succeeds
        assert enrich.loose_extract_postal(NL, "Lorentzstraat 212 1971 HX Ijmuiden") == "1971 HX"

    def test_already_clean_code_is_unchanged(self):
        assert enrich.loose_extract_postal(IT, "37067") == "37067"

    def test_no_code_present_returns_empty(self):
        # junk / test rows have no code-shaped token
        assert enrich.loose_extract_postal(IT, "mirceaTest8.03") == ""
        assert enrich.loose_extract_postal(IT, "aaaaaaaaa") == ""

    def test_missing_regex_or_input_returns_empty(self):
        assert enrich.loose_extract_postal(None, "37067 Something") == ""
        assert enrich.loose_extract_postal(IT, "") == ""

    def test_bad_regex_does_not_raise(self):
        assert enrich.loose_extract_postal("^([0-9]{5}$", "37067") == ""


class TestAcceptGeocode:
    def test_same_country_accepted(self):
        assert enrich.accept_geocode("AL", "AL022") is True
        assert enrich.accept_geocode("el", "EL642") is True  # case-insensitive

    def test_cross_border_rejected(self):
        # the failure modes observed in the estimated-rows sample
        assert enrich.accept_geocode("AL", "DEG01") is False
        assert enrich.accept_geocode("RS", "PL217") is False

    def test_empty_inputs_rejected(self):
        assert enrich.accept_geocode("AL", "") is False
        assert enrich.accept_geocode("", "AL022") is False
