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
        assert enrich.accept_geocode("GR", "EL642") is True  # ISO GR remaps to NUTS EL

    def test_cross_border_rejected(self):
        # the failure modes observed in the estimated-rows sample
        assert enrich.accept_geocode("AL", "DEG01") is False
        assert enrich.accept_geocode("RS", "PL217") is False

    def test_empty_inputs_rejected(self):
        assert enrich.accept_geocode("AL", "") is False
        assert enrich.accept_geocode("", "AL022") is False


class TestFoundFlagBranch:
    """process_row must read the v3 `found` flag and still accept 2.x statuses."""

    @staticmethod
    def _client(handler):
        import httpx2 as httpx

        return httpx.Client(transport=httpx.MockTransport(handler))

    @staticmethod
    def _row():
        return {
            "OID": "1",
            "COUNTRY_CD": "DE",
            "CITY": "",
            "STREET_NAME_AND_NUMBER": "",
            "POSTAL_CODE": "99999",
        }

    def _run(self, handler):
        import httpx2 as httpx  # noqa: F401

        with self._client(handler) as c:
            return enrich.process_row(self._row(), c, "http://api", {}, 5.0)

    def test_v3_found_false_is_not_found(self):
        import httpx2 as httpx

        out = self._run(
            lambda r: httpx.Response(
                200, json={"found": False, "message": "No NUTS mapping found ...", "nuts3": None}
            )
        )
        assert out["POSTAL_MATCH_TYPE"] == "not_found"
        assert out["RESOLUTION_METHOD"] == "unresolved"

    def test_v3_unserved_country_is_unsupported(self):
        import httpx2 as httpx

        out = self._run(
            lambda r: httpx.Response(
                200, json={"found": False, "message": "Country 'ZZ' is not served by this instance."}
            )
        )
        assert out["POSTAL_MATCH_TYPE"] == "unsupported"
        assert out["RESOLUTION_METHOD"] == "unsupported"

    def test_v3_hit_is_read_as_before(self):
        import httpx2 as httpx

        out = self._run(
            lambda r: httpx.Response(
                200,
                json={
                    "found": True,
                    "message": None,
                    "nuts3": "DE300",
                    "match_type": "exact",
                    "nuts3_confidence": 1.0,
                },
            )
        )
        assert out["NUTS3_FINAL"] == "DE300"
        assert out["RESOLUTION_METHOD"] == "postal:exact"

    def test_legacy_404_still_understood(self):
        import httpx2 as httpx

        out = self._run(lambda r: httpx.Response(404, json={"detail": "no mapping"}))
        assert out["POSTAL_MATCH_TYPE"] == "not_found"

    def test_legacy_400_still_understood(self):
        import httpx2 as httpx

        out = self._run(lambda r: httpx.Response(400, json={"detail": "not supported"}))
        assert out["POSTAL_MATCH_TYPE"] == "unsupported"
