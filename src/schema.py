"""Typed models for the deal screening pipeline.

Layering rule enforced by these types:
- ExtractedDeal holds only what the package *states*, with provenance.
  There is deliberately no ltv/dscr field here.
- DerivedMetrics holds only what code *computed*, with the inputs it used.
- Discrepancy holds disagreements between the two. They are never merged.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, model_validator


# ---------------------------------------------------------------------------
# Field wrappers: every extracted value carries provenance or a missing reason
# ---------------------------------------------------------------------------

class Field(BaseModel):
    # Types kept lean deliberately: the JSON-schema grammar for strict
    # structured outputs compounds across ~40 wrapped fields, and the API
    # rejects an oversized grammar. Dates travel as ISO strings.
    value: float | str | None = None
    source: str | None = None          # where in the package this came from
    as_of: str | None = None           # document date (ISO), not extraction date
    missing_reason: str | None = None  # required when value is None

    @model_validator(mode="after")
    def _missing_needs_reason(self) -> "Field":
        if self.value is None and not self.missing_reason:
            raise ValueError("Field with value=None must set missing_reason")
        return self

    @classmethod
    def of(cls, value: Any, source: str | None = None, as_of: str | None = None) -> "Field":
        return cls(value=value, source=source, as_of=as_of)

    @classmethod
    def missing(cls, reason: str = "not stated in package") -> "Field":
        return cls(value=None, missing_reason=reason)


def _missing_field() -> Field:
    return Field.missing("not extracted")


class UseMixEntry(BaseModel):
    use: str                      # e.g. multifamily, retail, student_housing
    pct: float | None = None      # share of units or NRA, if stated


class ListField(BaseModel):
    """Wrapper for string-list extractions (documents, credit events)."""
    value: list[str] | None = None
    source: str | None = None
    as_of: str | None = None
    missing_reason: str | None = None

    @model_validator(mode="after")
    def _missing_needs_reason(self) -> "ListField":
        if self.value is None and not self.missing_reason:
            raise ValueError("ListField with value=None must set missing_reason")
        return self

    @classmethod
    def of(cls, value: list, source: str | None = None) -> "ListField":
        return cls(value=value, source=source)

    @classmethod
    def missing(cls, reason: str = "not stated in package") -> "ListField":
        return cls(value=None, missing_reason=reason)


class UseMixField(BaseModel):
    """Wrapper for the property's use mix — a property can be more than one thing."""
    value: list[UseMixEntry] | None = None
    source: str | None = None
    as_of: str | None = None
    missing_reason: str | None = None

    @model_validator(mode="after")
    def _missing_needs_reason(self) -> "UseMixField":
        if self.value is None and not self.missing_reason:
            raise ValueError("UseMixField with value=None must set missing_reason")
        return self

    @classmethod
    def of(cls, value: list, source: str | None = None) -> "UseMixField":
        return cls(value=value, source=source)

    @classmethod
    def missing(cls, reason: str = "not stated in package") -> "UseMixField":
        return cls(value=None, missing_reason=reason)


def _missing_list() -> ListField:
    return ListField.missing("not extracted")


# ---------------------------------------------------------------------------
# Extracted deal, grouped by what the lending box tests
# ---------------------------------------------------------------------------

class Identity(BaseModel):
    deal_id: Field = None  # assigned by store on first receipt; broker refs go in source
    sponsor_name: Field = None
    borrowing_entity: Field = None  # SPEs mean borrower is new each deal; principal is not
    channel: Field = None
    received_at: Field = None

    @model_validator(mode="before")
    @classmethod
    def _default_missing(cls, data: Any) -> Any:
        return _fill_missing(cls, data)


class PropertyInfo(BaseModel):
    asset_type: Field = None
    unit_count: Field = None
    year_built: Field = None
    stabilization_status: Field = None
    msa: Field = None
    address: Field = None
    use_mix: UseMixField = None

    @model_validator(mode="before")
    @classmethod
    def _default_missing(cls, data: Any) -> Any:
        return _fill_missing(cls, data)


class LoanTerms(BaseModel):
    requested_amount: Field = None
    annual_debt_service: Field = None
    quoted_rate: Field = None
    quoted_term: Field = None

    @model_validator(mode="before")
    @classmethod
    def _default_missing(cls, data: Any) -> Any:
        return _fill_missing(cls, data)


class Valuation(BaseModel):
    appraised_value: Field = None
    appraisal_type: Field = None
    appraisal_status: Field = None

    @model_validator(mode="before")
    @classmethod
    def _default_missing(cls, data: Any) -> Any:
        return _fill_missing(cls, data)


class Operations(BaseModel):
    noi: Field = None
    effective_gross_income: Field = None
    gross_potential_rent: Field = None
    operating_expenses: Field = None
    trailing_period: Field = None  # e.g. "T-12", "T-3 annualized", "pro forma"

    @model_validator(mode="before")
    @classmethod
    def _default_missing(cls, data: Any) -> Any:
        return _fill_missing(cls, data)


