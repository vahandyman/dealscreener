"""Queue ordering. Pure Python; weights live in config/ranking.yaml.

Tiers are fixed: advance -> needs_review -> decline. Within a tier each deal
gets component scores in [0, 1], combined by configured weights. This is a
latency product: the ordering question is "which deal is most likely to
become a loan, and what's the cheapest path to finding out."
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel

from src.schema import CriterionStatus, Outcome, ScreenResult

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

TIER_ORDER = {Outcome.ADVANCE: 0, Outcome.NEEDS_REVIEW: 1, Outcome.DECLINE: 2}


class QueueEntry(BaseModel):
    deal_id: str
    outcome: Outcome
    rank: int | None = None
    score: float
    rank_reason: str
    components: dict[str, float]


def load_ranking_config(path: Path | str | None = None) -> dict:
    with open(path or CONFIG_DIR / "ranking.yaml") as f:
        return yaml.safe_load(f)


def _distance_score(result: ScreenResult) -> tuple[float, str | None]:
    """Closer to passing on the criteria that ARE evaluable ranks higher."""
    fails = [
        r for r in result.trace.results
        if r.status == CriterionStatus.FAIL and r.margin is not None
    ]
    if not fails:
        return 1.0, None
    # margin is relative slack, negative outside the box (e.g. -0.025 = 2.5% over)
    avg_miss = sum(r.margin for r in fails) / len(fails)
    score = max(0.0, min(1.0, 1.0 + avg_miss))
    detail = ", ".join(
        f"{r.criterion} {'near-line' if r.near_line else 'outside'} ({r.margin:+.1%})"
        for r in fails
    )
    return score, detail


def _resolvability_score(result: ScreenResult, cfg: dict) -> tuple[float, str | None]:
    """One missing document beats a structural gap."""
    gaps = [r for r in result.trace.results if r.status == CriterionStatus.NOT_EVALUABLE]
    if not gaps:
        return 1.0, None
    doc_map = cfg.get("resolving_documents", {})
    docs, structural = set(), []
    for g in gaps:
        doc = doc_map.get(g.criterion)
        (docs.add(doc) if doc else structural.append(g.criterion))
    if structural:
        return 0.2, f"structural gap(s): {', '.join(structural)}"
    score = 1.0 / (1 + len(docs))
    return score, f"{len(docs)} doc(s) away: {', '.join(sorted(docs))}"


def _discrepancy_score(result: ScreenResult) -> tuple[float, str | None]:
    """The story not holding together pushes a deal down."""
    if not result.discrepancies:
        return 1.0, None
    penalty = sum(1.0 if d.severity == "major" else 0.4 for d in result.discrepancies)
    majors = sum(1 for d in result.discrepancies if d.severity == "major")
    minors = len(result.discrepancies) - majors
    parts = [p for p, n in (("%d major" % majors, majors), ("%d minor" % minors, minors)) if n]
    return 1.0 / (1 + penalty), f"discrepancies: {', '.join(parts)}"


def _age_score(received_at: str | None, cfg: dict, now: datetime | None = None) -> tuple[float, int]:
    if not received_at:
        return 0.0, 0
    now = now or datetime.now(timezone.utc)
    received = datetime.fromisoformat(received_at)
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    days = max(0, (now - received).days)
    return min(1.0, days / cfg.get("age_horizon_days", 14)), days


def _loan_size_score(result: ScreenResult, cfg: dict) -> float:
    amount = result.deal.loan.requested_amount.value
    if not isinstance(amount, (int, float)):
        return 0.0
    return min(1.0, float(amount) / cfg.get("loan_size_norm", 75_000_000))


def score_deal(
    deal_id: str,
    result: ScreenResult,
    received_at: str | None,
    cfg: dict,
    now: datetime | None = None,
) -> QueueEntry:
    w = cfg["weights"]
    distance, d_detail = _distance_score(result)
    resolvability, r_detail = _resolvability_score(result, cfg)
    discrepancy, disc_detail = _discrepancy_score(result)
    age, days = _age_score(received_at, cfg, now)
    loan_size = _loan_size_score(result, cfg)

    components = {
        "distance_from_box": distance,
        "resolvability": resolvability,
        "discrepancy": discrepancy,
        "time_in_queue": age,
        "loan_size": loan_size,
    }
    score = sum(w.get(k, 0.0) * v for k, v in components.items())

    outcome = result.trace.outcome
    if outcome == Outcome.ADVANCE:
        parts = ["all criteria pass on supported facts"]
    elif outcome == Outcome.DECLINE:
        parts = list(result.trace.outcome_reasons[:1])
    else:
        parts = [p for p in (d_detail, r_detail, disc_detail) if p]
        if result.trace.correlated_flags:
            parts.append("correlated misses flagged")
        if not parts:
            parts = ["needs review"]
    if days:
        parts.append(f"{days}d in queue")

    return QueueEntry(
        deal_id=deal_id,
        outcome=outcome,
        score=round(score, 4),
        rank_reason="; ".join(parts),
        components={k: round(v, 4) for k, v in components.items()},
    )


def rank_queue(
    entries: list[tuple[str, ScreenResult, str | None]],
    cfg: dict | None = None,
    now: datetime | None = None,
) -> list[QueueEntry]:
    """entries: (deal_id, latest ScreenResult, received_at ISO string)."""
    cfg = cfg or load_ranking_config()
    scored = [score_deal(d, r, ts, cfg, now) for d, r, ts in entries]
    scored.sort(key=lambda e: (TIER_ORDER[e.outcome], -e.score, e.deal_id))
    for i, e in enumerate(scored, start=1):
        e.rank = i
    return scored
