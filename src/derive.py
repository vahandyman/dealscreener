"""Derived metrics. Pure Python — no LLM anywhere near this file.

Every metric either computes from named inputs or returns
computable=False with a reason. Missing input never produces a number.
"""

from __future__ import annotations

from src.schema import (
    DerivedMetric,
    DerivedMetrics,
    Discrepancy,
    ExtractedDeal,
    Field,
)

# Discrepancy tolerances: stated vs derived disagreement past these is surfaced.
OCC_MINOR = 0.02   # 2 points
OCC_MAJOR = 0.05
LTV_MINOR = 0.01
LTV_MAJOR = 0.03
DSCR_MINOR = 0.03
DSCR_MAJOR = 0.10


def _val(field: Field) -> float | int | str | None:
    return field.value if field is not None else None


def _num(field: Field) -> float | None:
    v = _val(field)
    return float(v) if isinstance(v, (int, float)) else None


def _not_computable(name: str, reason: str, inputs: list[str]) -> DerivedMetric:
    return DerivedMetric(name=name, computable=False, reason=reason, inputs_used=inputs)


def derive_ltv(deal: ExtractedDeal) -> DerivedMetric:
    inputs = ["loan.requested_amount", "valuation.appraised_value"]
    amount = _num(deal.loan.requested_amount)
    value = _num(deal.valuation.appraised_value)
    if amount is None:
        return _not_computable("ltv", f"requested_amount missing: {deal.loan.requested_amount.missing_reason}", inputs)
    if value is None:
        return _not_computable("ltv", f"appraised_value missing: {deal.valuation.appraised_value.missing_reason}", inputs)
    if value <= 0:
        return _not_computable("ltv", "appraised_value is not positive", inputs)
    return DerivedMetric(name="ltv", value=amount / value, computable=True, inputs_used=inputs)


def derive_dscr(deal: ExtractedDeal) -> DerivedMetric:
    inputs = ["operations.noi", "operations.trailing_period", "loan.annual_debt_service"]
    noi = _num(deal.operations.noi)
    ads = _num(deal.loan.annual_debt_service)
    if noi is None:
        # Deliberately NOT inferred from GPR - OpEx: with occupied-nonpaying
        # units that arithmetic overstates NOI badly.
        return _not_computable(
            "dscr",
            "NOI not stated in package "
            f"({deal.operations.noi.missing_reason}); not inferred from GPR minus OpEx",
            inputs,
        )
    trailing = _val(deal.operations.trailing_period)
    if trailing is None:
        return _not_computable(
            "dscr",
            "NOI figure present but not tied to a trailing statement period; "
            "cannot distinguish trailing actuals from pro forma",
            inputs,
        )
    if isinstance(trailing, str) and "pro forma" in trailing.lower():
        return _not_computable(
            "dscr", "only pro forma NOI available; box tests trailing actuals", inputs
        )
    if ads is None:
        return _not_computable(
            "dscr", f"annual_debt_service missing: {deal.loan.annual_debt_service.missing_reason}", inputs
        )
    if ads <= 0:
        return _not_computable("dscr", "annual_debt_service is not positive", inputs)
    return DerivedMetric(name="dscr", value=noi / ads, computable=True, inputs_used=inputs)


def derive_occupancy(deal: ExtractedDeal) -> tuple[DerivedMetric, DerivedMetric, bool]:
    """Physical occupancy, both ways: paying-only and physical (all occupied).

    Returns (paying_only, physical, diverge). The box tests physical
    occupancy; nonpaying occupants are physically present but produce no
    income, so the paying-only reading and the divergence are signals.

    Physical prefers the explicit paying/nonpaying split when both halves are
    stated, falls back to a stated total-occupied count, and is otherwise not
    computable — a lone paying count is a floor, not a physical occupancy,
    and a missing nonpaying count is never assumed to be zero.
    """
    occ = deal.occupancy
    total = _num(occ.physical_units_total)
    occupied_total = _num(occ.units_occupied_total)
    paying = _num(occ.units_occupied_paying)
    nonpaying = _num(occ.units_occupied_nonpaying)

    base_inputs = ["occupancy.physical_units_total", "occupancy.units_occupied_paying"]
    if total is None or total <= 0:
        reason = (
            f"physical_units_total missing: {occ.physical_units_total.missing_reason}"
            if total is None else "physical_units_total is not positive"
        )
        m = _not_computable("occupancy_paying_only", reason, base_inputs)
        return m, m.model_copy(update={"name": "occupancy_physical"}), False

    if paying is None:
        paying_only = _not_computable(
            "occupancy_paying_only",
            f"units_occupied_paying missing: {occ.units_occupied_paying.missing_reason}",
            base_inputs,
        )
    else:
        paying_only = DerivedMetric(
            name="occupancy_paying_only", value=paying / total, computable=True, inputs_used=base_inputs
        )

    if paying is not None and nonpaying is not None:
        physical = DerivedMetric(
            name="occupancy_physical",
            value=(paying + nonpaying) / total,
            computable=True,
            inputs_used=["occupancy.physical_units_total", "occupancy.units_occupied_paying",
                         "occupancy.units_occupied_nonpaying"],
        )
    elif occupied_total is not None:
        physical = DerivedMetric(
            name="occupancy_physical",
            value=occupied_total / total,
            computable=True,
            inputs_used=["occupancy.physical_units_total", "occupancy.units_occupied_total"],
        )
    else:
        physical = _not_computable(
            "occupancy_physical",
            "occupied unit count not available: no total-occupied figure and no "
            "complete paying/nonpaying split (a missing nonpaying count is not assumed zero)",
            ["occupancy.physical_units_total", "occupancy.units_occupied_total",
             "occupancy.units_occupied_paying", "occupancy.units_occupied_nonpaying"],
        )

    diverge = (
        paying_only.computable
        and physical.computable
        and abs(physical.value - paying_only.value) > 1e-9
    )
    return paying_only, physical, diverge


