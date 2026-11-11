"""Tests for postal_patterns.py — preprocessing, tercet_map, extraction."""

import pytest

from app.postal_patterns import (
    _COMPILED,
    PATTERNS_META,
    POSTAL_PATTERNS,
    _apply_tercet_map,
    _preprocess,
    extract_outward,
    extract_postal_code,
)


# ── _preprocess tests ─────────────────────────────────────────────────────────


class TestPreprocess:
    def test_strip_excel_float_suffix(self):
        assert _preprocess("28040.0", None) == "28040"

    def test_strip_excel_float_double_zero(self):
        assert _preprocess("28040.00", None) == "28040"

    def test_remove_dot_thousands(self):
        assert _preprocess("13.600", None) == "13600"

    def test_thousands_before_float_strip(self):
        """13.000 should become 13000 (not 13 if .0 stripped first)."""
        assert _preprocess("13.000", None) == "13000"

    def test_leading_zero_restore(self):
        entry = {"expected_digits": 5}
        assert _preprocess("8461", entry) == "08461"

    def test_leading_zero_no_pad_when_correct_length(self):
        entry = {"expected_digits": 5}
        assert _preprocess("28040", entry) == "28040"

    def test_leading_zero_no_pad_when_not_one_short(self):
        entry = {"expected_digits": 5}
        assert _preprocess("846", entry) == "846"

    def test_no_pad_without_expected_digits(self):
        entry = {}
        assert _preprocess("8461", entry) == "8461"

    def test_passthrough_clean_input(self):
        assert _preprocess("10115", None) == "10115"


# ── _apply_tercet_map tests ──────────────────────────────────────────────────


class TestApplyTercetMap:
    def test_truncate(self):
        assert _apply_tercet_map("D02X285", "truncate:3") == "D02"

    def test_prepend(self):
        assert _apply_tercet_map("1010", "prepend:LV") == "LV1010"

    def test_keep_alpha(self):
        assert _apply_tercet_map("VLT1010", "keep_alpha") == "VLT"

    def test_keep_alpha_no_match(self):
        assert _apply_tercet_map("1234", "keep_alpha") == "1234"

    def test_unknown_action_passthrough(self):
        assert _apply_tercet_map("ABC", "unknown:x") == "ABC"


# ── extract_postal_code tests ────────────────────────────────────────────────


class TestExtractPostalCode:
    def test_de_basic(self):
        assert extract_postal_code("DE", "10115") == "10115"

    def test_de_with_prefix(self):
        assert extract_postal_code("DE", "D-10115") == "10115"

    def test_de_with_country_prefix(self):
        assert extract_postal_code("DE", "DE-10115") == "10115"

    def test_at_basic(self):
        assert extract_postal_code("AT", "1010") == "1010"

    def test_at_with_prefix(self):
        assert extract_postal_code("AT", "A-1010") == "1010"

    def test_pl_with_dash(self):
        assert extract_postal_code("PL", "00-950") == "00950"

    def test_pl_without_dash(self):
        assert extract_postal_code("PL", "00950") == "00950"

    def test_ie_truncates_to_routing_key(self):
        assert extract_postal_code("IE", "D02 X285") == "D02"

    def test_ie_no_space(self):
        assert extract_postal_code("IE", "D02X285") == "D02"

    def test_lv_prepends_country_code(self):
        assert extract_postal_code("LV", "1010") == "LV1010"

    def test_lv_with_prefix(self):
        assert extract_postal_code("LV", "LV-1010") == "LV1010"

    def test_mt_keep_alpha(self):
        assert extract_postal_code("MT", "VLT 1010") == "VLT"

    def test_mt_no_space(self):
        assert extract_postal_code("MT", "MST1000") == "MST"

    def test_nl_basic(self):
        assert extract_postal_code("NL", "1012 AB") == "1012AB"

    def test_cz_with_space(self):
        assert extract_postal_code("CZ", "110 00") == "11000"

    def test_se_with_prefix(self):
        assert extract_postal_code("SE", "SE-10005") == "10005"

    def test_excel_float_recovery(self):
        """Excel float '28040.0' for DE should extract correctly."""
        assert extract_postal_code("DE", "28040.0") == "28040"

    def test_excel_thousands_recovery(self):
        """Dot-thousands '13.600' for DE should extract correctly."""
        assert extract_postal_code("DE", "13.600") == "13600"

    def test_unknown_country_fallback(self):
        """Unknown country falls back to normalize_postal_code."""
        assert extract_postal_code("ZZ", "AB-123") == "AB123"

    def test_el_greek_pattern(self):
        assert extract_postal_code("EL", "10431") == "10431"

    def test_el_with_gr_prefix(self):
        assert extract_postal_code("EL", "GR-10431") == "10431"

    def test_me_basic(self):
        assert extract_postal_code("ME", "81000") == "81000"

    def test_me_with_prefix(self):
        assert extract_postal_code("ME", "ME-85320") == "85320"

    def test_me_with_space_prefix(self):
        assert extract_postal_code("ME", "ME 85320") == "85320"


class TestFaroeIslands:
    def test_bare_three_digits(self):
        from app.postal_patterns import extract_postal_code

        assert extract_postal_code("FO", "100") == "100"

    def test_prefixed_variants(self):
        from app.postal_patterns import extract_postal_code

        assert extract_postal_code("FO", "FO-100") == "100"
        assert extract_postal_code("FO", "FO 100") == "100"
        assert extract_postal_code("FO", "FO100") == "100"

    def test_rejects_non_three_digit(self):
        # No regex match → falls back to normalize_postal_code(cleaned),
        # which is NOT a clean 3-digit extraction.
        from app.postal_patterns import _COMPILED

        pat = _COMPILED["FO"]
        assert pat.match("1234") is None
        assert pat.match("ABC") is None
        assert pat.match("DK-3800") is None


