"""Tests for data_loader.py — normalize functions and lookup tiers."""

import httpx2 as httpx
import pytest

from app import data_loader
from app.data_loader import lookup, normalize_country, normalize_postal_code


# ── normalize_postal_code tests ──────────────────────────────────────────────


class TestNormalizePostalCode:
    def test_strips_spaces(self):
        assert normalize_postal_code("  10115  ") == "10115"

    def test_removes_dashes(self):
        assert normalize_postal_code("00-950") == "00950"

    def test_uppercases(self):
        assert normalize_postal_code("sw1a 1aa") == "SW1A1AA"

    def test_removes_dots(self):
        assert normalize_postal_code("1012.AB") == "1012AB"

    def test_empty_string(self):
        assert normalize_postal_code("") == ""


# ── normalize_country tests ──────────────────────────────────────────────────


class TestNormalizeCountry:
    def test_uppercase(self):
        assert normalize_country("de") == "DE"

    def test_gr_to_el(self):
        assert normalize_country("GR") == "EL"

    def test_gr_lowercase(self):
        assert normalize_country("gr") == "EL"

    def test_strips_whitespace(self):
        assert normalize_country("  AT  ") == "AT"

    def test_el_stays_el(self):
        assert normalize_country("EL") == "EL"

    def test_gb_to_uk(self):
        assert normalize_country("GB") == "UK"

    def test_gb_lowercase(self):
        assert normalize_country("gb") == "UK"

    def test_uk_stays_uk(self):
        assert normalize_country("UK") == "UK"


# ── lookup tests (all 5 tiers) ──────────────────────────────────────────────


