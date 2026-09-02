import pytest
from pydantic import ValidationError

from src.schema import DerivedMetric, ExtractedDeal, Field, ListField


def test_missing_value_requires_reason():
    with pytest.raises(ValidationError):
        Field(value=None)
    with pytest.raises(ValidationError):
        ListField(value=None)


def test_missing_with_reason_is_valid():
    f = Field.missing("appraisal not yet delivered")
    assert f.value is None and f.missing_reason


def test_extracted_deal_has_no_derived_metric_fields():
    """The extraction schema must not be able to carry LTV or DSCR."""
    names = set()
    deal = ExtractedDeal()
    for group_name, group in deal:
        if isinstance(group, list):
            continue
        names |= {f"{group_name}.{n}" for n in type(group).model_fields}
    assert not any(n.endswith((".ltv", ".dscr")) for n in names)


def test_absent_fields_default_to_explicit_missing():
    deal = ExtractedDeal()
    assert deal.valuation.appraised_value.value is None
    assert deal.valuation.appraised_value.missing_reason == "not extracted"


def test_derived_metric_consistency():
    with pytest.raises(ValidationError):
        DerivedMetric(name="ltv", computable=False)  # no reason
    with pytest.raises(ValidationError):
        DerivedMetric(name="ltv", computable=True, value=None)
