"""YAML-configured rule engine. Pure Python — the LLM never reads the rules.

Evaluation order:
1. HARD criteria on extracted facts. First failure on a supported fact ->
   decline, stop. Nothing downstream runs — no LTV gets computed on a hotel.
2. Derive metrics (code, not LLM).
3. Criteria with a null required input -> not_evaluable -> needs_review.
4. GRADUATED failures -> needs_review. Judgment lives on those lines.
5. Everything passes on supported facts -> advance.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from src.derive import derive_metrics, find_discrepancies
from src.schema import (
    CriterionResult,
    CriterionStatus,
    DerivedMetric,
    ExtractedDeal,
    Outcome,
    RuleTrace,
    ScreenResult,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

HARD_ORDER = ["asset_type", "property_exclusions", "market"]
GRADUATED_ORDER = ["loan_size", "ltv", "dscr", "occupancy", "sponsor_net_worth", "sponsor_liquidity"]

# Graduated criteria that miss in a correlated direction: each group names a
# credit story the box can't see because it tests misses one at a time.
CORRELATION_GROUPS = {
    "stretched sponsor / thin cushion": ["ltv", "dscr", "sponsor_liquidity", "sponsor_net_worth"],
}


def load_lending_box(path: Path | str | None = None) -> dict[str, Any]:
    with open(path or CONFIG_DIR / "lending_box.yaml") as f:
        return yaml.safe_load(f)


def load_msa_list(path: Path | str | None = None) -> list[str]:
    with open(path or CONFIG_DIR / "msa_top75.yaml") as f:
        return yaml.safe_load(f)["msas"]


def _val(field) -> Any:
    return field.value if field is not None else None


def _numval(field) -> float | None:
    # Numeric value or None: a non-numeric extraction is treated as
    # not-evaluable by numeric criteria, never compared or coerced blindly.
    v = _val(field)
    return float(v) if isinstance(v, (int, float)) else None


def _skipped(name: str, enforcement: str) -> CriterionResult:
    return CriterionResult(
        criterion=name,
        enforcement=enforcement,
        status=CriterionStatus.SKIPPED,
        note="not evaluated: a hard criterion already declined this deal",
    )


# ---------------------------------------------------------------------------
# Hard criteria — evaluated on extracted facts only
# ---------------------------------------------------------------------------

def eval_asset_type(deal: ExtractedDeal, cfg: dict) -> CriterionResult:
    inputs = ["property.asset_type", "property.unit_count", "property.stabilization_status"]
    asset = _val(deal.property.asset_type)
    units = _numval(deal.property.unit_count)
    units_raw = _val(deal.property.unit_count)
    status = _val(deal.property.stabilization_status)
    tested = {"asset_type": asset, "unit_count": units, "stabilization_status": status}
    threshold = {
        "allowed": cfg["allowed"],
        "min_units": cfg["min_units"],
        "required_status": cfg["required_status"],
    }

    fails = []
    if asset is not None and str(asset).lower() not in cfg["allowed"]:
        fails.append(f"asset_type '{asset}' not in allowed {cfg['allowed']}")
    if units is not None and units < cfg["min_units"]:
        fails.append(f"{units} units < minimum {cfg['min_units']}")
    if status is not None and str(status).lower() not in cfg["required_status"]:
        fails.append(f"stabilization_status '{status}' not in {cfg['required_status']}")
    if fails:  # a supported fact fails: hard fail wins over any missing input
        return CriterionResult(
            criterion="asset_type", enforcement="hard", status=CriterionStatus.FAIL,
            value=tested, threshold=threshold, inputs_used=inputs, note="; ".join(fails),
        )

    missing = [n for n, v in (("asset_type", asset), ("unit_count", units), ("stabilization_status", status)) if v is None]
    if missing:
        note = f"missing required input(s): {', '.join(missing)}"
        if units is None and units_raw is not None:
            note += f"; unit_count present but not numeric: {units_raw!r}"
        return CriterionResult(
            criterion="asset_type", enforcement="hard", status=CriterionStatus.NOT_EVALUABLE,
            value=tested, threshold=threshold, inputs_used=inputs, note=note,
        )
    return CriterionResult(
        criterion="asset_type", enforcement="hard", status=CriterionStatus.PASS,
        value=tested, threshold=threshold, inputs_used=inputs,
    )


def eval_property_exclusions(deal: ExtractedDeal, cfg: dict) -> CriterionResult:
    inputs = ["property.use_mix", "property.asset_type"]
    use_mix = deal.property.use_mix.value
    asset = _val(deal.property.asset_type)
    excluded = set(cfg["excluded"])
    threshold = {"excluded": cfg["excluded"], "student_housing_max_pct": cfg["student_housing_max_pct"]}

    uses: list[dict] = []
    if use_mix:
        uses = [u if isinstance(u, dict) else u.model_dump() for u in use_mix]
    tested = {"use_mix": uses, "asset_type": asset}

    fails = []
    for u in uses:
        name = str(u.get("use", "")).lower()
        if name in excluded:
            fails.append(f"use '{name}' is excluded")
        if name == "student_housing":
            pct = u.get("pct")
            if pct is None:
                return CriterionResult(
                    criterion="property_exclusions", enforcement="hard",
                    status=CriterionStatus.NOT_EVALUABLE, value=tested, threshold=threshold,
                    inputs_used=inputs,
                    note="student_housing present but share not stated; cannot test 50% cap",
                )
            if pct > cfg["student_housing_max_pct"]:
                fails.append(f"student_housing at {pct}% exceeds {cfg['student_housing_max_pct']}% cap")
    if asset is not None and str(asset).lower() in excluded:
        fails.append(f"asset_type '{asset}' is excluded")

    if fails:
        return CriterionResult(
            criterion="property_exclusions", enforcement="hard", status=CriterionStatus.FAIL,
            value=tested, threshold=threshold, inputs_used=inputs, note="; ".join(fails),
        )
    if not uses and asset is None:
        return CriterionResult(
            criterion="property_exclusions", enforcement="hard",
            status=CriterionStatus.NOT_EVALUABLE, value=tested, threshold=threshold,
            inputs_used=inputs, note="neither use_mix nor asset_type available",
        )
    return CriterionResult(
        criterion="property_exclusions", enforcement="hard", status=CriterionStatus.PASS,
        value=tested, threshold=threshold, inputs_used=inputs,
    )


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def _msa_parts(name: str) -> tuple[set[str], set[str]]:
    """Split 'Dallas-Fort Worth-Arlington, TX' -> (city names, state codes)."""
    if "," in name:
        cities_part, states_part = name.rsplit(",", 1)
        states = {s.strip().upper() for s in re.split(r"[-/]", states_part) if s.strip()}
    else:
        cities_part, states = name, set()
    cities = set()
    for c in re.split(r"[-/]", cities_part):
        c = re.sub(r"\b(msa|metro area|metropolitan area|metro)\b", "", _norm(c)).strip()
        if c:
            cities.add(c)
    return cities, states


def eval_market(deal: ExtractedDeal, cfg: dict, msa_list: list[str]) -> CriterionResult:
    inputs = ["property.msa"]
    msa = _val(deal.property.msa)
    threshold = cfg["requirement"]
    if msa is None:
        return CriterionResult(
            criterion="market", enforcement="hard", status=CriterionStatus.NOT_EVALUABLE,
            value=None, threshold=threshold, inputs_used=inputs,
            note=f"msa missing: {deal.property.msa.missing_reason}",
        )
    deal_cities, deal_states = _msa_parts(str(msa))
    for entry in msa_list:
        cities, states = _msa_parts(entry)
        if deal_cities & cities and (not deal_states or not states or deal_states & states):
            return CriterionResult(
                criterion="market", enforcement="hard", status=CriterionStatus.PASS,
                value=msa, threshold=threshold, inputs_used=inputs,
                note=f"matched top-75 MSA: {entry}",
            )
    return CriterionResult(
        criterion="market", enforcement="hard", status=CriterionStatus.FAIL,
        value=msa, threshold=threshold, inputs_used=inputs,
        note=f"'{msa}' not matched to any top-75 MSA",
    )


# ---------------------------------------------------------------------------
# Graduated criteria — evaluated on derived metrics / extracted facts
# ---------------------------------------------------------------------------

def _metric_result(
    name: str, cfg: dict, metric: DerivedMetric, *,
    direction: str, limit: float, tolerance: float, note: str | None = None,
) -> CriterionResult:
    """Shared shape for threshold tests on a derived metric.

    direction 'max': value must be <= limit. 'min': value must be >= limit.
    margin is relative slack (positive inside the box).
    """
    base = dict(
        criterion=name, enforcement=cfg["enforcement"], threshold=limit,
        inputs_used=metric.inputs_used, enforcement_note=cfg.get("enforcement_note"),
    )
    if not metric.computable:
        return CriterionResult(
            **base, status=CriterionStatus.NOT_EVALUABLE, value=None,
            note=metric.reason,
        )
    v = metric.value
    if direction == "max":
        ok, overshoot, margin = v <= limit, v - limit, (limit - v) / limit
    else:
        ok, overshoot, margin = v >= limit, limit - v, (v - limit) / limit
    near = (not ok) and overshoot <= tolerance
    notes = [n for n in [note] if n]
    if near:
        notes.append(f"outside the line but within near-line tolerance ({tolerance})")
    return CriterionResult(
        **base,
        status=CriterionStatus.PASS if ok else CriterionStatus.FAIL,
        value=round(v, 4), margin=round(margin, 4), near_line=near,
        note="; ".join(notes) or None,
    )


def eval_loan_size(deal: ExtractedDeal, cfg: dict) -> CriterionResult:
    inputs = ["loan.requested_amount"]
    amount = _numval(deal.loan.requested_amount)
    threshold = {"min": cfg["min"], "max": cfg["max"]}
    base = dict(
        criterion="loan_size", enforcement=cfg["enforcement"], threshold=threshold,
        inputs_used=inputs, enforcement_note=cfg.get("enforcement_note"),
    )
    if amount is None:
        return CriterionResult(
            **base, status=CriterionStatus.NOT_EVALUABLE,
            note=f"requested_amount missing: {deal.loan.requested_amount.missing_reason}",
        )
    ok = cfg["min"] <= amount <= cfg["max"]
    # relative distance to the nearer violated bound (positive inside)
    margin = min(amount - cfg["min"], cfg["max"] - amount) / cfg["max"]
    note = None
    if not ok:
        side = "below minimum" if amount < cfg["min"] else "above maximum"
        note = f"{amount:,.0f} is {side}"
    return CriterionResult(
        **base, status=CriterionStatus.PASS if ok else CriterionStatus.FAIL,
        value=amount, margin=round(margin, 4), note=note,
    )


def eval_sponsor_net_worth(deal: ExtractedDeal, cfg: dict) -> CriterionResult:
    inputs = ["sponsor.net_worth", "loan.requested_amount"]
    nw = _numval(deal.sponsor.net_worth)
    amount = _numval(deal.loan.requested_amount)
    base = dict(
        criterion="sponsor_net_worth", enforcement=cfg["enforcement"],
        threshold=cfg["rule"], inputs_used=inputs, enforcement_note=cfg.get("enforcement_note"),
    )
    missing = [n for n, v in (("sponsor.net_worth", nw), ("loan.requested_amount", amount)) if v is None]
    if missing:
        return CriterionResult(
            **base, status=CriterionStatus.NOT_EVALUABLE,
            note=f"missing required input(s): {', '.join(missing)}",
        )
    ok = nw >= amount
    return CriterionResult(
        **base, status=CriterionStatus.PASS if ok else CriterionStatus.FAIL,
        value=nw, margin=round((nw - amount) / amount, 4),
        note=None if ok else f"net worth {nw:,.0f} < loan amount {amount:,.0f}",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def evaluate(
    deal: ExtractedDeal,
    box: dict[str, Any] | None = None,
    msa_list: list[str] | None = None,
) -> ScreenResult:
    box = box or load_lending_box()
    msa_list = msa_list or load_msa_list()
    criteria = box["criteria"]
    results: list[CriterionResult] = []

    # --- Stage 1: hard gate on extracted facts. First fail stops everything.
    hard_evaluators = {
        "asset_type": lambda: eval_asset_type(deal, criteria["asset_type"]),
        "property_exclusions": lambda: eval_property_exclusions(deal, criteria["property_exclusions"]),
        "market": lambda: eval_market(deal, criteria["market"], msa_list),
    }
    for i, name in enumerate(HARD_ORDER):
        res = hard_evaluators[name]()
        results.append(res)
        if res.status == CriterionStatus.FAIL:
            for later in HARD_ORDER[i + 1:]:
                results.append(_skipped(later, criteria[later]["enforcement"]))
            for g in GRADUATED_ORDER:
                results.append(_skipped(g, criteria[g]["enforcement"]))
            trace = RuleTrace(
                box_version=box["version"], results=results, outcome=Outcome.DECLINE,
                outcome_reasons=[f"hard criterion '{name}' failed: {res.note}"],
            )
            return ScreenResult(deal=deal, derived=None, discrepancies=[], trace=trace)

    # --- Stage 2: derive metrics (only after the hard gate clears)
    derived = derive_metrics(deal)
    discrepancies = find_discrepancies(deal, derived)

    # --- Stage 3: graduated criteria
    occ_cfg = criteria["occupancy"]
    occ_note = (
        f"tested on physical occupancy; paying-only = "
        f"{derived.occupancy_paying_only.value:.1%}"
        if derived.occupancy_variants_diverge and derived.occupancy_paying_only.computable
        else None
    )
    occ_res = _metric_result(
        "occupancy", occ_cfg, derived.occupancy_physical,
        direction="min", limit=occ_cfg["min"], tolerance=occ_cfg["near_line_tolerance"],
        note=occ_note,
    )
    if occ_res.status == CriterionStatus.PASS:
        occ_res.note = "; ".join(
            n for n in [
                occ_res.note,
                f"{occ_cfg['sustained_days']}-day sustained requirement not verifiable from a single rent roll snapshot",
            ] if n
        )

    results.extend([
        eval_loan_size(deal, criteria["loan_size"]),
        _metric_result("ltv", criteria["ltv"], derived.ltv,
                       direction="max", limit=criteria["ltv"]["max"],
                       tolerance=criteria["ltv"]["near_line_tolerance"]),
        _metric_result("dscr", criteria["dscr"], derived.dscr,
                       direction="min", limit=criteria["dscr"]["min"],
                       tolerance=criteria["dscr"]["near_line_tolerance"]),
        occ_res,
        eval_sponsor_net_worth(deal, criteria["sponsor_net_worth"]),
        _metric_result("sponsor_liquidity", criteria["sponsor_liquidity"], derived.liquidity_months,
                       direction="min", limit=criteria["sponsor_liquidity"]["min_months_pi"],
                       tolerance=criteria["sponsor_liquidity"]["near_line_tolerance_months"]),
    ])

    # --- Stage 4: aggregate. Routing follows the enforcement FLAG, so
    # reclassifying a criterion in config changes routing with no code change.
    not_evaluable = [r for r in results if r.status == CriterionStatus.NOT_EVALUABLE]
    fails = [r for r in results if r.status == CriterionStatus.FAIL]
    hard_fails = [r for r in fails if r.enforcement == "hard"]
    grad_fails = [r for r in fails if r.enforcement == "graduated"]

    if hard_fails:
        trace = RuleTrace(
            box_version=box["version"], results=results, outcome=Outcome.DECLINE,
            outcome_reasons=[f"hard criterion '{r.criterion}' failed: {r.note}" for r in hard_fails],
        )
        return ScreenResult(deal=deal, derived=derived, discrepancies=discrepancies, trace=trace)

    reasons: list[str] = []
    reasons += [f"'{r.criterion}' not evaluable: {r.note}" for r in not_evaluable]
    reasons += [f"graduated criterion '{r.criterion}' outside the box: {r.note or r.value}" for r in grad_fails]

    # --- Correlated graduated misses: the box tests one line at a time and
    # has nothing to say about several small misses telling one story.
    # Surface it; don't resolve it.
    correlated: list[str] = []
    failed_names = {r.criterion for r in grad_fails}
    for story, members in CORRELATION_GROUPS.items():
        hit = [m for m in members if m in failed_names]
        if len(hit) >= 2:
            correlated.append(
                f"{story}: {', '.join(hit)} all miss in the same direction; "
                "the box evaluates misses one at a time — needs analyst judgment"
            )

    outcome = Outcome.NEEDS_REVIEW if reasons else Outcome.ADVANCE
    if outcome == Outcome.ADVANCE:
        reasons = ["all criteria pass on supported facts"]

    trace = RuleTrace(
        box_version=box["version"], results=results, outcome=outcome,
        outcome_reasons=reasons, correlated_flags=correlated,
    )
    return ScreenResult(deal=deal, derived=derived, discrepancies=discrepancies, trace=trace)