class TestAlbaniaExtraction:
    def test_bare_four_digits(self):
        assert extract_postal_code("AL", "1001") == "1001"

    def test_with_al_dash_prefix(self):
        assert extract_postal_code("AL", "AL-1001") == "1001"

    def test_with_al_space_prefix(self):
        assert extract_postal_code("AL", "AL 5001") == "5001"

    def test_lowercase_prefix(self):
        assert extract_postal_code("AL", "al-9721") == "9721"

    def test_three_digit_not_matched_as_four(self):
        # Too short: regex requires exactly 4 digits; must NOT become a 4-digit code.
        assert extract_postal_code("AL", "100") != "1000"


class TestUKExtraction:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("SW1A 2AA", "SW1A2AA"),
            ("sw1a 2aa", "SW1A2AA"),
            ("SW1A2AA", "SW1A2AA"),
            ("M1 1AA", "M11AA"),
            ("B33 8TH", "B338TH"),
            ("W1A 1HQ", "W1A1HQ"),
            ("CR2 6XH", "CR26XH"),
            ("DN55 1PT", "DN551PT"),
            ("EC1A 1BB", "EC1A1BB"),
        ],
    )
    def test_uk_regex_extracts_normalized_full_postcode(self, raw, expected):
        assert extract_postal_code("UK", raw) == expected


def test_patterns_meta_version_bumped():
    # Adding the Crown Dependencies and Gibraltar is an additive coverage change;
    # minor version bump.
    # Countries without leading-zero codes reject a leading 0; minor bump.
    # CZ first digit narrowed to the official 1-7 range; minor bump.
    assert PATTERNS_META["version"] == "1.7"


class TestExtractOutward:
    @pytest.mark.parametrize(
        "raw, expected_outward",
        [
            ("SW1A 2AA", "SW1A"),
            ("sw1a2aa", "SW1A"),
            ("M1 1AA", "M1"),
            ("B33 8TH", "B33"),
            ("EC1A 1BB", "EC1A"),
            ("DN55 1PT", "DN55"),
            ("SW1A", "SW1A"),  # outward-only input
            ("M1", "M1"),
        ],
    )
    def test_extract_outward_for_uk(self, raw, expected_outward):
        assert extract_outward("UK", raw) == expected_outward

    def test_returns_none_for_country_without_flag(self):
        # AT does not declare outward_only; outward extraction is undefined.
        assert extract_outward("AT", "1010") is None

    def test_extract_postal_code_unaffected_by_outward_only_flag(self):
        # Tier 1 lookup for UK must still yield the full normalised postcode.
        assert extract_postal_code("UK", "SW1A 2AA") == "SW1A2AA"


# Countries whose national schemes allocate first digits 1-9 only (no TERCET
# NUTS-2024 code starts with 0 either). LU is excluded: its 0xxx codes are
# PackUp parcel lockers. A one-digit-short input must not be padded into a
# code that passes the format check (EACSP-122291: HU "105" became "0105").
NO_LEADING_ZERO = ["AT", "BG", "CH", "CY", "CZ", "HR", "HU", "LI", "PT", "SE", "SI"]


class TestNoLeadingZeroCountries:
    @pytest.mark.parametrize("cc", NO_LEADING_ZERO)
    def test_leading_zero_code_fails_format(self, cc):
        digits = POSTAL_PATTERNS[cc]["expected_digits"]
        assert not _COMPILED[cc].match("0" * digits)

    @pytest.mark.parametrize("cc", NO_LEADING_ZERO)
    def test_one_digit_short_is_not_padded(self, cc):
        digits = POSTAL_PATTERNS[cc]["expected_digits"]
        short = "1" * (digits - 1)
        assert extract_postal_code(cc, short) == short

    @pytest.mark.parametrize("cc", NO_LEADING_ZERO)
    def test_examples_still_match(self, cc):
        for ex in POSTAL_PATTERNS[cc]["example"].split(", "):
            assert _COMPILED[cc].match(ex.upper()), (cc, ex)

    def test_hu_short_code_reported_case(self):
        assert extract_postal_code("HU", "105") == "105"
        assert extract_postal_code("HU", "H-1052") == "1052"

    def test_padding_kept_where_leading_zeros_exist(self):
        assert extract_postal_code("ES", "8461") == "08461"
        assert extract_postal_code("DE", "1067") == "01067"
        assert extract_postal_code("LU", "161") == "0161"


class TestCzechFirstDigit:
    # Czech postcodes (PSČ) start with 1-7; 8xxxx/9xxxx and 0xxxx are Slovak or
    # unallocated ranges, and every CZ code in TERCET NUTS-2024 starts with 1-7.
    @pytest.mark.parametrize("raw", ["11000", "110 00", "79999", "CZ-110 00", "cz 56943"])
    def test_official_range_matches(self, raw):
        assert _COMPILED["CZ"].match(raw.upper())

    @pytest.mark.parametrize("raw", ["80000", "97101", "900 86", "05801", "CZ-97101"])
    def test_outside_range_fails_format(self, raw):
        assert not _COMPILED["CZ"].match(raw.upper())

    def test_official_spaced_form_is_first_example(self):
        assert POSTAL_PATTERNS["CZ"]["example"].split(", ")[0] == "110 00"