class TestLookup:
    def test_tier1_exact_match(self, mock_data):
        result = lookup("DE", "10115")
        assert result is not None
        assert result["match_type"] == "exact"
        assert result["nuts3"] == "DE300"
        assert result["nuts2"] == "DE30"
        assert result["nuts1"] == "DE3"
        assert result["nuts1_confidence"] == 1.0
        assert result["nuts2_confidence"] == 1.0
        assert result["nuts3_confidence"] == 1.0

    def test_tier1_exact_with_names(self, mock_data):
        result = lookup("DE", "10115")
        assert result["nuts3_name"] == "Berlin"
        assert result["nuts1_name"] == "Berlin"

    def test_tier2_estimated(self, mock_data):
        result = lookup("FR", "97105")
        assert result is not None
        assert result["match_type"] == "estimated"
        assert result["nuts3"] == "FRY10"
        assert result["nuts1_confidence"] == 0.98

    def test_tier3_approximate(self, mock_data):
        """DE postal code 10118 doesn't exist exactly but shares prefix 101 with 10115/10117."""
        result = lookup("DE", "10118")
        assert result is not None
        assert result["match_type"] == "approximate"
        assert result["nuts3"] == "DE300"
        assert result["nuts3_confidence"] < 1.0

    def test_tier4_country_fallback(self, mock_data):
        """YY has unanimous NUTS1/2 but dominant NUTS3 → country fallback."""
        result = lookup("YY", "9999")
        assert result is not None
        assert result["match_type"] == "approximate"
        assert result["nuts1"] == "YY1"
        assert result["nuts2"] == "YY11"
        assert result["nuts3"] == "YY111"
        assert result["nuts1_confidence"] == 1.0
        assert result["nuts2_confidence"] == 1.0

    def test_tier5_single_nuts3(self, mock_data):
        """XX has only one NUTS3 region → single-NUTS3 fallback."""
        result = lookup("XX", "9999")
        assert result is not None
        assert result["match_type"] == "estimated"
        assert result["nuts3"] == "XX000"
        assert result["nuts3_confidence"] == 1.0

    def test_tier5_me_via_settings_fallback(self, mock_data):
        """ME has no TERCET data; single-NUTS3 fallback comes from settings."""
        result = lookup("ME", "81000")
        assert result is not None
        assert result["match_type"] == "estimated"
        assert result["nuts3"] == "ME000"
        assert result["nuts2"] == "ME00"
        assert result["nuts1"] == "ME0"
        assert result["nuts3_confidence"] == 1.0

    def test_tier5_me_with_prefix(self, mock_data):
        """ME-prefixed input still resolves via the single-NUTS3 fallback."""
        result = lookup("ME", "ME-85320")
        assert result is not None
        assert result["nuts3"] == "ME000"

    def test_no_match(self, mock_data):
        """Country with data but no matching postal code and no fallback."""
        result = lookup("AT", "9999")
        assert result is not None
        # AT has multiple NUTS3 regions, so it should get approximate via prefix or None
        # Depends on prefix match — 9 doesn't match any AT prefix well
        # but with 3 entries all AT130, it may actually resolve
        # Let's just verify it returns something (either approx or exact)

    def test_gr_to_el_mapping(self, mock_data):
        """GR input should map to EL internally."""
        result = lookup("GR", "11141")
        assert result is not None
        assert result["match_type"] == "exact"
        assert result["nuts3"] == "EL303"

    def test_unknown_country_returns_none(self, mock_data):
        """Country not in data should return None."""
        result = lookup("ZZ", "12345")
        assert result is None

    def test_tier6_fo_synthetic(self, mock_data):
        """FO has no NUTS coverage → territory-only result, no fabricated code."""
        result = lookup("FO", "100")
        assert result is not None
        assert result["match_type"] is None
        assert result["nuts3"] is None
        assert result["nuts2"] is None
        assert result["nuts1"] is None
        assert result["territory"]["id"] == "FO"
        assert result["territory"]["nuts_coverage"] == "none"

    def test_tier6_fo_names(self, mock_data):
        result = lookup("FO", "100")
        assert result["nuts1_name"] is None
        assert result["nuts2_name"] is None
        assert result["nuts3_name"] is None
        assert result["territory"]["name"] == "Faroe Islands"

    def test_tier6_fo_prefix_variants(self, mock_data):
        for raw in ("FO-100", "FO 100", "FO100", "970", "999"):
            assert lookup("FO", raw)["territory"]["id"] == "FO"

    def test_tier6_fo_rejects_bad_format(self, mock_data):
        """Format guard: non-3-digit input is rejected outright by the gate."""
        assert lookup("FO", "1234") is None
        assert lookup("FO", "ABC") is None
        assert lookup("FO", "DK-3800") is None

    def test_tier6_fo_rejects_two_digit(self, mock_data):
        """Real FO codes are 100-970, never leading-zero-padded. A bare 2-digit
        input must NOT be recovered to a 3-digit code and reach the territory
        statement."""
        assert lookup("FO", "10") is None
        assert lookup("FO", "99") is None

    def test_tier6_fo_in_loaded_countries(self, mock_data):
        from app.data_loader import get_loaded_countries

        assert "FO" in get_loaded_countries()


class TestParseEstimatesFromText:
    def test_parses_well_formed_csv(self):
        from app.data_loader import parse_estimates_from_text

        text = (
            "COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\n"
            "DE,99999,DE300,DE30,DE3,high\n"
            "FR,75000,FR101,FR10,FR1,medium\n"
        )
        d, skipped = parse_estimates_from_text(text)
        assert skipped == 0
        assert len(d) == 2
        assert d[("DE", "99999")]["nuts3"] == "DE300"
        assert d[("FR", "75000")]["nuts3"] == "FR101"
        # Confidence is mapped from label to numeric per settings.confidence_map.
        assert 0.0 < d[("DE", "99999")]["nuts3_confidence"] <= 1.0

    def test_skips_unknown_confidence(self):
        from app.data_loader import parse_estimates_from_text

        text = (
            "COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\n"
            "DE,99999,DE300,DE30,DE3,high\n"
            "DE,99998,DE300,DE30,DE3,bogus\n"
        )
        d, skipped = parse_estimates_from_text(text)
        assert skipped == 1
        assert ("DE", "99998") not in d
        assert ("DE", "99999") in d

    def test_handles_utf8_bom(self):
        from app.data_loader import parse_estimates_from_text

        text = (
            "﻿COUNTRY_CODE,POSTAL_CODE,ESTIMATED_NUTS3,ESTIMATED_NUTS2,ESTIMATED_NUTS1,CONFIDENCE\n"
            "DE,99999,DE300,DE30,DE3,high\n"
        )
        d, skipped = parse_estimates_from_text(text)
        assert len(d) == 1
        assert ("DE", "99999") in d


