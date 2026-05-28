"""``SendInvoiceWorkflow`` — Stage 6: scope gate added at workflow entry.

Per docs/build-plan.md §Stage 6 and
docs/superpowers/specs/2026-05-27-stage-6-intent-classifier-design.md:

  Runner.run(scope_gate)  →  evaluate_policy(input_validation)
       │                              │
       │                       (out_of_scope)
       │                              │
       │                              ▼
       │                       audit_log(unsupported, is_terminal_event=True)
       │                              │
       │                              ▼
       │                            END
       │  (send_invoice)
       ▼
  Runner.run(main_agent)  →  build context  →  evaluate_policy(pre_action_proposal)
       │                                                │
       │                                                ▼
       │                                       wait_condition(approved)
       │                                                │
       │                                                ▼
       │                              evaluate_policy(pre_execute)
       │                                                │
       │                                                ▼
       │                                           execute_send
       │                                                │
       │                                                ▼
       │                                           audit_log
       │                                           (is_terminal_event=True)
       │
       └────────── any block / decline / timeout → audit_log → END
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.contrib.openai_agents.workflow import stateful_mcp_server
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.workflow import ActivityConfig

with workflow.unsafe.imports_passed_through():
    from agents import Runner

    from compass.policy import Actor, AuditPayload, Phase, ToolCallRecord
    from workflows.send_invoice.activities import (
        AuditEvent,
        EvaluatePolicyInput,
        ExecuteSendInput,
        audit_log,
        evaluate_policy,
        execute_send,
    )
    from workflows.send_invoice.agents import build_main_agent
    from workflows.send_invoice.context import (
        extract_reasoning_text,
        extract_tool_calls,
        hash_proposal,
        project_resolved_entities,
    )
    from workflows.send_invoice.scope_gate import build_scope_gate_agent
    from workflows.send_invoice.types import (
        ApprovalDecision,
        SendInvoiceRequest,
        WorkflowResult,
    )

_POLICY_DECISION_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="SendInvoiceWorkflow")
class SendInvoiceWorkflow:
    def __init__(self) -> None:
        self._approval: ApprovalDecision | None = None
        self._next_seq = 0
        self._proposal_hash: str | None = None
        self._policy_hash: str | None = None
        self._tool_calls: list[ToolCallRecord] = []
        self._reasoning_text: str = ""

    @workflow.signal(name="approve")
    async def approve(self, decision: ApprovalDecision) -> None:
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

        # ---- 0. scope gate -------------------------------------------------
        scope_agent = build_scope_gate_agent()
        gate_result = await Runner.run(
            scope_agent,
            input=req.user_message,
            max_turns=1,
        )
        classification = gate_result.final_output
        if classification is None:
            await self._audit(
                phase="input_validation",
                event_kind="agent_no_output",
                payload={"user_message": req.user_message},
                decision="block",
            )
            return WorkflowResult(
                outcome="unsupported",
                detail="Scope gate returned no structured classification.",
            )

        input_ctx = {
            "user_message": req.user_message,
            "classification": classification.model_dump(),
            "workflow_run_id": run_id,
        }
        try:
            payload = await workflow.execute_activity(
                evaluate_policy,
                EvaluatePolicyInput(
                    workflow_run_id=run_id,
                    starting_sequence_no=self._next_seq + 1,
                    phase=Phase.input_validation.value,
                    context=input_ctx,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_POLICY_DECISION_RETRY,
            )
        except ActivityError as e:
            cause = e.cause if isinstance(e.cause, ApplicationError) else None
            err_type = cause.type if cause else None
            # One rule at this phase today; bump conservatively past
            # any sink writes the activity made before raising.
            self._next_seq += 2
            await self._audit(
                phase="input_validation",
                event_kind="unsupported",
                payload={
                    "user_message": req.user_message,
                    "classification": classification.model_dump(),
                    "error_type": err_type,
                    "message": str(e),
                },
                decision="block",
            )
            return WorkflowResult(outcome="unsupported", detail=str(e))

        self._policy_hash = payload.policy_hash
        self._next_seq = payload.next_sequence_no - 1

        await self._audit(
            phase="input_validation",
            event_kind="intent_classified",
            payload={
                "user_message": req.user_message,
                "classification": classification.model_dump(),
            },
            decision="permit",
        )

        # ---- 1. agent loop --------------------------------------------------
        async with stateful_mcp_server(
            "bank",
            config=ActivityConfig(
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            ),
        ) as bank:
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

        # ---- 2. build policy context (pure workflow code) -------------------
        self._tool_calls = extract_tool_calls(result)
        self._reasoning_text = extract_reasoning_text(result)
        resolved_entities = project_resolved_entities(self._tool_calls)
        self._proposal_hash = hash_proposal(proposal.model_dump())

        proposal_ctx = {
            "proposal": proposal.model_dump(),
            "resolved_entities": resolved_entities,
            "tool_calls": self._tool_calls,
            "reasoning_text": self._reasoning_text,
            "workflow_run_id": run_id,
        }

        # ---- 3. pre_action_proposal policy gate -----------------------------
        try:
            payload = await workflow.execute_activity(
                evaluate_policy,
                EvaluatePolicyInput(
                    workflow_run_id=run_id,
                    starting_sequence_no=self._next_seq + 1,
                    phase=Phase.pre_action_proposal.value,
                    context=proposal_ctx,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_POLICY_DECISION_RETRY,
            )
        except ActivityError as e:
            # Temporal wraps the activity's ApplicationError in ActivityError;
            # unwrap to read the type the activity assigned.
            cause = e.cause if isinstance(e.cause, ApplicationError) else None
            err_type = cause.type if cause else None
            # The activity may have reserved sequence numbers for rule events
            # it wrote before raising; advance past them conservatively.
            self._next_seq += 12  # at most 12 rules in the policy at stage 5
            await self._audit(
                phase="pre_action_proposal",
                event_kind="policy_rejected",
                payload={"error_type": err_type, "message": str(e)},
                decision="block",
            )
            return WorkflowResult(outcome="policy_rejected", detail=str(e))

        self._policy_hash = payload.policy_hash
        self._next_seq = payload.next_sequence_no - 1  # advance to last used

        # ---- 4. human approval wait -----------------------------------------
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
        assert approval is not None
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

        # ---- 5. pre_execute policy gate -------------------------------------
        pre_exec_ctx = {
            **proposal_ctx,
            "approval": approval.model_dump(),
            "proposal_hash_at_proposal": self._proposal_hash,
            "policy_hash_at_proposal": self._policy_hash,
        }
        try:
            payload = await workflow.execute_activity(
                evaluate_policy,
                EvaluatePolicyInput(
                    workflow_run_id=run_id,
                    starting_sequence_no=self._next_seq + 1,
                    phase=Phase.pre_execute.value,
                    context=pre_exec_ctx,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_POLICY_DECISION_RETRY,
            )
            self._next_seq = payload.next_sequence_no - 1
        except ActivityError as e:
            cause = e.cause if isinstance(e.cause, ApplicationError) else None
            err_type = cause.type if cause else None
            self._next_seq += 4  # at most 2 pre_execute rules
            await self._audit(
                phase="pre_execute",
                event_kind="policy_rejected",
                payload={"error_type": err_type, "message": str(e)},
                decision="block",
                actor={"user_id": approval.approver_id, "auth_method": "demo_cli"},
            )
            return WorkflowResult(outcome="policy_rejected", detail=str(e))

        # ---- 6. side effect -------------------------------------------------
        invoice_id = await workflow.execute_activity(
            execute_send,
            ExecuteSendInput(
                workflow_run_id=run_id,
                proposal=proposal.model_dump(),
                approval=approval.model_dump(),
            ),
            start_to_close_timeout=timedelta(seconds=30),
        )

        # ---- 7. terminal audit row with audit_validation --------------------
        await self._audit(
            phase="audit_validation",
            event_kind="executed",
            payload={
                "invoice_id": invoice_id,
                "total_cents": proposal.total_cents,
            },
            decision="permit",
            actor={"user_id": approval.approver_id, "auth_method": "demo_cli"},
            is_terminal_event=True,
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
        payload: AuditPayload,
        decision: str | None = None,
        rule_id: str | None = None,
        actor: Actor | None = None,
        is_terminal_event: bool = False,
    ) -> None:
        seq = self._allocate_seq()
        await workflow.execute_activity(
            audit_log,
            AuditEvent(
                workflow_run_id=workflow.info().workflow_id,
                sequence_no=seq,
                phase=phase,
                event_kind=event_kind,
                payload=payload,
                decision=decision,
                rule_id=rule_id,
                actor=actor,
                is_terminal_event=is_terminal_event,
                policy_hash_for_validation=self._policy_hash,
                tool_calls_for_validation=self._tool_calls,
                reasoning_text_for_validation=self._reasoning_text,
            ),
            start_to_close_timeout=timedelta(seconds=15),
        )
        # Audit_validation may have written extra rule_fired rows past
        # our allocated seq. Advance our counter past them to avoid
        # collisions on the next write.
        if is_terminal_event:
            # Conservative bump — at Stage 5 there are 2 audit_validation
            # rules; even if both fire we won't collide. Production-grade
            # counter sync would have the activity return the new tip.
            self._next_seq += 4
