import pytest

from src.derive import derive_metrics, find_discrepancies
from src.schema import ExtractedDeal, Field
from tests.fixtures import DEAL_001, DEAL_003, DEAL_004


def test_known_math_on_deal_001():
    m = derive_metrics(DEAL_001)
    assert m.ltv.computable and m.ltv.value == pytest.approx(0.75)
    assert m.dscr.computable and m.dscr.value == pytest.approx(2_000_000 / 1_500_000)
    assert m.occupancy_paying_only.value == pytest.approx(112 / 120)
    assert m.occupancy_physical.value == pytest.approx(114 / 120)
    assert m.occupancy_variants_diverge  # 2 nonpaying units make the readings differ
    assert m.liquidity_months.value == pytest.approx(14.4)


def test_metrics_name_their_inputs():
    m = derive_metrics(DEAL_001)
    assert "loan.requested_amount" in m.ltv.inputs_used
    assert "valuation.appraised_value" in m.ltv.inputs_used


def test_missing_input_never_produces_a_number():
    m = derive_metrics(ExtractedDeal())  # everything missing
    for metric in (m.ltv, m.dscr, m.occupancy_paying_only,
                   m.occupancy_physical, m.liquidity_months):
        assert not metric.computable
        assert metric.value is None
        assert metric.reason


def test_missing_appraisal_blocks_ltv_only():
    m = derive_metrics(DEAL_003)
    assert not m.ltv.computable
    assert "appraisal" in m.ltv.reason
    assert m.dscr.computable  # other metrics unaffected


def test_noi_never_inferred_from_gpr_minus_opex():
    deal = DEAL_001.model_copy(deep=True)
    deal.operations.noi = Field.missing("no operating statement in package")
    # GPR and OpEx are both present — the tempting arithmetic must not happen
    assert deal.operations.gross_potential_rent.value is not None
    m = derive_metrics(deal)
    assert not m.dscr.computable
    assert "not inferred" in m.dscr.reason


def test_pro_forma_noi_is_not_computable():
    deal = DEAL_001.model_copy(deep=True)
    deal.operations.trailing_period = Field.of("pro forma")
    m = derive_metrics(deal)
    assert not m.dscr.computable


def test_missing_nonpaying_count_blocks_incl_variant_not_paying_only():
    deal = DEAL_001.model_copy(deep=True)
    deal.occupancy.units_occupied_nonpaying = Field.missing("rent roll does not break out delinquents")
    m = derive_metrics(deal)
    assert m.occupancy_paying_only.computable
    assert not m.occupancy_physical.computable  # never assume zero


def test_stated_occupancy_discrepancy_is_surfaced_not_resolved():
    deal = DEAL_001.model_copy(deep=True)
    deal.occupancy.stated_occupancy = Field.of(0.99, "broker cover letter")
    m = derive_metrics(deal)
    disc = find_discrepancies(deal, m)
    assert len(disc) == 1
    d = disc[0]
    assert d.field_name == "stated_occupancy"
    assert d.stated_value == 0.99
    assert d.derived_value == pytest.approx(0.95)
    assert d.severity == "minor"


def test_broker_ltv_claim_checked_against_derived():
    m = derive_metrics(DEAL_004)
    disc = find_discrepancies(DEAL_004, m)
    claims = [d for d in disc if d.field_name == "broker_claim.ltv"]
    assert len(claims) == 1
    assert claims[0].severity == "major"  # claimed 0.78, derived 0.82
