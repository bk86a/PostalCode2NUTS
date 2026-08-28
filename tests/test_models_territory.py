"""NUTSResult carries an optional territory block and nullable NUTS fields."""

import pytest
from pydantic import ValidationError

from app.models import NUTSResult, ContextInfo


def test_territory_only_result_validates_with_null_nuts():
    r = NUTSResult(
        postal_code="98800",
        country_code="NC",
        match_type=None,
        nuts1=None, nuts1_confidence=None,
        nuts2=None, nuts2_confidence=None,
        nuts3=None, nuts3_confidence=None,
        context=ContextInfo(
            id="NC", iso="NC", name="New Caledonia", status="oct",
            administering_country="FR", legal_basis="TFEU Part Four, Annex II",
            note=None, nuts_coverage="none",
        ),
    )
    assert r.nuts3 is None
    assert r.match_type is None
    assert r.context.status == "oct"
    assert r.context.nuts_coverage == "none"


def test_ordinary_result_omits_the_territory_block():
    r = NUTSResult(
        postal_code="10115", country_code="DE", match_type="exact",
        nuts1="DE3", nuts1_confidence=1.0,
        nuts2="DE30", nuts2_confidence=1.0,
        nuts3="DE300", nuts3_confidence=1.0,
    )
    assert r.context is None


def test_nuts_coverage_is_constrained():
    with pytest.raises(ValidationError):
        ContextInfo(
            id="NC", iso="NC", name="New Caledonia", status="oct",
            administering_country="FR", legal_basis=None, note=None,
            nuts_coverage="partial",
        )


def test_status_is_constrained():
    with pytest.raises(ValidationError):
        ContextInfo(
            id="NC", iso="NC", name="New Caledonia", status="colony",
            administering_country="FR", legal_basis=None, note=None,
            nuts_coverage="none",
        )