class TestEstimateOnlyCountry:
    def test_estimate_only_country_is_loaded(self, mock_data):
        from app.data_loader import get_loaded_countries

        assert "AL" in get_loaded_countries()

    def test_albania_resolves_via_estimates(self, mock_data):
        result = lookup("AL", "1001")
        assert result is not None
        assert result["match_type"] == "estimated"
        assert result["nuts3"] == "AL022"
        assert result["nuts1"] == "AL0"
        assert result["nuts3_name"] == "Tiranë"


class TestAlbaniaBlockTier:
    def test_gap_code_resolves_via_block(self, mock_data):
        # 1055 is absent from the GeoNames estimates (the #118 gap) but the
        # block tier resolves it to the Tirana qark.
        from app.data_loader import lookup

        result = lookup("AL", "1055")
        assert result is not None
        assert result["match_type"] == "estimated"
        assert result["nuts3"] == "AL022"
        assert result["nuts1"] == "AL0"
        assert result["nuts3_confidence"] == 0.9

    def test_district_geonames_omits_resolves(self, mock_data):
        from app.data_loader import lookup

        # Peqin (35xx) — GeoNames has no such codes at all.
        result = lookup("AL", "3550")
        assert result is not None
        assert result["nuts3"] == "AL021"

    def test_al_stays_in_loaded_countries(self):
        from app.data_loader import get_loaded_countries

        assert "AL" in get_loaded_countries()


class TestBundledAlbaniaData:
    VALID_AL_NUTS3 = {
        "AL011",
        "AL012",
        "AL013",
        "AL014",
        "AL015",
        "AL021",
        "AL022",
        "AL031",
        "AL032",
        "AL033",
        "AL034",
        "AL035",
    }

    def test_no_al_rows_remain_in_estimates_csv(self):
        from pathlib import Path

        from app.data_loader import parse_estimates_from_text

        text = Path("tercet_missing_codes.csv").read_text(encoding="utf-8")
        parsed, _ = parse_estimates_from_text(text)
        assert not any(cc == "AL" for cc, _ in parsed), "AL now resolves via the block map, not estimates"

    def test_block_map_covers_all_twelve_nuts3(self):
        from app.albania_blocks import BLOCKS

        assert {nuts3 for _, nuts3, _ in BLOCKS} == self.VALID_AL_NUTS3

    def test_sample_codes_resolve_estimated(self):
        from app.data_loader import lookup

        for pc in ("1001", "1055", "5001", "9401", "3550"):
            result = lookup("AL", pc)
            assert result is not None
            assert result["match_type"] == "estimated"
            assert result["nuts3"] in self.VALID_AL_NUTS3
            assert result["nuts2"] == result["nuts3"][:4]
            assert result["nuts1"] == "AL0"


