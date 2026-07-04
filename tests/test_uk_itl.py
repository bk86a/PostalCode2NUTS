"""Tests for app.uk_itl — LAD→ITL3 bridge parsing and bundled lookup."""

from app import uk_itl


SAMPLE_CSV = (
    "LAD25CD,ITL325CD,ITL325NM,ITL225CD,ITL225NM,ITL125CD,ITL125NM\n"
    "E06000001,TLC31,Hartlepool and Stockton-on-Tees,TLC3,Tees Valley,TLC,North East (England)\n"
    "E06000002,TLC32,South Teesside,TLC3,Tees Valley,TLC,North East (England)\n"
    "S12000033,TLM50,Aberdeen City and Aberdeenshire,TLM5,North Eastern Scotland,TLM,Scotland\n"
)


class TestParseLadItl:
    def test_lad_to_itl3_mapping(self):
        lad_to_itl3, _ = uk_itl.parse_lad_itl(SAMPLE_CSV)
        assert lad_to_itl3["E06000001"] == "TLC31"
        assert lad_to_itl3["S12000033"] == "TLM50"

    def test_itl_names_all_levels(self):
        _, names = uk_itl.parse_lad_itl(SAMPLE_CSV)
        assert names["TLC31"] == "Hartlepool and Stockton-on-Tees"
        assert names["TLC3"] == "Tees Valley"
        assert names["TLC"] == "North East (England)"
        assert names["TLM50"] == "Aberdeen City and Aberdeenshire"

    def test_codes_uppercased(self):
        lad_to_itl3, _ = uk_itl.parse_lad_itl(
            "LAD25CD,ITL325CD,ITL325NM,ITL225CD,ITL225NM,ITL125CD,ITL125NM\n"
            "e06000001,tlc31,Name,tlc3,N2,tlc,N1\n"
        )
        assert lad_to_itl3["E06000001"] == "TLC31"

    def test_vintage_suffix_variation(self):
        # 2021 vintage columns must parse the same way.
        lad_to_itl3, names = uk_itl.parse_lad_itl(
            "LAD21CD,ITL321CD,ITL321NM,ITL221CD,ITL221NM,ITL121CD,ITL121NM\n"
            "E06000001,TLC31,Foo,TLC3,Bar,TLC,Baz\n"
        )
        assert lad_to_itl3["E06000001"] == "TLC31"
        assert names["TLC"] == "Baz"

    def test_missing_columns_returns_empty(self):
        lad_to_itl3, names = uk_itl.parse_lad_itl("foo,bar\n1,2\n")
        assert lad_to_itl3 == {}
        assert names == {}


class TestBundled:
    def test_bundled_loads_and_is_complete(self):
        lad_to_itl3, names = uk_itl.load_bundled()
        # ~361 LADs, all ITL3 codes are TL-prefixed
        assert len(lad_to_itl3) > 300
        assert all(v.startswith("TL") for v in lad_to_itl3.values())
        # names cover all three levels present in the map
        assert all(itl3 in names for itl3 in lad_to_itl3.values())

    def test_bundled_truncation_holds(self):
        # ITL2 = ITL3[:4], ITL1 = ITL3[:3] must both be named, so _build_result's
        # truncation-based nuts2/nuts1 derivation resolves names correctly.
        _, names = uk_itl.load_bundled()
        lad_to_itl3, _ = uk_itl.load_bundled()
        for itl3 in lad_to_itl3.values():
            assert itl3[:4] in names
            assert itl3[:3] in names
