"""``SendInvoiceWorkflow`` — durable orchestration for the send-invoice flow.

Per docs/build-plan.md §Stage 4 and
docs/superpowers/specs/2026-05-27-stage-4-send-invoice-workflow-design.md:

  Runner.run(agent)  →  evaluate_policy  →  wait_condition(approved)
       │                       │                     │
       │                       │                     ▼
       │                       │              execute_send → audit_log
       │                       │                     │
       │                       └────────── audit_log (rejected) → END
       │                                              │
       └────────── (timeout / declined) ─────► audit_log (declined) → END

Two interop rules from the build-plan are explicit here:

* ``execute_send`` is a workflow-step activity. It is NEVER exposed to
  the agent as ``activity_as_tool``; doing so would let the agent send
  before the human signal arrives. (interop rule 1)
* ``audit_log`` writes carry a monotonic ``sequence_no`` allocated by
  the workflow. Activity retries collide on the UNIQUE constraint and
  ``ON CONFLICT DO NOTHING`` makes them idempotent. (interop rule 3)
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.contrib.openai_agents.workflow import stateful_mcp_server
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from agents import Runner

    from workflows.send_invoice.activities import (
        AuditEvent,
        EvaluatePolicyInput,
        ExecuteSendInput,
        audit_log,
        evaluate_policy,
        execute_send,
    )
    from workflows.send_invoice.agents import build_main_agent
    from workflows.send_invoice.types import (
        ApprovalDecision,
        SendInvoiceRequest,
        WorkflowResult,
    )

# Retry-once on policy decisions — by definition non-retryable. The
# engine itself (infra / config failures) gets the default retry.
_POLICY_DECISION_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="SendInvoiceWorkflow")
class SendInvoiceWorkflow:
    """Durable single-invoice send workflow."""

    def __init__(self) -> None:
        self._approval: ApprovalDecision | None = None
        # Deterministic monotonic counter for audit rows. Workflow code is
        # replay-deterministic, so a retried activity that re-allocates a
        # number sees the same value as the original attempt.
        self._next_seq = 0

    @workflow.signal(name="approve")
    async def approve(self, decision: ApprovalDecision) -> None:
        """First signal wins. Duplicates are logged and ignored.

        At Stage 4 we don't model "approver changes mind" or "multiple
        approvers race" — those belong to Stage 5's
        ``dual_control_above_threshold``. Logging duplicates keeps them
        visible for later debugging.
        """
        if self._approval is not None:
            await self._audit(
                phase="pre_execute",
                event_kind="duplicate_approval_signal",
                payload={"received": decision.model_dump()},
            )
            return
        self._approval = decision

    @workflow.run
    async def run(self, req: SendInvoiceRequest) -> WorkflowResult:
        run_id = workflow.info().workflow_id

        # ---- 1. agent loop ---------------------------------------------------
        # The plugin's stateful MCP wrapper keeps one MCP subprocess warm
        # for the whole agent loop; without it every tool call would spawn
        # a fresh subprocess.
        async with stateful_mcp_server("bank") as bank:
            agent = build_main_agent(bank)
            result = await Runner.run(agent, input=req.user_message, max_turns=10)

        proposal = result.final_output
        if proposal is None:
            await self._audit(
                phase="pre_action_proposal",
                event_kind="agent_no_output",
                payload={"user_message": req.user_message},
            )
            return WorkflowResult(
                outcome="policy_rejected",
                detail="Agent returned no structured proposal.",
            )

        # ---- 2. policy gate (Stage 4 stub: always permits) -------------------
        try:
            policy = await workflow.execute_activity(
                evaluate_policy,
                EvaluatePolicyInput(
                    workflow_run_id=run_id,
                    sequence_no=self._allocate_seq(),
                    proposal=proposal.model_dump(),
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_POLICY_DECISION_RETRY,
            )
        except ApplicationError as e:
            # Stage 5 will distinguish PolicyDecisionError (non-retryable
            # → block / escalate) from PolicyEngineError / PolicyInfraError
            # (retryable). At Stage 4 nothing throws PolicyDecisionError
            # so we land here only on a real failure — emit audit and end.
            await self._audit(
                phase="pre_action_proposal",
                event_kind="policy_engine_failure",
                payload={"error": str(e)},
                decision="block",
            )
            return WorkflowResult(outcome="policy_rejected", detail=str(e))

        if not policy.permit:
            await self._audit(
                phase="pre_action_proposal",
                event_kind="policy_rejected",
                payload={"rule_ids_fired": list(policy.rule_ids_fired)},
                decision="block",
            )
            return WorkflowResult(
                outcome="policy_rejected",
                detail=f"Rules fired: {', '.join(policy.rule_ids_fired)}",
            )

        # ---- 3. human approval wait -----------------------------------------
        try:
            await workflow.wait_condition(
                lambda: self._approval is not None,
                timeout=timedelta(seconds=req.approval_timeout_seconds),
            )
        except TimeoutError:
            await self._audit(
                phase="pre_execute",
                event_kind="declined",
                payload={"reason": "approval_timeout"},
                decision="block",
            )
            return WorkflowResult(outcome="timeout", detail="No approval within window.")

        approval = self._approval
        assert approval is not None  # narrowing for type checker
        await self._audit(
            phase="pre_execute",
            event_kind="approval_signal",
            payload={"approval": approval.model_dump()},
            decision="permit" if approval.approved else "block",
            actor={"user_id": approval.approver_id, "auth_method": "demo_cli"},
        )

        if not approval.approved:
            await self._audit(
                phase="pre_execute",
                event_kind="declined",
                payload={"notes": approval.notes},
                decision="block",
                actor={"user_id": approval.approver_id, "auth_method": "demo_cli"},
            )
            return WorkflowResult(outcome="declined", detail=approval.notes)

        # ---- 4. side effect -------------------------------------------------
        invoice_id = await workflow.execute_activity(
            execute_send,
            ExecuteSendInput(
                workflow_run_id=run_id,
                proposal=proposal.model_dump(),
                approval=approval.model_dump(),
            ),
            start_to_close_timeout=timedelta(seconds=30),
        )

        # ---- 5. final audit -------------------------------------------------
        await self._audit(
            phase="audit_validation",
            event_kind="executed",
            payload={"invoice_id": invoice_id, "total_cents": proposal.total_cents},
            decision="permit",
            actor={"user_id": approval.approver_id, "auth_method": "demo_cli"},
        )
        return WorkflowResult(outcome="sent", invoice_id=invoice_id)

    # ---- helpers --------------------------------------------------------

    def _allocate_seq(self) -> int:
        self._next_seq += 1
        return self._next_seq

    async def _audit(
        self,
        *,
        phase: str,
        event_kind: str,
        payload: dict[str, object],
        decision: str | None = None,
        rule_id: str | None = None,
        actor: dict[str, object] | None = None,
    ) -> None:
        await workflow.execute_activity(
            audit_log,
            AuditEvent(
                workflow_run_id=workflow.info().workflow_id,
                sequence_no=self._allocate_seq(),
                phase=phase,
                event_kind=event_kind,
                payload=payload,
                decision=decision,
                rule_id=rule_id,
                actor=actor,
            ),
            start_to_close_timeout=timedelta(seconds=10),
        )