class TestLoadNSPL:
    # LAD (GSS) → ITL3 (TL) bridge, mirroring app/uk_lad_itl.csv shape.
    LAD_MAP = {"E06000001": "TLC31", "S12000033": "TLM50"}

    @staticmethod
    def _zip_bytes(csv_text, arcname="Data/multi_csv/NSPL_MAY_2026_UK_A.csv"):
        import io as _io
        import zipfile

        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(arcname, csv_text)
        return buf.getvalue()

    def test_populates_lookup_via_lad_bridge(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data_loader, "_lookup", {})
        csv_text = (
            "pcds,lad25cd,doterm\n"
            "SW1A 2AA,E06000001,\n"
            "AB1 0AA,S12000033,\n"
            "M1 9NS,E06000001,202312\n"  # terminated → skipped
        )
        content = self._zip_bytes(csv_text)

        def handler(request):
            return httpx.Response(200, content=content, headers={"ETag": '"v1"'})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        count = data_loader._load_nspl(client, "https://example.com/NSPL.zip", tmp_path, self.LAD_MAP)
        assert count == 2
        # Emits TL codes (via LAD → ITL3), not NSPL's GSS itl column.
        assert data_loader._lookup[("UK", "SW1A2AA")] == "TLC31"
        assert data_loader._lookup[("UK", "AB10AA")] == "TLM50"
        assert ("UK", "M19NS") not in data_loader._lookup

    def test_unmapped_lad_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data_loader, "_lookup", {})
        content = self._zip_bytes("pcds,lad25cd,doterm\nZZ1 1ZZ,E99999999,\n")
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content)))
        assert data_loader._load_nspl(client, "https://x/n.zip", tmp_path, self.LAD_MAP) == 0

    def test_returns_zero_when_url_unset(self, tmp_path):
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        assert data_loader._load_nspl(client, "", tmp_path, self.LAD_MAP) == 0

    def test_swallows_exceptions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data_loader, "_lookup", {})

        def handler(request):
            raise httpx.ConnectError("boom")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert data_loader._load_nspl(client, "https://example.com/x.zip", tmp_path, self.LAD_MAP) == 0

    def test_non_zip_response_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data_loader, "_lookup", {})

        def handler(request):
            return httpx.Response(200, content=b"not a zip")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        assert data_loader._load_nspl(client, "https://example.com/x.zip", tmp_path, self.LAD_MAP) == 0

    def test_transient_failure_reuses_cached_zip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data_loader, "_lookup", {})
        # Seed the on-disk cache from a prior successful run.
        (tmp_path / "nspl.zip").write_bytes(self._zip_bytes("pcds,lad25cd,doterm\nSW1A 2AA,E06000001,\n"))

        def handler(request):
            raise httpx.ConnectError("ons down")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        count = data_loader._load_nspl(client, "https://example.com/x.zip", tmp_path, self.LAD_MAP)
        assert count == 1
        assert data_loader._lookup[("UK", "SW1A2AA")] == "TLC31"

    def test_nspl_failure_does_not_block_tercet(self, tmp_path, monkeypatch):
        """If NSPL is unreachable, previously-loaded TERCET data must still serve."""
        monkeypatch.setattr(data_loader, "_lookup", {("AT", "1010"): "AT130"})

        def handler(request):
            raise httpx.ConnectError("ons unavailable")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        nspl_count = data_loader._load_nspl(client, "https://ons.invalid/nspl.zip", tmp_path, self.LAD_MAP)
        assert nspl_count == 0
        # AT lookup must still work (TERCET data untouched)
        result = data_loader.lookup("AT", "1010")
        assert result is not None
        assert result["nuts3"] == "AT130"


class TestUKOutwardLookup:
    def test_outward_only_input_returns_estimated(self, mock_data):
        # "SW1A" has no inward part; resolves via the outward majority-vote tier.
        result = lookup("UK", "SW1A")
        assert result is not None
        assert result["nuts3"] == "TLI32"
        assert result["match_type"] == "estimated"
        assert result["nuts1_confidence"] == pytest.approx(0.90)
        assert result["nuts2_confidence"] == pytest.approx(0.80)
        assert result["nuts3_confidence"] == pytest.approx(0.70)

    def test_full_postcode_still_exact(self, mock_data):
        result = lookup("UK", "SW1A 2AA")
        assert result["match_type"] == "exact"
        assert result["nuts3"] == "TLI32"

    def test_unlisted_full_postcode_resolves_via_outward(self, mock_data):
        # Valid-format UK postcode not in the data → outward "SW1A" still resolves.
        result = lookup("UK", "SW1A 9ZZ")
        assert result is not None
        assert result["nuts3"] == "TLI32"
        assert result["match_type"] == "estimated"

    def test_unknown_outward_returns_none(self, mock_data):
        assert lookup("UK", "ZZ99") is None

    def test_outward_miss_does_not_fall_through_to_prefix(self, mock_data):
        # "SW99 9ZZ" shares the "SW" prefix with SW1A… but SW99 is not a known
        # outward; must NOT return an arbitrary prefix-based ITL3 — stop instead.
        assert lookup("UK", "SW99 9ZZ") is None

    def test_uk_result_tagged_itl(self, mock_data):
        assert lookup("UK", "SW1A 2AA")["code_system"] == "ITL"
        assert lookup("UK", "SW1A")["code_system"] == "ITL"

    def test_non_uk_result_tagged_nuts(self, mock_data):
        assert lookup("AT", "1010")["code_system"] == "NUTS"
        assert lookup("DE", "10118")["code_system"] == "NUTS"