class Occupancy(BaseModel):
    physical_units_total: Field = None
    units_occupied_total: Field = None  # rent rolls often state only this, no paying split
    units_occupied_paying: Field = None
    units_vacant: Field = None
    units_occupied_nonpaying: Field = None  # its own bucket; never folded into the others
    economic_occupancy: Field = None
    stated_occupancy: Field = None  # broker's number, kept apart from anything computed
    rent_roll_as_of: Field = None

    @model_validator(mode="before")
    @classmethod
    def _default_missing(cls, data: Any) -> Any:
        return _fill_missing(cls, data)


class Sponsor(BaseModel):
    net_worth: Field = None
    liquidity: Field = None
    asset_count: Field = None
    credit_events: ListField = None

    @model_validator(mode="before")
    @classmethod
    def _default_missing(cls, data: Any) -> Any:
        return _fill_missing(cls, data)


class PackageInfo(BaseModel):
    documents_present: ListField = None
    documents_missing: ListField = None
    earliest_document_date: Field = None

    @model_validator(mode="before")
    @classmethod
    def _default_missing(cls, data: Any) -> Any:
        return _fill_missing(cls, data)


class BrokerClaim(BaseModel):
    """A metric the package *asserts* (e.g. "conservative 72% LTV").

    Claims are quarantined here so they can never be mistaken for facts:
    derive.py and rules.py ignore them entirely; only the discrepancy
    checker reads them, to compare against the derived figure.
    """
    metric: Literal["ltv", "dscr"]
    value: float
    source: str | None = None


class ExtractedDeal(BaseModel):
    identity: Identity = None
    property: PropertyInfo = None
    loan: LoanTerms = None
    valuation: Valuation = None
    operations: Operations = None
    occupancy: Occupancy = None
    sponsor: Sponsor = None
    package: PackageInfo = None
    broker_claims: list[BrokerClaim] = []

    @model_validator(mode="before")
    @classmethod
    def _default_groups(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for name, f in cls.model_fields.items():
                if data.get(name) is None and name != "broker_claims":
                    data[name] = {}
        return data


def _fill_missing(cls: type[BaseModel], data: Any) -> Any:
    """Default any absent field to an explicit missing marker."""
    if not isinstance(data, dict):
        return data
    for name, f in cls.model_fields.items():
        if data.get(name) is None:
            ann = str(f.annotation)
            if "UseMixField" in ann:
                data[name] = UseMixField.missing("not extracted")
            elif "ListField" in ann:
                data[name] = ListField.missing("not extracted")
            else:
                data[name] = Field.missing("not extracted")
    return data


# ---------------------------------------------------------------------------
# Derived metrics: produced only by derive.py, never by the LLM
# ---------------------------------------------------------------------------

class DerivedMetric(BaseModel):
    name: str
    value: float | None = None
    computable: bool
    reason: str | None = None          # required when not computable
    inputs_used: list[str] = []        # extracted field names consumed

    @model_validator(mode="after")
    def _consistent(self) -> "DerivedMetric":
        if not self.computable and not self.reason:
            raise ValueError(f"{self.name}: not computable requires a reason")
        if self.computable and self.value is None:
            raise ValueError(f"{self.name}: computable metric must carry a value")
        return self


class DerivedMetrics(BaseModel):
    ltv: DerivedMetric
    dscr: DerivedMetric
    occupancy_paying_only: DerivedMetric
    occupancy_physical: DerivedMetric  # the box's metric: occupied units / total
    occupancy_variants_diverge: bool = False  # nonpaying units make the two differ
    liquidity_months: DerivedMetric


class Discrepancy(BaseModel):
    field_name: str
    stated_value: float | None
    derived_value: float | None
    delta: float | None
    severity: Literal["minor", "major"]
    note: str | None = None


# ---------------------------------------------------------------------------
# Rule evaluation output: the audit artifact
# ---------------------------------------------------------------------------

class Outcome(str, Enum):
    ADVANCE = "advance"
    DECLINE = "decline"
    NEEDS_REVIEW = "needs_review"


class CriterionStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUABLE = "not_evaluable"
    SKIPPED = "skipped"  # hard decline already fired; criterion never ran


class CriterionResult(BaseModel):
    criterion: str
    enforcement: Literal["hard", "graduated"]
    status: CriterionStatus
    value: Any = None                  # what was tested
    threshold: Any = None              # what it was tested against
    inputs_used: list[str] = []
    margin: float | None = None        # relative slack; >0 inside the box, <0 outside
    near_line: bool = False            # outside but within configured tolerance
    note: str | None = None
    enforcement_note: str | None = None  # e.g. "source doc did not classify; config decision"


class RuleTrace(BaseModel):
    box_version: str
    results: list[CriterionResult]
    outcome: Outcome
    outcome_reasons: list[str]
    correlated_flags: list[str] = []   # graduated misses pointing the same direction


class ScreenResult(BaseModel):
    """One full screening pass over one deal."""
    deal: ExtractedDeal
    derived: DerivedMetrics | None = None   # None when a hard decline fired first
    discrepancies: list[Discrepancy] = []
    trace: RuleTrace
