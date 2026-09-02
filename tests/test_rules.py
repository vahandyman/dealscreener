import pytest

from src.rules import evaluate, load_lending_box
from src.schema import CriterionStatus, ExtractedDeal, Field, Outcome, UseMixField
from tests.fixtures import ALL_DEALS, DEAL_001, DEAL_002, DEAL_003, DEAL_004, DEAL_005


@pytest.mark.parametrize("deal_id", list(ALL_DEALS))
def test_hand_labeled_outcomes(deal_id):
    deal, expected = ALL_DEALS[deal_id]
    result = evaluate(deal)
    assert result.trace.outcome.value == expected, (
        f"{deal_id}: expected {expected}, got {result.trace.outcome.value}; "
        f"reasons: {result.trace.outcome_reasons}"
    )


def test_hard_decline_stops_evaluation():
    result = evaluate(DEAL_002)
    assert result.trace.outcome == Outcome.DECLINE
    assert result.derived is None  # no LTV was computed on a hotel
    by_name = {r.criterion: r for r in result.trace.results}
    assert by_name["asset_type"].status == CriterionStatus.FAIL
    for name in ("ltv", "dscr", "occupancy", "sponsor_liquidity"):
        assert by_name[name].status == CriterionStatus.SKIPPED


def test_missing_input_routes_to_needs_review_not_decline():
    result = evaluate(DEAL_003)
    assert result.trace.outcome == Outcome.NEEDS_REVIEW
    by_name = {r.criterion: r for r in result.trace.results}
    assert by_name["ltv"].status == CriterionStatus.NOT_EVALUABLE
    assert by_name["ltv"].value is None


def test_graduated_fail_routes_to_needs_review():
    result = evaluate(DEAL_004)
    assert result.trace.outcome == Outcome.NEEDS_REVIEW
    ltv = next(r for r in result.trace.results if r.criterion == "ltv")
    assert ltv.status == CriterionStatus.FAIL
    assert ltv.near_line  # 0.82 vs 0.80 max, tolerance 0.03
    assert ltv.value == pytest.approx(0.82)


def test_correlated_graduated_misses_are_flagged():
    result = evaluate(DEAL_005)
    assert result.trace.outcome == Outcome.NEEDS_REVIEW
    assert len(result.trace.correlated_flags) == 1
    flag = result.trace.correlated_flags[0]
    for name in ("ltv", "dscr", "sponsor_liquidity"):
        assert name in flag


def test_single_graduated_miss_is_not_flagged_as_correlated():
    result = evaluate(DEAL_004)
    assert result.trace.correlated_flags == []


def test_trace_carries_values_thresholds_and_inputs():
    result = evaluate(DEAL_001)
    assert result.trace.outcome == Outcome.ADVANCE
    ltv = next(r for r in result.trace.results if r.criterion == "ltv")
    assert ltv.value == pytest.approx(0.75)
    assert ltv.threshold == 0.80
    assert "valuation.appraised_value" in ltv.inputs_used


def test_unclassified_enforcement_is_flagged_in_output():
    result = evaluate(DEAL_001)
    loan_size = next(r for r in result.trace.results if r.criterion == "loan_size")
    assert loan_size.enforcement_note  # source doc didn't classify; config decision


def test_empty_deal_is_needs_review_not_decline():
    result = evaluate(ExtractedDeal())
    assert result.trace.outcome == Outcome.NEEDS_REVIEW


def test_excluded_use_in_mix_declines_even_when_labeled_multifamily():
    deal = DEAL_001.model_copy(deep=True)
    deal.property.use_mix = UseMixField.of(
        [{"use": "multifamily", "pct": 60}, {"use": "senior_living", "pct": 40}]
    )
    result = evaluate(deal)
    assert result.trace.outcome == Outcome.DECLINE


def test_student_housing_over_cap_declines():
    deal = DEAL_001.model_copy(deep=True)
    deal.property.use_mix = UseMixField.of(
        [{"use": "multifamily", "pct": 40}, {"use": "student_housing", "pct": 60}]
    )
    result = evaluate(deal)
    assert result.trace.outcome == Outcome.DECLINE


def test_student_housing_unknown_share_needs_review():
    deal = DEAL_001.model_copy(deep=True)
    deal.property.use_mix = UseMixField.of(
        [{"use": "multifamily"}, {"use": "student_housing"}]
    )
    result = evaluate(deal)
    assert result.trace.outcome == Outcome.NEEDS_REVIEW


def test_market_outside_top_75_declines():
    deal = DEAL_001.model_copy(deep=True)
    deal.property.msa = Field.of("Casper, WY")
    result = evaluate(deal)
    assert result.trace.outcome == Outcome.DECLINE


def test_non_numeric_extracted_value_never_crashes_numeric_criteria():
    # Extraction can legitimately return a string where a number was
    # ambiguous ("140 keys + 12 units"). That's not-evaluable, not a crash.
    deal = DEAL_001.model_copy(deep=True)
    deal.property.unit_count = Field.of("140 keys + 12 residential units")
    result = evaluate(deal)
    assert result.trace.outcome == Outcome.NEEDS_REVIEW
    asset = next(r for r in result.trace.results if r.criterion == "asset_type")
    assert asset.status == CriterionStatus.NOT_EVALUABLE
    assert "not numeric" in asset.note
