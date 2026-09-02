import copy

from src.rank import load_ranking_config, rank_queue
from src.rules import evaluate
from src.schema import Field, Outcome
from tests.fixtures import DEAL_001, DEAL_002, DEAL_003, DEAL_004, DEAL_005

NOW = "2026-08-25T00:00:00+00:00"


def _entries(extra=None):
    deals = [
        ("DEAL-001", DEAL_001), ("DEAL-002", DEAL_002), ("DEAL-003", DEAL_003),
        ("DEAL-004", DEAL_004), ("DEAL-005", DEAL_005),
    ] + (extra or [])
    return [(deal_id, evaluate(deal), NOW) for deal_id, deal in deals]


def test_tier_order_is_absolute():
    queue = rank_queue(_entries())
    assert queue[0].outcome == Outcome.ADVANCE
    assert queue[-1].outcome == Outcome.DECLINE
    tiers = [{"advance": 0, "needs_review": 1, "decline": 2}[e.outcome.value] for e in queue]
    assert tiers == sorted(tiers)


def test_discrepancy_pushes_down_within_tier():
    # Same deal, same missing appraisal; the only difference is a stated
    # occupancy that contradicts the rent roll. The story not holding
    # together must cost rank — and must NOT change the outcome.
    contradicted = DEAL_003.model_copy(deep=True)
    contradicted.occupancy.stated_occupancy = Field.of(0.99, "broker summary")
    queue = rank_queue(_entries(extra=[("DEAL-003X", contradicted)]))
    by_id = {e.deal_id: e for e in queue}
    assert by_id["DEAL-003X"].outcome == by_id["DEAL-003"].outcome == Outcome.NEEDS_REVIEW
    assert by_id["DEAL-003"].rank < by_id["DEAL-003X"].rank


def test_one_doc_gap_outranks_structural_gap():
    # DEAL-003 is one appraisal away. The same deal with an unidentifiable
    # market has a gap no single document closes — it must score lower.
    structural = DEAL_003.model_copy(deep=True)
    structural.property.msa = Field.missing("market not identifiable from package")
    queue = rank_queue(_entries(extra=[("DEAL-003S", structural)]))
    by_id = {e.deal_id: e for e in queue}
    assert by_id["DEAL-003S"].outcome == Outcome.NEEDS_REVIEW
    assert by_id["DEAL-003"].rank < by_id["DEAL-003S"].rank
    assert by_id["DEAL-003S"].components["resolvability"] < by_id["DEAL-003"].components["resolvability"]


def test_every_entry_has_a_rank_reason():
    for e in rank_queue(_entries()):
        assert e.rank_reason


def test_time_in_queue_prevents_starvation():
    cfg = load_ranking_config()
    fresh = {e.deal_id: e.score for e in rank_queue(_entries(), cfg)}
    aged_entries = [
        (d, r, "2026-08-01T00:00:00+00:00" if d == "DEAL-005" else ts)
        for d, r, ts in _entries()
    ]
    aged = {e.deal_id: e.score for e in rank_queue(aged_entries, cfg)}
    assert aged["DEAL-005"] > fresh["DEAL-005"]


def test_reweighting_changes_order_without_code_change():
    cfg = load_ranking_config()
    needs_review = lambda queue: [e.deal_id for e in queue if e.outcome == Outcome.NEEDS_REVIEW]
    baseline = needs_review(rank_queue(_entries(), cfg))

    by_size_cfg = copy.deepcopy(cfg)
    by_size_cfg["weights"] = {
        "distance_from_box": 0.0, "resolvability": 0.0,
        "discrepancy": 0.0, "time_in_queue": 0.0, "loan_size": 1.0,
    }
    by_size = needs_review(rank_queue(_entries(), by_size_cfg))
    # pure size weighting: $41M, then $29.05M, then $14.5M
    assert by_size == ["DEAL-004", "DEAL-005", "DEAL-003"]
    assert by_size != baseline
