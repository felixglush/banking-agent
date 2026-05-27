"""Pydantic models for ``bank`` MCP tool I/O.

Defined here (not reused from ``synthetic_account_1.pydantic_models``)
because the tool surface is the agent-visible contract — it must stay
stable independently of the generator's record types. Field shapes
mirror ``db/schema.sql``; amounts in cents, hours/quantities in micros
(``value * 1e6``), dates as ``date``, timestamps as timezone-aware
``datetime``.
"""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class BoundedList[T](BaseModel):
    """List result capped at a server-side maximum.

    ``truncated=True`` means the underlying query matched more rows than
    the cap and the caller should narrow its filters; the MCP server does
    not paginate the missing rows — see ``mcp_bank/README.md``.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[T]
    truncated: bool


KycStatus = Literal["verified", "pending", "restricted", "rejected"]
InvoiceStatus = Literal["draft", "sent", "paid", "overdue", "disputed"]
LineItemSourceType = Literal["contract", "rate_card", "time_tracking", "user_specified"]
TransactionDirection = Literal["debit", "credit"]
AccountType = Literal["operating", "payroll", "reserve", "credit_card"]
RateUnit = Literal["hour", "flat", "month"]
ContractKind = Literal["msa", "sow", "retainer"]


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
    type: AccountType
    currency: str
    balance_cents: int
    opened_at: datetime


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    account_id: str
    amount_cents: int = Field(gt=0)
    direction: TransactionDirection
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
    unit: RateUnit
    list_amount_cents: int = Field(gt=0)
    currency: str
    effective_from: date
    effective_to: date | None = None


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
    source_type: LineItemSourceType
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
    status: InvoiceStatus
    payment_received_at: datetime | None = None
    source_type: LineItemSourceType
    contract_id: str | None = None
    dispute_flag: bool = False
    line_items: list[InvoiceLineItem] = Field(default_factory=list[InvoiceLineItem])


class Contract(BaseModel):
    """Contract row as exposed by the MCP.

    ``billing_structure`` and ``rate_overrides`` are returned as JSONB
    blobs — the agent reasons over their shape; the generator's typed
    discriminated union (``synthetic_account_1.pydantic_models``) is the
    load-time validation point, not the MCP surface.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    customer_id: str
    kind: ContractKind
    effective_from: date
    expires_at: date | None = None
    currency: Annotated[str, Field(min_length=3, max_length=3)]
    billing_structure: dict[str, object]
    rate_overrides: list[dict[str, object]] = Field(default_factory=list[dict[str, object]])
    monthly_hour_cap: int | None = None
    scope_summary: str
    source_doc_ref: str | None = None
