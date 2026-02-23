"""Tests for postal_patterns.py — preprocessing, tercet_map, extraction."""

from app.postal_patterns import _apply_tercet_map, _preprocess, extract_postal_code


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
