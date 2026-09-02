"""Proves the rules live in YAML, not in code: editing config flips outcomes."""

import copy

from src.rules import evaluate, load_lending_box
from src.schema import Outcome
from tests.fixtures import DEAL_001, DEAL_004


def test_config_loads_with_version():
    box = load_lending_box()
    assert box["version"]
    assert set(box["criteria"]) >= {"asset_type", "ltv", "dscr", "occupancy"}


def test_raising_ltv_max_flips_needs_review_to_advance():
    box = load_lending_box()
    assert evaluate(DEAL_004, box).trace.outcome == Outcome.NEEDS_REVIEW  # LTV 0.82 > 0.80

    loosened = copy.deepcopy(box)
    loosened["criteria"]["ltv"]["max"] = 0.85
    assert evaluate(DEAL_004, loosened).trace.outcome == Outcome.ADVANCE


def test_tightening_dscr_flips_advance_to_needs_review():
    box = load_lending_box()
    assert evaluate(DEAL_001, box).trace.outcome == Outcome.ADVANCE  # DSCR 1.333

    tightened = copy.deepcopy(box)
    tightened["criteria"]["dscr"]["min"] = 1.40
    assert evaluate(DEAL_001, tightened).trace.outcome == Outcome.NEEDS_REVIEW


def test_reclassifying_enforcement_changes_routing():
    """Flip loan_size from graduated to hard: an out-of-range request should
    then decline instead of routing to review."""
    box = load_lending_box()
    small = DEAL_001.model_copy(deep=True)
    small.loan.requested_amount.value = 3_000_000  # below 5M minimum
    assert evaluate(small, box).trace.outcome == Outcome.NEEDS_REVIEW

    reclassified = copy.deepcopy(box)
    reclassified["criteria"]["loan_size"]["enforcement"] = "hard"
    result = evaluate(small, reclassified)
    assert result.trace.outcome == Outcome.DECLINE
    loan_size = next(r for r in result.trace.results if r.criterion == "loan_size")
    assert loan_size.enforcement == "hard"
