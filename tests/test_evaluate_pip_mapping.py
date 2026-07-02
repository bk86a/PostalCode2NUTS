"""Tests for scripts/evaluate_pip_mapping.py (evaluation core)."""

import importlib.util
import sys
from pathlib import Path

from shapely.geometry import box

REPO = Path(__file__).resolve().parent.parent
# make sibling scripts importable for the module under test
sys.path.insert(0, str(REPO / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "evaluate_pip_mapping",
    REPO / "scripts" / "evaluate_pip_mapping.py",
)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

from nuts_pip import NutsPip  # noqa: E402

ORACLE = NutsPip([("AB100", box(0.0, 0.0, 1.0, 1.0))])  # lon/lat 0..1
COORDS = {
    ("AB", "100"): (0.5, 0.5),  # inside AB100
    ("AB", "200"): (0.5, 0.5),  # inside AB100 too (will disagree)
    ("AB", "999"): (0.5, 0.5),  # inside, but current lookup fails → rescue
    ("AB", "777"): (50.0, 50.0),  # coord outside all regions
    # AB 555 intentionally absent → no_coord
}


def _fake_lookup(country, postcode):
    table = {
        ("AB", "100"): {"nuts3": "AB100", "match_type": "exact"},
        ("AB", "200"): {"nuts3": "AB200", "match_type": "exact"},
        ("AB", "777"): {"nuts3": "AB100", "match_type": "exact"},
        ("AB", "555"): {"nuts3": "AB100", "match_type": "exact"},
    }
    return table.get((country, postcode))  # AB/999 → None


def test_classify_buckets():
    assert ev.classify(None, None, None) == "no_coord"
    assert ev.classify(None, (0.5, 0.5), None) == "pip_outside"
    assert ev.classify(None, (0.5, 0.5), {"nuts3": "AB100"}) == "rescue"
    cur = {"nuts3": "AB100"}
    assert ev.classify(cur, (0.5, 0.5), {"nuts3": "AB100"}) == "agree"
    assert ev.classify(cur, (0.5, 0.5), {"nuts3": "AB200"}) == "disagree"


def test_evaluate_produces_expected_buckets():
    rows = [("AB", "100"), ("AB", "200"), ("AB", "999"), ("AB", "777"), ("AB", "555")]
    records = ev.evaluate(rows, COORDS, ORACLE, _fake_lookup)
    buckets = {(r["country"], r["postcode"]): r["bucket"] for r in records}
    assert buckets[("AB", "100")] == "agree"
    assert buckets[("AB", "200")] == "disagree"
    assert buckets[("AB", "999")] == "rescue"
    assert buckets[("AB", "777")] == "pip_outside"
    assert buckets[("AB", "555")] == "no_coord"


def test_summarize_crosstabs_by_tier():
    rows = [("AB", "100"), ("AB", "200"), ("AB", "999")]
    records = ev.evaluate(rows, COORDS, ORACLE, _fake_lookup)
    summary = ev.summarize(records)
    assert summary["overall"]["agree"] == 1
    assert summary["overall"]["disagree"] == 1
    assert summary["overall"]["rescue"] == 1
    # AB/100 and AB/200 are 'exact' tier; AB/999 has no current result
    assert summary["by_tier"]["exact"]["agree"] == 1
    assert summary["by_tier"]["exact"]["disagree"] == 1
    assert summary["by_tier"]["not_found"]["rescue"] == 1


def test_format_report_includes_overall_and_tiers():
    summary = {
        "overall": {"agree": 8, "disagree": 2, "rescue": 5, "no_coord": 3},
        "by_tier": {"exact": {"agree": 8, "disagree": 2}, "not_found": {"rescue": 5}},
    }
    report = ev.format_report(summary)
    assert "agree" in report and "8" in report
    assert "exact" in report
    assert "not_found" in report


def test_evaluate_normalizes_query_country_for_coord_lookup():
    # coords keyed under canonical EL; queries may arrive as GR (ISO) or EL.
    oracle = NutsPip([("EL300", box(0.0, 0.0, 1.0, 1.0))])
    coords = {("EL", "10431"): (0.5, 0.5)}

    def norm(c):
        return "EL" if c.strip().upper() == "GR" else c.strip().upper()

    def lk(country, postcode):
        return None  # TERCET miss → bucket becomes 'rescue' when coord resolves

    recs = ev.evaluate([("GR", "10431"), ("EL", "10431")], coords, oracle, lk, normalize_cc=norm)
    buckets = {(r["country"], r["postcode"]): r["bucket"] for r in recs}
    assert buckets[("GR", "10431")] == "rescue"  # GR reconciled to EL coord
    assert buckets[("EL", "10431")] == "rescue"


def _write_csv(path, header, rows):
    import csv as _csv

    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def test_read_query_rows_accepts_query_log_headers(tmp_path):
    p = tmp_path / "q.csv"
    _write_csv(p, ["COUNTRY", "POST_CODE"], [["DE", "10115"], ["fr", " 75001 "]])
    assert list(ev._read_query_rows(str(p))) == [("DE", "10115"), ("fr", "75001")]


def test_read_query_rows_accepts_estimate_data_headers(tmp_path):
    # tercet_missing_codes.csv uses COUNTRY_CODE / POSTAL_CODE.
    p = tmp_path / "d.csv"
    _write_csv(p, ["COUNTRY_CODE", "POSTAL_CODE", "ESTIMATED_NUTS3"], [["AL", "1001", "AL011"]])
    assert list(ev._read_query_rows(str(p))) == [("AL", "1001")]


def test_read_query_rows_missing_column_raises_clear_error(tmp_path):
    import pytest

    p = tmp_path / "bad.csv"
    _write_csv(p, ["NATION", "ZIP"], [["DE", "10115"]])
    with pytest.raises(ValueError, match="missing a country column"):
        list(ev._read_query_rows(str(p)))
