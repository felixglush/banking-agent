"""Side-effect activities for the SendInvoice workflow.

Stage 5:
* ``evaluate_policy`` runs compass.policy.evaluate at the requested
  phase, writes a policy_snapshots row, and maps exceptions to
  Temporal's retry semantics. Switches on phase (pre_action_proposal,
  pre_execute, audit_validation) — one activity, runtime arg.
* ``execute_send`` unchanged from Stage 4.
* ``audit_log`` grows ``is_terminal_event``: when True, runs
  evaluate_audit_validation against the candidate row before insert,
  and writes rule_fired events + the original row in one transaction.

All three remain idempotent under Temporal retries — see
docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md
§Activity failure semantics.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, cast

import psycopg
from langfuse import LangfuseOtelSpanAttributes
from opentelemetry import trace as otel_trace
from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.exceptions import ApplicationError

from compass.policy import (
    Actor,
    AuditPayload,
    Phase,
    PolicyEngineError,
    ToolCallRecord,
    evaluate,
    write_policy_snapshot,
)
from compass.policy.audit_sink import AuditLogSink, SequenceAllocator
from policies.send_invoice import RULES
from workflows.send_invoice.context import hash_proposal
from workflows.send_invoice.types import (
    ApprovalDecision,
    InvoiceProposal,
    PolicyDecisionPayload,
)


def _dsn() -> str:
    dsn = os.environ.get("COMPASS_PG_DSN")
    if not dsn:
        raise RuntimeError("workflows.send_invoice.activities: COMPASS_PG_DSN must be set.")
    return dsn


# (phase, event_kind) pairs that immediately precede a WorkflowResult return,
# mapped to the WorkflowResult.outcome they produce. Kept in lockstep with the
# returns in workflows.send_invoice.workflow.SendInvoiceWorkflow.run; the trace
# output is reconstructed from the audit payload so every outcome — not just
# the `sent` path — carries a meaningful output in Langfuse.
def _terminal_outcome(event: "AuditEvent") -> str | None:
    """WorkflowResult.outcome for a terminal audit event, else None."""
    kind, phase = event.event_kind, event.phase
    if kind == "executed":
        return "sent"
    if kind == "policy_rejected":
        return "policy_rejected"
    if kind == "unsupported":
        return "unsupported"
    if kind == "declined":
        reason = cast(str | None, event.payload.get("reason"))
        return "timeout" if reason == "approval_timeout" else "declined"
    if kind == "agent_no_output":
        # input_validation → unsupported; pre_action_proposal → policy_rejected.
        return "unsupported" if phase == "input_validation" else "policy_rejected"
    return None


def _trace_output(event: "AuditEvent", outcome: str) -> dict[str, Any]:
    """Structured trace output for a terminal event: outcome + salient fields."""
    out: dict[str, Any] = {"outcome": outcome}
    if outcome == "sent":
        out["invoice_id"] = event.payload.get("invoice_id")
        out["total_cents"] = event.payload.get("total_cents")
        return out
    detail = (
        event.payload.get("notes")
        or event.payload.get("reason")
        or event.payload.get("message")
    )
    if detail is not None:
        out["detail"] = detail
    return out


def _enrich_langfuse_trace(event: "AuditEvent") -> None:
    """Set Langfuse trace-level attributes on the active OTel span.

    Langfuse aggregates these attributes onto the trace regardless of
    which span sets them — so emitting from inside an activity works.
    Silent no-op if no active span (Langfuse not configured).

    Trace attributes set:
    * Always: tags include ``wf:<workflow_run_id>`` so compass.eval can
      look up a workflow's trace from its Temporal workflow_id.
    * ``intent_classified`` event: trace_name=send_invoice:<intent>,
      tags also include ``send_invoice`` and the intent.
    * trace_input: the user message, whenever the payload carries one.
    * trace_output: outcome + salient fields for every terminal event
      (each WorkflowResult-returning branch), not just the `sent` path.
    * user.id from actor when present.
    """
    span = otel_trace.get_current_span()
    if not span.is_recording():
        return
    tags: list[str] = [f"wf:{event.workflow_run_id}"]
    if event.event_kind == "intent_classified":
        classification = cast(dict[str, Any] | None, event.payload.get("classification"))
        if classification is not None:
            intent = cast(str | None, classification.get("intent"))
            if intent is not None:
                span.set_attribute(LangfuseOtelSpanAttributes.TRACE_NAME, f"send_invoice:{intent}")
                tags.extend(["send_invoice", intent])
    span.set_attribute(LangfuseOtelSpanAttributes.TRACE_TAGS, json.dumps(tags))

    user_message = cast(str | None, event.payload.get("user_message"))
    if user_message is not None:
        span.set_attribute(LangfuseOtelSpanAttributes.TRACE_INPUT, user_message)

    if event.actor is not None:
        user_id = cast(str | None, event.actor.get("user_id"))
        if user_id is not None:
            span.set_attribute(LangfuseOtelSpanAttributes.TRACE_USER_ID, user_id)

    outcome = _terminal_outcome(event)
    if outcome is not None:
        span.set_attribute(
            LangfuseOtelSpanAttributes.TRACE_OUTPUT,
            json.dumps(_trace_output(event, outcome)),
        )


# ---------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------


@dataclass
class AuditEvent:
    workflow_run_id: str
    sequence_no: int
    phase: str
    event_kind: str
    payload: AuditPayload
    decision: str | None = None
    rule_id: str | None = None
    actor: Actor | None = None
    # New at Stage 5. When True, evaluate_audit_validation runs against
    # this event's payload before the row is written. Used for the
    # final terminal audit row of the workflow.
    is_terminal_event: bool = False
    # Workflow's current policy_hash (captured at pre_action_proposal).
    # Required when is_terminal_event=True so the audit_validation
    # rules can check log_policy_version.
    policy_hash_for_validation: str | None = None
    # Tool calls + reasoning are passed through for the same reason.
    tool_calls_for_validation: list[ToolCallRecord] = field(default_factory=lambda: [])
    reasoning_text_for_validation: str = ""


async def _write_audit_row(
    cur: psycopg.AsyncCursor,
    event: AuditEvent,
    *,
    policy_hash: str | None,
) -> None:
    """Single-row INSERT into audit_log; idempotent via ON CONFLICT."""
    actor_param = Jsonb(event.actor) if event.actor is not None else None
    await cur.execute(
        """
        INSERT INTO audit_log (
            workflow_run_id, sequence_no, phase, event_kind, rule_id,
            policy_hash, decision, actor, payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (workflow_run_id, sequence_no) DO NOTHING
        """,
        (
            event.workflow_run_id,
            event.sequence_no,
            event.phase,
            event.event_kind,
            event.rule_id,
            policy_hash or "unknown",
            event.decision,
            actor_param,
            Jsonb(event.payload),
        ),
    )


@activity.defn
async def audit_log(event: AuditEvent) -> None:
    """Append one (or more) rows to audit_log.

    Non-terminal events: one row, simple insert.

    Terminal events: run evaluate_audit_validation against the
    candidate row in-memory; emit rule_fired events through an
    AuditLogSink that allocates sequence numbers starting at
    event.sequence_no + 1; then insert the original terminal row at
    event.sequence_no. All in one transaction — recursion-safe.

    No raise on audit_validation BLOCK — at Stage 5 those rules fire
    only on workflow defects, and we write the row regardless so the
    audit trail isn't lost. The rule_fired row stays in the log for
    later analysis.
    """
    _enrich_langfuse_trace(event)
    policy_disabled = os.environ.get("COMPASS_POLICY_DISABLE") == "1"
    async with await psycopg.AsyncConnection.connect(_dsn()) as conn:
        try:
            async with conn.cursor() as cur:
                if event.is_terminal_event and not policy_disabled:
                    # Run audit_validation rules; their rule_fired/skipped
                    # rows are written via AuditLogSink starting one past
                    # the terminal row's reserved slot.
                    ctx = {
                        "audit_entry_candidate": {
                            "phase": event.phase,
                            "event_kind": event.event_kind,
                            "payload": event.payload,
                        },
                        "policy_hash": event.policy_hash_for_validation,
                        "tool_calls": event.tool_calls_for_validation,
                        "reasoning_text": event.reasoning_text_for_validation,
                    }
                    sink = AuditLogSink(
                        conn,
                        event.workflow_run_id,
                        SequenceAllocator(event.sequence_no + 1),
                        event.policy_hash_for_validation or "unknown",
                    )
                    try:
                        await evaluate(
                            RULES,
                            Phase.audit_validation,
                            ctx,
                            sink=sink,
                        )
                    except PolicyEngineError as e:
                        raise ApplicationError(
                            str(e),
                            type="PolicyEngineError",
                            non_retryable=not e.retryable,
                        ) from e
                await _write_audit_row(
                    cur,
                    event,
                    policy_hash=event.policy_hash_for_validation,
                )
            await conn.commit()
        except psycopg.Error as e:
            raise ApplicationError(
                str(e),
                type="PolicyInfraError",
                non_retryable=False,
            ) from e


# ---------------------------------------------------------------------
# evaluate_policy
# ---------------------------------------------------------------------


@dataclass
class EvaluatePolicyInput:
    workflow_run_id: str
    starting_sequence_no: int
    phase: str  # Phase enum value as string (Temporal dataclass-friendly)
    context: dict[str, Any]


@activity.defn
async def evaluate_policy(args: EvaluatePolicyInput) -> PolicyDecisionPayload:
    """Run evaluate() at the requested phase; persist snapshot + audit.

    See spec §Workflow integration — evaluate_policy.
    """
    phase = Phase(args.phase)
    # Type the activity span as a Langfuse guardrail observation so the
    # UI shows policy gates as gates, not generic spans.
    span = otel_trace.get_current_span()
    if span.is_recording():
        span.set_attribute(LangfuseOtelSpanAttributes.OBSERVATION_TYPE, "guardrail")
        span.set_attribute(
            LangfuseOtelSpanAttributes.OBSERVATION_INPUT,
            json.dumps({"phase": args.phase}),
        )

    # ---- eval-only ablation hatch ----------------------------------
    # Stage-7+ policy-ablation eval (build-plan §Stage 7) flips this
    # env var to measure the marginal value of the policy engine. When
    # set, the activity short-circuits to permit=True with a sentinel
    # policy_hash so audit_log queries can identify ablation runs.
    # Zero impact on production (env var is never set).
    if os.environ.get("COMPASS_POLICY_DISABLE") == "1":
        activity.logger.warning(
            "COMPASS_POLICY_DISABLE=1 — bypassing policy gate at phase=%s "
            "(eval-only ablation; should never be set in production)",
            phase.value,
        )
        return PolicyDecisionPayload(
            permit=True,
            policy_hash="disabled-for-eval",
            rule_ids_fired=[],
            escalations=[],
            next_sequence_no=args.starting_sequence_no,
        )

    try:
        async with await psycopg.AsyncConnection.connect(_dsn()) as conn:
            policy_hash = await write_policy_snapshot(conn, "send_invoice", RULES)
            allocator = SequenceAllocator(args.starting_sequence_no)
            sink = AuditLogSink(
                conn,
                args.workflow_run_id,
                allocator,
                policy_hash,
            )

            # The drift-detection primitives compare hashes pulled from
            # the context dict. The workflow puts proposal_hash_at_proposal
            # in there; we add current_proposal_hash (recomputed here
            # from the proposal) and current_policy_hash (the hash we
            # just computed for the snapshot).
            ctx = dict(args.context)
            if "proposal" in ctx and ctx.get("proposal") is not None:
                ctx["current_proposal_hash"] = hash_proposal(ctx["proposal"])
            ctx["current_policy_hash"] = policy_hash

            try:
                decision = await evaluate(RULES, phase, ctx, sink=sink)
            except PolicyEngineError as e:
                raise ApplicationError(
                    str(e),
                    type="PolicyEngineError",
                    non_retryable=not e.retryable,
                ) from e

            await conn.commit()
    except psycopg.Error as e:
        raise ApplicationError(
            str(e),
            type="PolicyInfraError",
            non_retryable=False,
        ) from e

    if not decision.permit:
        raise ApplicationError(
            "policy blocked",
            {
                "phase": phase.value,
                "rule_ids_fired": list(decision.rule_ids_fired),
                "violations": [
                    {"rule_id": v.rule_id, "message": v.message, "evidence": v.evidence}
                    for v in decision.violations
                ],
            },
            type="PolicyDecisionError",
            non_retryable=True,
        )

    return PolicyDecisionPayload(
        permit=True,
        policy_hash=policy_hash,
        rule_ids_fired=list(decision.rule_ids_fired),
        escalations=[
            {"rule_id": v.rule_id, "message": v.message, "evidence": v.evidence}
            for v in decision.escalations
        ],
        next_sequence_no=allocator.peek(),
    )


# ---------------------------------------------------------------------
# execute_send  (unchanged from Stage 4)
# ---------------------------------------------------------------------


@dataclass
class ExecuteSendInput:
    workflow_run_id: str
    proposal: dict[str, Any]
    approval: dict[str, Any]


@activity.defn
async def resolve_customer_contract(customer_id: str) -> dict[str, Any] | None:
    """Return the customer's contract (each customer has at most one), as the
    fields the policy reads — ``id``, ``currency``, ``monthly_hour_cap``.

    Used by the workflow to resolve the contract deterministically for
    contract/time-tracking invoices when the agent didn't — the contract a
    customer bills under is a property of the customer, not a model judgment.
    The workflow injects this into ``resolved_entities.contract`` so the
    policy gates see a fully-resolved contract (not a dangling id)."""
    async with await psycopg.AsyncConnection.connect(_dsn()) as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT id, currency, monthly_hour_cap FROM contracts WHERE customer_id = %s "
            "ORDER BY effective_from DESC, id DESC LIMIT 1",
            (customer_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return {"id": row[0], "currency": row[1], "monthly_hour_cap": row[2]}


@activity.defn
async def execute_send(args: ExecuteSendInput) -> str:
    """Persist the approved invoice. Returns the invoice id."""
    proposal = InvoiceProposal.model_validate(args.proposal)
    approval = ApprovalDecision.model_validate(args.approval)
    invoice_id = f"inv-{args.workflow_run_id}"
    activity.logger.info(
        "execute_send: persisting %s for customer=%s total=%s%s approver=%s",
        invoice_id,
        proposal.customer_id,
        proposal.total_cents,
        proposal.currency,
        approval.approver_id,
    )

    try:
        async with await psycopg.AsyncConnection.connect(_dsn()) as conn, conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO invoices (
                    id, customer_id, issued_at, due_at, total_cents, currency,
                    status, source_type, contract_id
                )
                VALUES (%s, %s, now(), now() + (%s || ' days')::interval, %s, %s,
                        'sent', %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    invoice_id,
                    proposal.customer_id,
                    str(proposal.payment_terms_days),
                    proposal.total_cents,
                    proposal.currency,
                    proposal.source_type,
                    proposal.contract_id,
                ),
            )
            for line_no, line in enumerate(proposal.line_items, start=1):
                await cur.execute(
                    """
                    INSERT INTO invoice_line_items (
                        id, invoice_id, line_no, description, quantity_micros,
                        unit_amount_cents, line_total_cents, source_type,
                        source_refs, computation
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        f"{invoice_id}-li-{line_no:02d}",
                        invoice_id,
                        line_no,
                        line.description,
                        line.quantity_micros,
                        line.unit_amount_cents,
                        line.line_total_cents,
                        line.source_type,
                        Jsonb({"refs": line.source_refs}),
                        line.computation,
                    ),
                )
            await conn.commit()
    except psycopg.errors.IntegrityError as e:
        # Deterministic data error — the proposal references a customer or
        # contract that doesn't exist (often an agent-hallucinated id). Retrying
        # can never succeed, so fail non-retryably instead of looping on the
        # default retry backoff forever (which hangs the workflow and, in turn,
        # the eval harness awaiting its result).
        raise ApplicationError(
            f"invoice insert violated a database constraint: {e}",
            type="InvoiceIntegrityError",
            non_retryable=True,
        ) from e
    return invoice_id
