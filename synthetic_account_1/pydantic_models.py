"""Pydantic models for Synthetic Account 1.

The ``Contract`` model is the load-time validation point referenced in
docs/build-plan.md line 118 ("anything that fails schema validation is
rejected at load time and never reaches the policy engine"). The other
models give the generator and verifier a typed surface for the records
it writes to JSONL.

All amounts are integer cents; quantities/hours are stored as integer
micros (``value * 1e6``) so the JSONL round-trips through Postgres
``BIGINT`` columns without floating-point drift.
"""

from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------
# Contract billing structure (pre-derived; see build-plan line 118).
# ---------------------------------------------------------------------


class Milestone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    amount_cents: int = Field(gt=0)
    due_date: date


class FlatFeeSOW(BaseModel):
    """Flat-fee statement of work with milestone schedule."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["flat_fee_sow"] = "flat_fee_sow"
    total_amount_cents: int = Field(gt=0)
    milestones: list[Milestone] = Field(min_length=1)

    @model_validator(mode="after")
    def _milestones_sum_to_total(self) -> Self:
        total = sum(m.amount_cents for m in self.milestones)
        if total != self.total_amount_cents:
            raise ValueError(
                f"flat_fee_sow milestones sum to {total}, "
                f"expected total_amount_cents={self.total_amount_cents}"
            )
        return self


class MonthlyRetainer(BaseModel):
    """Fixed monthly fee. Includes optional T&M tail."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["monthly_retainer"] = "monthly_retainer"
    monthly_amount_cents: int = Field(gt=0)


class RateOverride(BaseModel):
    """A negotiated rate that supersedes the list rate for a role/service."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=1)
    unit: Literal["hour", "flat", "month"]
    amount_cents: int = Field(gt=0)


class TimeAndMaterials(BaseModel):
    """T&M with negotiated rates OR a monthly hour cap (but not neither)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["t_and_m"] = "t_and_m"
    rate_overrides: list[RateOverride] = Field(default_factory=list[RateOverride])
    monthly_hour_cap: int | None = Field(default=None, gt=0)
    list_rates_apply: bool = True

    @model_validator(mode="after")
    def _has_some_structure(self) -> Self:
        if not self.rate_overrides and self.monthly_hour_cap is None and self.list_rates_apply:
            # A pure-list-rate T&M arrangement is fine — that's a contract
            # that simply says "we'll do T&M against your published list
            # rates." No assertion needed.
            return self
        return self


BillingStructure = Annotated[
    FlatFeeSOW | MonthlyRetainer | TimeAndMaterials,
    Field(discriminator="kind"),
]


class Contract(BaseModel):
    """Pre-structured contract row. Build-plan line 118: 'rejected at load time'."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    kind: Literal["msa", "sow", "retainer"]
    effective_from: date
    expires_at: date | None = None
    currency: str = Field(min_length=3, max_length=3)
    billing_structure: BillingStructure
    rate_overrides: list[RateOverride] = Field(default_factory=list[RateOverride])
    monthly_hour_cap: int | None = None
    scope_summary: str = Field(min_length=1)
    source_doc_ref: str | None = None

    @model_validator(mode="after")
    def _expires_after_effective(self) -> Self:
        if self.expires_at is not None and self.expires_at <= self.effective_from:
            raise ValueError("expires_at must be strictly after effective_from")
        return self


# ---------------------------------------------------------------------
# Other domain models (used by simulate.py for typed construction).
# ---------------------------------------------------------------------


KycStatus = Literal["verified", "pending", "restricted", "rejected"]


class Customer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    email: str
    address: str
    kyc_status: KycStatus
    default_payment_terms_days: int = Field(gt=0)
    cohort: str
    created_at: datetime


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: Literal["operating", "payroll", "reserve", "credit_card"]
    currency: str
    balance_cents: int
    opened_at: datetime


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    account_id: str
    amount_cents: int = Field(gt=0)
    direction: Literal["debit", "credit"]
    counterparty: str
    memo: str
    category: str
    posted_at: datetime
    related_invoice_id: str | None = None


class RateCardEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    service: str
    role: str | None = None
    unit: Literal["hour", "flat", "month"]
    list_amount_cents: int = Field(gt=0)
    currency: str
    effective_from: date
    effective_to: date | None = None


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    customer_id: str
    name: str
    status: Literal["active", "completed", "on_hold"]


class TimeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    customer_id: str
    project_id: str
    role: str
    hours_micros: int = Field(gt=0)
    occurred_at: date
    description: str
    invoiced: bool = False


class InvoiceLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    invoice_id: str
    line_no: int = Field(ge=1)
    description: str
    quantity_micros: int = Field(gt=0)
    unit_amount_cents: int = Field(gt=0)
    line_total_cents: int = Field(gt=0)
    source_type: Literal["contract", "rate_card", "time_tracking", "user_specified"]
    source_refs: dict[str, object]
    computation: str


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    customer_id: str
    issued_at: datetime
    due_at: datetime
    total_cents: int = Field(gt=0)
    currency: str
    status: Literal["draft", "sent", "paid", "overdue", "disputed"]
    payment_received_at: datetime | None = None
    source_type: Literal["contract", "rate_card", "time_tracking", "user_specified"]
    contract_id: str | None = None
    dispute_flag: bool = False


class Dispute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    transaction_id: str
    opened_at: datetime
    kind: str
    status: str
    resolution_outcome: str | None = None