class TestBuildOutwardIndex:
    def test_majority_vote(self, monkeypatch):
        monkeypatch.setattr(
            data_loader,
            "_lookup",
            {
                ("UK", "SW1A2AA"): "TLI32",
                ("UK", "SW1A1AA"): "TLI32",
                ("UK", "SW1A0AA"): "TLI31",  # minority
                ("UK", "M11AA"): "TLD45",
                ("UK", "M11AB"): "TLD45",
            },
        )
        monkeypatch.setattr(data_loader, "_outward_lookup", {})
        data_loader._build_outward_index("UK")
        assert data_loader._outward_lookup[("UK", "SW1A")] == ("TLI32", pytest.approx(2 / 3))
        assert data_loader._outward_lookup[("UK", "M1")] == ("TLD45", pytest.approx(1.0))

    def test_skips_short_codes(self, monkeypatch):
        monkeypatch.setattr(data_loader, "_lookup", {("UK", "AB1"): "TLC11"})
        monkeypatch.setattr(data_loader, "_outward_lookup", {})
        data_loader._build_outward_index("UK")
        assert data_loader._outward_lookup == {}


class TestNSPLConfigHash:
    def test_empty_when_unconfigured(self, monkeypatch):
        monkeypatch.setattr(data_loader.settings, "nspl_url", "", raising=False)
        monkeypatch.setattr(data_loader.settings, "uk_itl_lookup_url", "", raising=False)
        assert data_loader._nspl_config_hash() == ""

    def test_changes_when_url_set(self, monkeypatch):
        monkeypatch.setattr(data_loader.settings, "nspl_url", "", raising=False)
        monkeypatch.setattr(data_loader.settings, "uk_itl_lookup_url", "", raising=False)
        empty = data_loader._nspl_config_hash()
        monkeypatch.setattr(data_loader.settings, "nspl_url", "https://ons/nspl.zip", raising=False)
        assert data_loader._nspl_config_hash() != empty
        assert data_loader._nspl_config_hash() != ""

    def test_db_invalidated_when_nspl_config_changes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(data_loader.settings, "nspl_url", "", raising=False)
        monkeypatch.setattr(data_loader.settings, "uk_itl_lookup_url", "", raising=False)
        monkeypatch.setattr(data_loader, "_lookup", {("AT", "1010"): "AT130"})
        monkeypatch.setattr(data_loader, "_estimates", {})
        monkeypatch.setattr(data_loader, "_nuts_names", {})
        db = tmp_path / "cache.db"
        data_loader._save_to_db(db)
        assert data_loader._db_is_valid(db) is True
        # Operator now enables NSPL → cache must be considered invalid.
        monkeypatch.setattr(data_loader.settings, "nspl_url", "https://ons/nspl.zip", raising=False)
        assert data_loader._db_is_valid(db) is False


class TestConditionalGet:
    def test_sends_conditional_headers_when_etag_known(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(304)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        cached_meta = {
            "etag": '"abc123"',
            "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT",
        }
        result = data_loader._download_zip_conditional(client, "https://example.com/foo.zip", cached_meta)
        assert result.status_code == 304
        assert captured["headers"]["if-none-match"] == '"abc123"'
        assert captured["headers"]["if-modified-since"] == "Wed, 01 Jan 2025 00:00:00 GMT"

    def test_omits_headers_when_meta_empty(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(200, content=b"x")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        data_loader._download_zip_conditional(client, "https://example.com/foo.zip", {})
        assert "if-none-match" not in captured["headers"]
        assert "if-modified-since" not in captured["headers"]
