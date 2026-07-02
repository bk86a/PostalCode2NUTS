"""Tests for app/resolver.py cascade (pure, no HTTP)."""

from app.resolver import resolve

STRONG = {
    "match_type": "exact",
    "nuts1": "BE2",
    "nuts1_name": "Vl",
    "nuts2": "BE24",
    "nuts2_name": "VlB",
    "nuts3": "BE241",
    "nuts3_name": "Halle",
    "nuts3_confidence": 1.0,
}
WEAK = {**STRONG, "match_type": "approximate", "nuts3_confidence": 0.5}


class FakePip:
    def __init__(self, res):
        self._res = res

    def lookup(self, lat, lon):
        return self._res


def _names(code):
    return f"name:{code}"


def _run(
    current,
    street="Rue",
    city="X",
    pip_res=None,
    geocode=lambda s, c, p: (50.0, 4.0),
    geocode_fn_none=False,
):
    return resolve(
        "BE",
        "3080",
        street,
        city,
        lookup_fn=lambda c, p: current,
        geocode_fn=None if geocode_fn_none else geocode,
        pip=FakePip(pip_res),
        name_fn=_names,
    )


def test_strong_postal_no_geocode():
    r = _run(STRONG)
    assert r["resolved_via"] == "postal" and r["geocode"]["status"] == "not_attempted"
    assert r["nuts3"] == "BE241"


def test_weak_no_address():
    r = _run(WEAK, street=None, city=None)
    assert r["geocode"]["status"] == "no_address" and r["resolved_via"] == "postal"
    assert r["nuts3"] == "BE241"  # best-effort postal still returned


def test_weak_geocoder_unavailable():
    r = _run(WEAK, geocode_fn_none=True)
    assert r["geocode"]["status"] == "geocoder_unavailable" and r["resolved_via"] == "postal"


def test_weak_geocode_no_result():
    r = _run(WEAK, geocode=lambda s, c, p: None)
    assert r["geocode"]["status"] == "no_result" and r["resolved_via"] == "postal"


def test_weak_pip_outside():
    r = _run(WEAK, pip_res=None)  # geocode ok, pip miss
    assert r["geocode"]["status"] == "pip_outside"
    assert r["geocode"]["lat"] == 50.0 and r["resolved_via"] == "postal"


def test_weak_geocode_ok():
    r = _run(WEAK, pip_res={"nuts0": "DE", "nuts1": "DE1", "nuts2": "DE11", "nuts3": "DE111"})
    assert r["resolved_via"] == "geocode" and r["geocode"]["status"] == "ok"
    assert r["nuts3"] == "DE111" and r["nuts3_name"] == "name:DE111"
    assert r["nuts3_confidence"] is None and r["geocode"]["nuts3"] == "DE111"


def test_not_found_no_address_is_none():
    r = _run(None, street=None, city=None)
    assert r["resolved_via"] == "none" and r["match_type"] == "not_found"
    assert r["nuts3"] is None and r["geocode"]["status"] == "no_address"
