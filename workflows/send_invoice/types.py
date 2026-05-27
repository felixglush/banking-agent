"""Typed contracts for the SendInvoice workflow.

``InvoiceProposal`` is the structured output the agent returns from
``Runner.run`` — it is what ``evaluate_policy`` consumes (Stage 5 will
make this enforceable), what the approval UI will render (Stage 12), and
what ``execute_send`` persists. Defined here (not in ``mcp_bank.models``)
because the MCP surface and the workflow surface evolve independently.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LineItemSourceType = Literal["contract", "rate_card", "time_tracking", "user_specified"]


class LineItemProposal(BaseModel):
    """Single proposed line item with full provenance.

    The agent must populate ``source_type``, ``source_refs``, and
    ``computation`` for every line; Stage 5 turns that into enforceable
    policy via ``require_evidence_citation``.
    """

    model_config = ConfigDict(extra="forbid")

    description: str
    quantity_micros: int = Field(gt=0)
    unit_amount_cents: int = Field(gt=0)
    line_total_cents: int = Field(gt=0)
    source_type: LineItemSourceType
    source_refs: list[str]
    computation: str


class InvoiceProposal(BaseModel):
    """Structured output of the main agent.

    Currency is ISO-4217 (e.g. ``"USD"``); cents are integers; quantities
    are micros (``value * 1e6``) to match the schema.
    """

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    currency: str
    total_cents: int = Field(gt=0)
    payment_terms_days: int = Field(gt=0)
    source_type: LineItemSourceType
    contract_id: str | None = None
    line_items: list[LineItemProposal]
    notes: str | None = None


class SendInvoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_message: str
    # 1-hour default; overridable per request.
    approval_timeout_seconds: int = 3600


class ApprovalDecision(BaseModel):
    """Payload of the ``approve`` workflow signal."""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    approver_id: str
    notes: str | None = None


class PolicyDecisionPayload(BaseModel):
    """Activity return from ``evaluate_policy`` at Stage 5.

    Replaces the Stage-4 ``PolicyDecision`` stub. The compass
    ``Decision`` type lives in compass.policy.types; this is the
    activity-boundary serialization the workflow consumes.
    """

    model_config = ConfigDict(extra="forbid")

    permit: bool
    policy_hash: str
    rule_ids_fired: list[str] = []
    escalations: list[dict] = []
    next_sequence_no: int


WorkflowOutcome = Literal["sent", "policy_rejected", "declined", "timeout"]


class WorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: WorkflowOutcome
    invoice_id: str | None = None
    detail: str | None = None
