"""Five hand-built deals with hand-labeled expected outcomes.

These are hardcoded ExtractedDeal objects — no LLM involved — so the
derivation and rule logic can be validated before extraction exists.
Replace or extend with the take-home's actual sample packages.
"""

from src.schema import BrokerClaim, ExtractedDeal, Field, ListField, UseMixField


def _f(value, source=None, as_of=None):
    return Field.of(value, source=source, as_of=as_of)


# DEAL-001 — clean pass everywhere -> advance
DEAL_001 = ExtractedDeal(
    identity=dict(
        deal_id=_f("DEAL-001"),
        sponsor_name=_f("Marisol Vega", "broker cover letter"),
        borrowing_entity=_f("Maple Court Partners LLC", "broker cover letter"),
        channel=_f("broker"),
        received_at=_f("2026-08-10"),
    ),
    property=dict(
        asset_type=_f("multifamily", "offering memo p.1"),
        unit_count=_f(120, "offering memo p.2"),
        year_built=_f(2004),
        stabilization_status=_f("stabilized", "offering memo p.2"),
        msa=_f("Dallas-Fort Worth-Arlington, TX", "offering memo p.1"),
        address=_f("410 Maple Ct, Garland, TX"),
        use_mix=UseMixField.of([{"use": "multifamily", "pct": 100}], "offering memo p.2"),
    ),
    loan=dict(
        requested_amount=_f(24_000_000, "loan request summary"),
        annual_debt_service=_f(1_500_000, "loan request summary"),
        quoted_rate=_f(0.0625),
        quoted_term=_f("10Y fixed"),
    ),
    valuation=dict(
        appraised_value=_f(32_000_000, "appraisal report", as_of="2026-07-15"),
        appraisal_type=_f("as-is"),
        appraisal_status=_f("final"),
    ),
    operations=dict(
        noi=_f(2_000_000, "T-12 operating statement", as_of="2026-06-30"),
        effective_gross_income=_f(3_400_000, "T-12 operating statement"),
        gross_potential_rent=_f(3_600_000, "T-12 operating statement"),
        operating_expenses=_f(1_400_000, "T-12 operating statement"),
        trailing_period=_f("T-12"),
    ),
    occupancy=dict(
        physical_units_total=_f(120, "rent roll", as_of="2026-07-31"),
        units_occupied_paying=_f(112, "rent roll"),
        units_vacant=_f(6, "rent roll"),
        units_occupied_nonpaying=_f(2, "rent roll"),
        stated_occupancy=_f(0.95, "broker cover letter"),
        rent_roll_as_of=_f("2026-07-31"),
    ),
    sponsor=dict(
        net_worth=_f(30_000_000, "PFS", as_of="2026-06-30"),
        liquidity=_f(1_800_000, "PFS"),
        asset_count=_f(9),
        credit_events=ListField.of([]),
    ),
    package=dict(
        documents_present=ListField.of(["offering memo", "T-12", "rent roll", "appraisal", "PFS"]),
        documents_missing=ListField.of([]),
        earliest_document_date=_f("2026-06-30"),
    ),
)
# Hand-worked: LTV 24/32 = 0.75 ok; DSCR 2.0/1.5 = 1.333 ok;
# occupancy paying-only 112/120 = 0.9333 ok (incl nonpaying 0.95);
# liquidity 1.8M / 125k = 14.4 months ok; NW 30M >= 24M ok.
EXPECTED_001 = "advance"


# DEAL-002 — extended-stay hotel pitched as multifamily -> hard decline,
# nothing downstream evaluated.
DEAL_002 = ExtractedDeal(
    identity=dict(
        deal_id=_f("DEAL-002"),
        sponsor_name=_f("R. Okafor"),
        borrowing_entity=_f("Harborview Lodging LLC"),
        channel=_f("broker"),
        received_at=_f("2026-08-12"),
    ),
    property=dict(
        asset_type=_f("hospitality", "offering memo: 140-key extended-stay"),
        unit_count=_f(140),
        stabilization_status=_f("stabilized"),
        msa=_f("Tampa-St. Petersburg-Clearwater, FL"),
        use_mix=UseMixField.of([{"use": "hospitality", "pct": 100}]),
    ),
    loan=dict(
        requested_amount=_f(18_000_000),
        annual_debt_service=_f(1_300_000),
    ),
    valuation=dict(appraised_value=_f(26_000_000, "appraisal")),
    operations=dict(noi=_f(1_900_000, "T-12"), trailing_period=_f("T-12")),
    occupancy=dict(
        physical_units_total=_f(140),
        units_occupied_paying=_f(115),
        units_occupied_nonpaying=_f(0),
    ),
    sponsor=dict(net_worth=_f(22_000_000), liquidity=_f(1_500_000)),
)
EXPECTED_002 = "decline"


