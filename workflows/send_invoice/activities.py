"""Side-effect activities for the SendInvoice workflow.

Three activities, all idempotent under Temporal retries:

* ``evaluate_policy`` — Stage 4 stub that writes a ``proposal`` audit row
  and returns permit. Stage 5 replaces the body with ``compass.policy``.
* ``execute_send`` — writes a row into ``invoices`` + ``invoice_line_items``,
  keyed on ``idempotency_key`` (the workflow run id). ``ON CONFLICT DO
  NOTHING`` makes activity retries safe.
* ``audit_log`` — appends to ``audit_log``. ``UNIQUE (workflow_run_id,
  sequence_no)`` + ``ON CONFLICT DO NOTHING`` makes retries safe.

Connection lifetime: one ``psycopg.AsyncConnection`` per activity call.
At Stage 4 single-workflow demo volume, a pool buys nothing and a pool
that outlives the worker process is one more shutdown hook to forget.
"""

import os
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.exceptions import ApplicationError

from workflows.send_invoice.types import (
    ApprovalDecision,
    InvoiceProposal,
    PolicyDecision,
)

# Used in every audit row at Stage 4 — Stage 5 generates a real hash from
# the loaded RULES module. Constant so audit log queries can filter
# "Stage 4 runs" out by hash if they want.
_STAGE_4_POLICY_HASH = "stage-4-stub"


def _dsn() -> str:
    """DSN for runtime-owned tables (``audit_log``, ``invoices``, ...).

    Reused from ``COMPASS_PG_DSN`` so the worker and the MCP subprocess
    point at the same Postgres instance.
    """
    dsn = os.environ.get("COMPASS_PG_DSN")
    if not dsn:
        raise RuntimeError("workflows.send_invoice.activities: COMPASS_PG_DSN must be set.")
    return dsn


# ---------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------


@dataclass
class AuditEvent:
    """Inputs to ``audit_log``. A dataclass (not pydantic) because Temporal
    serializes dataclasses cleanly and we want a stable activity signature."""

    workflow_run_id: str
    sequence_no: int
    phase: str
    event_kind: str
    payload: dict[str, Any]
    decision: str | None = None
    rule_id: str | None = None
    actor: dict[str, Any] | None = None


async def _write_audit_row(event: AuditEvent) -> None:
    """The actual DB write. Pulled out so ``evaluate_policy`` can append
    a ``proposal`` row without re-entering Temporal as a nested activity.
    """
    actor_param = Jsonb(event.actor) if event.actor is not None else None
    async with await psycopg.AsyncConnection.connect(_dsn()) as conn, conn.cursor() as cur:
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
                _STAGE_4_POLICY_HASH,
                event.decision,
                actor_param,
                Jsonb(event.payload),
            ),
        )
        await conn.commit()


@activity.defn
async def audit_log(event: AuditEvent) -> None:
    """Append one row to ``audit_log``.

    Idempotent on ``(workflow_run_id, sequence_no)`` — retries collide on
    the UNIQUE constraint and ``ON CONFLICT DO NOTHING`` drops the second
    write. ``event_kind`` is intentionally not part of the key (build-plan
    §Stage 4 interop rule 3) so multiple rules in one phase don't
    collide.
    """
    await _write_audit_row(event)


# ---------------------------------------------------------------------
# evaluate_policy (Stage 4 stub)
# ---------------------------------------------------------------------


@dataclass
class EvaluatePolicyInput:
    workflow_run_id: str
    sequence_no: int
    proposal: dict[str, Any]


@activity.defn
async def evaluate_policy(args: EvaluatePolicyInput) -> PolicyDecision:
    """Stage 4 stub: write a ``proposal`` audit row and permit.

    Stage 5 fills in the real engine. The signature is shaped so the
    Stage-5 swap is purely additive — same input, same output, same retry
    contract. ``PolicyDecisionError`` (block / escalate) is raised as
    ``ApplicationError(non_retryable=True)``; ``PolicyEngineError`` /
    ``PolicyInfraError`` would be raised as retryable. Neither path is
    exercised at Stage 4.
    """
    try:
        await _write_audit_row(
            AuditEvent(
                workflow_run_id=args.workflow_run_id,
                sequence_no=args.sequence_no,
                phase="pre_action_proposal",
                event_kind="proposal",
                decision="permit",
                payload={"proposal": args.proposal},
            )
        )
    except Exception as e:  # noqa: BLE001
        raise ApplicationError(
            f"evaluate_policy: audit write failed: {e}",
            type="PolicyInfraError",
            non_retryable=False,
        ) from e
    return PolicyDecision(permit=True, rule_ids_fired=[])


# ---------------------------------------------------------------------
# execute_send
# ---------------------------------------------------------------------


@dataclass
class ExecuteSendInput:
    workflow_run_id: str
    proposal: dict[str, Any]
    approval: dict[str, Any]


@activity.defn
async def execute_send(args: ExecuteSendInput) -> str:
    """Persist the approved invoice. Returns the invoice id.

    Idempotency: the invoice id is derived from ``workflow_run_id`` so two
    retries of this activity produce the same primary key, and ``ON
    CONFLICT DO NOTHING`` on every insert makes the second pass a no-op.
    The "send" verb is a no-op log line at v0.1 — no email/PDF; the
    persisted row is the artifact downstream evals check.
    """
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
    return invoice_id