def derive_liquidity_months(deal: ExtractedDeal) -> DerivedMetric:
    inputs = ["sponsor.liquidity", "loan.annual_debt_service"]
    liquidity = _num(deal.sponsor.liquidity)
    ads = _num(deal.loan.annual_debt_service)
    if liquidity is None:
        return _not_computable(
            "liquidity_months", f"sponsor liquidity missing: {deal.sponsor.liquidity.missing_reason}", inputs
        )
    if ads is None:
        return _not_computable(
            "liquidity_months",
            f"annual_debt_service missing: {deal.loan.annual_debt_service.missing_reason}",
            inputs,
        )
    if ads <= 0:
        return _not_computable("liquidity_months", "annual_debt_service is not positive", inputs)
    return DerivedMetric(
        name="liquidity_months", value=liquidity / (ads / 12), computable=True, inputs_used=inputs
    )


def derive_metrics(deal: ExtractedDeal) -> DerivedMetrics:
    paying_only, physical, diverge = derive_occupancy(deal)
    return DerivedMetrics(
        ltv=derive_ltv(deal),
        dscr=derive_dscr(deal),
        occupancy_paying_only=paying_only,
        occupancy_physical=physical,
        occupancy_variants_diverge=diverge,
        liquidity_months=derive_liquidity_months(deal),
    )


# ---------------------------------------------------------------------------
# Discrepancies: stated vs derived. Disagreement is an output, never averaged.
# ---------------------------------------------------------------------------

def _severity(delta: float, minor: float, major: float) -> str | None:
    if abs(delta) >= major:
        return "major"
    if abs(delta) >= minor:
        return "minor"
    return None


def find_discrepancies(deal: ExtractedDeal, metrics: DerivedMetrics) -> list[Discrepancy]:
    out: list[Discrepancy] = []

    stated_occ = _num(deal.occupancy.stated_occupancy)
    # Broker-stated occupancy is conventionally physical (heads in beds), so
    # compare against the incl-nonpaying reading; fall back to paying-only.
    derived_occ = (
        metrics.occupancy_physical
        if metrics.occupancy_physical.computable
        else metrics.occupancy_paying_only
    )
    if stated_occ is not None and derived_occ.computable:
        delta = stated_occ - derived_occ.value
        sev = _severity(delta, OCC_MINOR, OCC_MAJOR)
        if sev:
            out.append(
                Discrepancy(
                    field_name="stated_occupancy",
                    stated_value=stated_occ,
                    derived_value=round(derived_occ.value, 4),
                    delta=round(delta, 4),
                    severity=sev,
                    note=f"package states {stated_occ:.0%}; rent roll supports {derived_occ.value:.1%} ({derived_occ.name})",
                )
            )

    claim_specs = {
        "ltv": (metrics.ltv, LTV_MINOR, LTV_MAJOR),
        "dscr": (metrics.dscr, DSCR_MINOR, DSCR_MAJOR),
    }
    for claim in deal.broker_claims:
        metric, minor, major = claim_specs[claim.metric]
        if not metric.computable:
            continue
        delta = claim.value - metric.value
        sev = _severity(delta, minor, major)
        if sev:
            out.append(
                Discrepancy(
                    field_name=f"broker_claim.{claim.metric}",
                    stated_value=claim.value,
                    derived_value=round(metric.value, 4),
                    delta=round(delta, 4),
                    severity=sev,
                    note=f"package characterizes {claim.metric} as {claim.value}; derived from stated inputs: {metric.value:.3f}",
                )
            )
    return out