# DEAL-003 — appraisal not yet delivered -> LTV not evaluable -> needs_review.
# One document away from a clean answer (high resolvability, for ranking later).
DEAL_003 = ExtractedDeal(
    identity=dict(
        deal_id=_f("DEAL-003"),
        sponsor_name=_f("Cedar Ridge Holdings"),
        borrowing_entity=_f("CR Apartments Owner LLC"),
        received_at=_f("2026-08-14"),
    ),
    property=dict(
        asset_type=_f("multifamily"),
        unit_count=_f(88),
        stabilization_status=_f("stabilized"),
        msa=_f("Charlotte-Concord-Gastonia, NC-SC"),
        use_mix=UseMixField.of([{"use": "multifamily", "pct": 100}]),
    ),
    loan=dict(
        requested_amount=_f(14_500_000),
        annual_debt_service=_f(980_000),
    ),
    valuation=dict(
        appraised_value=Field.missing("appraisal ordered, not yet delivered"),
        appraisal_status=_f("ordered"),
    ),
    operations=dict(noi=_f(1_310_000, "T-12"), trailing_period=_f("T-12")),
    occupancy=dict(
        physical_units_total=_f(88),
        units_occupied_paying=_f(82),
        units_occupied_nonpaying=_f(0),
        units_vacant=_f(6),
        stated_occupancy=_f(0.93),
    ),
    sponsor=dict(net_worth=_f(19_000_000), liquidity=_f(1_100_000)),
    package=dict(documents_missing=ListField.of(["appraisal"])),
)
# DSCR 1.31/0.98 = 1.337 ok; occupancy 82/88 = 0.9318 ok;
# liquidity 1.1M / 81.7k = 13.5 months ok; NW ok. Only LTV blocks.
EXPECTED_003 = "needs_review"


# DEAL-004 — everything passes except LTV at 0.82: outside the line but
# within the 0.03 near-line tolerance -> needs_review, near_line flagged.
DEAL_004 = ExtractedDeal(
    identity=dict(
        deal_id=_f("DEAL-004"),
        sponsor_name=_f("Stonebrook Capital"),
        borrowing_entity=_f("Stonebrook Flats LLC"),
        received_at=_f("2026-08-16"),
    ),
    property=dict(
        asset_type=_f("multifamily"),
        unit_count=_f(210),
        stabilization_status=_f("stabilized"),
        msa=_f("Phoenix-Mesa-Chandler, AZ"),
        use_mix=UseMixField.of([{"use": "multifamily", "pct": 100}]),
    ),
    loan=dict(
        requested_amount=_f(41_000_000),
        annual_debt_service=_f(2_700_000),
    ),
    valuation=dict(appraised_value=_f(50_000_000, "appraisal")),
    operations=dict(noi=_f(3_600_000, "T-12"), trailing_period=_f("T-12")),
    occupancy=dict(
        physical_units_total=_f(210),
        units_occupied_paying=_f(195),
        units_occupied_nonpaying=_f(3),
        stated_occupancy=_f(0.94),
    ),
    sponsor=dict(net_worth=_f(55_000_000), liquidity=_f(2_400_000)),
    broker_claims=[BrokerClaim(metric="ltv", value=0.78, source="broker cover: 'conservative 78% leverage'")],
)
# LTV 41/50 = 0.82 > 0.80 (within 0.03 tolerance); DSCR 1.333 ok;
# occupancy 195/210 = 0.9286 ok; liquidity 2.4M/225k = 10.67 ok.
# Broker claims 0.78 vs derived 0.82 -> major discrepancy (delta 0.04).
EXPECTED_004 = "needs_review"


# DEAL-005 — three graduated misses in the same direction: LTV 0.83,
# DSCR 1.18, liquidity ~5.1 months -> needs_review + correlated flag.
DEAL_005 = ExtractedDeal(
    identity=dict(
        deal_id=_f("DEAL-005"),
        sponsor_name=_f("Pinnacle Partners"),
        borrowing_entity=_f("Pinnacle Lofts Owner LLC"),
        received_at=_f("2026-08-18"),
    ),
    property=dict(
        asset_type=_f("multifamily"),
        unit_count=_f(150),
        stabilization_status=_f("stabilized"),
        msa=_f("Denver-Aurora-Lakewood, CO"),
        use_mix=UseMixField.of([{"use": "multifamily", "pct": 100}]),
    ),
    loan=dict(
        requested_amount=_f(29_050_000),
        annual_debt_service=_f(2_000_000),
    ),
    valuation=dict(appraised_value=_f(35_000_000, "appraisal")),
    operations=dict(noi=_f(2_360_000, "T-12"), trailing_period=_f("T-12")),
    occupancy=dict(
        physical_units_total=_f(150),
        units_occupied_paying=_f(138),
        units_occupied_nonpaying=_f(4),
        stated_occupancy=_f(0.95),
    ),
    sponsor=dict(net_worth=_f(31_000_000), liquidity=_f(850_000)),
)
# LTV 29.05/35 = 0.83 fail; DSCR 2.36/2.0 = 1.18 fail;
# liquidity 850k/166.7k = 5.1 months fail; occupancy 138/150 = 0.92 ok.
EXPECTED_005 = "needs_review"


ALL_DEALS = {
    "DEAL-001": (DEAL_001, EXPECTED_001),
    "DEAL-002": (DEAL_002, EXPECTED_002),
    "DEAL-003": (DEAL_003, EXPECTED_003),
    "DEAL-004": (DEAL_004, EXPECTED_004),
    "DEAL-005": (DEAL_005, EXPECTED_005),
}
