"""Typed contracts for the SendInvoice workflow.

``InvoiceProposal`` is the structured output the agent returns from
``Runner.run`` — it is what ``evaluate_policy`` consumes (Stage 5 will
make this enforceable), what the approval UI will render (Stage 12), and
what ``execute_send`` persists. Defined here (not in ``mcp_bank.models``)
because the MCP surface and the workflow surface evolve independently.
"""

from typing import Any, Literal

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
    # Clarification path: set when the request is genuinely ambiguous given
    # the available data (e.g. several billable periods/services match and the
    # request doesn't say which). The agent asks instead of guessing; the
    # workflow then returns outcome="needs_clarification" and skips policy.
    # When True, the other fields are placeholders and line_items may be empty.
    needs_clarification: bool = False
    clarification_question: str | None = None


class SendInvoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_message: str
    # 1-hour default; overridable per request.
    approval_timeout_seconds: int = 3600
    # ---- ablation levers (carried through the workflow boundary so the
    # deterministic workflow doesn't read env) ----
    # Agent prompt: "fixed" (default) vs "legacy" (the abstention-prone prompt).
    prompt_variant: Literal["fixed", "legacy"] = "fixed"
    # Give the agent the compute_line_total / compute_invoice_total tools so it
    # offloads the micros/cents arithmetic (the dominant amount-error source).
    # On by default — a clear correctness win.
    use_invoice_tool: bool = True
    # Self-healing: on a pre_action_proposal policy block, feed the violation
    # back to the agent and retry up to this many extra attempts (0 = off).
    self_heal_max_attempts: int = 0
    # Clarification round-trip: when the agent asks (needs_clarification), the
    # workflow waits for a `clarify` signal with the answer. None (the default)
    # means wait indefinitely — a human gets unlimited time to answer. A bound
    # is opt-in (the eval harness sets one so an over-clarifying agent fails
    # fast instead of hanging the run); on bound expiry the workflow returns
    # needs_clarification terminally.
    clarification_timeout_seconds: int | None = None


class ClarificationResponse(BaseModel):
    """Payload of the ``clarify`` workflow signal — the caller's answer to the
    agent's clarification question."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    responder_id: str


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
    escalations: list[dict[str, Any]] = []
    next_sequence_no: int


WorkflowOutcome = Literal[
    "sent", "policy_rejected", "declined", "timeout", "unsupported", "needs_clarification"
]


class WorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: WorkflowOutcome
    invoice_id: str | None = None
    detail: str | None = None
