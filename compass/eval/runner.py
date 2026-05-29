"""Default WorkflowRunner impl: drives the SendInvoiceWorkflow via Temporal.

Trace linking: each case's Langfuse trace id is seeded deterministically
from the Temporal workflow id via ``Langfuse.create_trace_id(seed=...)``.
The runner opens a Langfuse root observation carrying that trace id and
runs ``start_workflow`` + approval signal + ``result()`` inside it. Temporal's
client-side OpenTelemetry interceptor injects the active span context into
the workflow headers, so the worker's spans inherit the same trace id. The
harness can then link the trace to its Dataset Run item by recomputing the
id from ``workflow_run_id`` — no tag lookup, no ingestion wait.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Literal, cast
from uuid import uuid4

from langfuse import Langfuse

from compass.eval.types import Case, CaseResult, ProbeResult
from workflows.send_invoice.types import (
    ApprovalDecision,
    ClarificationResponse,
    GateSnapshot,
    SendInvoiceRequest,
    WorkflowResult,
)
from workflows.send_invoice.workflow import SendInvoiceWorkflow


class TemporalWorkflowRunner:
    def __init__(
        self,
        *,
        client: Any,
        task_queue: str,
        langfuse_client: Any | None = None,
        execution_timeout_s: int = 300,
        prompt_variant: Literal["fixed", "legacy"] = "fixed",
        use_invoice_tool: bool = True,
        self_heal_max_attempts: int = 0,
        clarification_timeout_s: int = 30,
    ) -> None:
        self._client = client
        self._task_queue = task_queue
        self._lf = langfuse_client
        self._execution_timeout_s = execution_timeout_s
        self._clarification_timeout_s = clarification_timeout_s
        # Ablation levers, carried into each SendInvoiceRequest.
        self._prompt_variant: Literal["fixed", "legacy"] = prompt_variant
        self._use_invoice_tool = use_invoice_tool
        self._self_heal_max_attempts = self_heal_max_attempts

    @asynccontextmanager
    async def _observe(
        self, *, wfid: str, name: str, input_text: str
    ) -> AsyncGenerator[tuple[str | None, Any], None]:
        """Open the Langfuse root observation that seeds the deterministic trace
        id, yielding (trace_id, span) — or (None, None) when Langfuse is absent.
        Shared by run_case and run_probe (DRY)."""
        if self._lf is None:
            yield None, None
            return
        trace_id = Langfuse.create_trace_id(seed=wfid)
        with self._lf.start_as_current_observation(
            name=name,
            trace_context={"trace_id": trace_id},
            input=input_text,
        ) as span:
            yield trace_id, span

    async def run_case(self, case: Case) -> CaseResult:
        wfid = f"eval-{case.case_id}-{uuid4().hex[:8]}"
        async with self._observe(
            wfid=wfid, name=f"eval:{case.case_id}", input_text=case.request
        ) as (trace_id, span):
            result, activity = await self._drive(case, wfid)
            if span is not None:
                # Set trace I/O authoritatively from the root observation; fold in
                # the agent's tool calls + reasoning (the OpenInference LLM/tool
                # spans orphan into separate traces, so this root observation is the
                # only reliable surface that captures them).
                output: dict[str, Any] = {
                    "outcome": result.outcome,
                    "invoice_id": result.invoice_id,
                    "detail": result.detail,
                }
                tool_calls = activity.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    output["tool_calls"] = tool_calls
                reasoning = activity.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    output["reasoning"] = reasoning
                span.set_trace_io(input=case.request, output=output)
        return self._to_case_result(case, wfid, result, trace_id=trace_id)

    async def _drive(self, case: Case, wfid: str) -> tuple[WorkflowResult, dict[str, Any]]:
        handle = await self._client.start_workflow(
            SendInvoiceWorkflow.run,
            SendInvoiceRequest(
                user_message=case.request,
                approval_timeout_seconds=30,
                prompt_variant=self._prompt_variant,
                use_invoice_tool=self._use_invoice_tool,
                self_heal_max_attempts=self._self_heal_max_attempts,
                # Production waits for clarification indefinitely; the eval
                # bounds it so an over-clarifying agent fails fast rather than
                # hanging until the execution_timeout backstop.
                clarification_timeout_seconds=self._clarification_timeout_s,
            ),
            id=wfid,
            task_queue=self._task_queue,
            # Backstop: no single case may hang the sequential run. A workflow
            # that exceeds this (e.g. an activity stuck retrying) is terminated
            # by Temporal; handle.result() then raises and the harness scores
            # the case as a workflow_error and moves on.
            execution_timeout=timedelta(seconds=self._execution_timeout_s),
        )
        # Ambiguous case: buffer the disambiguating answer so the agent's
        # clarification round-trip can consume it (signals buffer against the
        # workflow id until consumed; unused if the agent never asks).
        if case.clarify_answer is not None:
            await handle.signal(
                "clarify",
                ClarificationResponse(answer=case.clarify_answer, responder_id="eval_harness"),
            )
        if case.expected_outcome in ("sent", "declined"):
            await handle.signal(
                "approve",
                ApprovalDecision(
                    approver_id="eval_harness",
                    approved=(case.expected_outcome == "sent"),
                    notes=f"automated by compass.eval for {case.case_id}",
                ),
            )
        result = await handle.result()
        return result, await self._query_agent_activity(handle)

    @staticmethod
    async def _query_agent_activity(handle: Any) -> dict[str, Any]:
        """Best-effort read of the workflow's tool calls + reasoning for trace
        enrichment. Returns {} on any failure or unexpected shape — trace
        enrichment must never fail the run."""
        try:
            data = await handle.query(SendInvoiceWorkflow.agent_activity)
        except Exception:  # noqa: BLE001 — enrichment is best-effort
            return {}
        return cast("dict[str, Any]", data) if isinstance(data, dict) else {}

    async def run_probe(
        self,
        attack: str,
        *,
        probe_id: str,
        gate_poll_interval_s: float = 0.1,
        gate_deadline_s: float = 120.0,
    ) -> ProbeResult:
        """Drive one adversarial attack to the pre_action_proposal gate, decline
        on permit so nothing executes, and report the gate verdict + proposal.

        Reuses the same client + trace-seeding wrapper as ``run_case`` (DRY);
        differs only in that it reads ``gate_snapshot`` and never approves."""
        wfid = f"adv-{probe_id}-{uuid4().hex[:8]}"
        async with self._observe(wfid=wfid, name=f"adversarial:{probe_id}", input_text=attack) as (
            trace_id,
            _span,
        ):
            handle = await self._client.start_workflow(
                SendInvoiceWorkflow.run,
                SendInvoiceRequest(
                    user_message=attack,
                    approval_timeout_seconds=self._execution_timeout_s,
                    prompt_variant=self._prompt_variant,
                    use_invoice_tool=self._use_invoice_tool,
                    self_heal_max_attempts=self._self_heal_max_attempts,
                    clarification_timeout_seconds=self._clarification_timeout_s,
                ),
                id=wfid,
                task_queue=self._task_queue,
                execution_timeout=timedelta(seconds=self._execution_timeout_s),
            )
            snap = await self._await_gate(handle, gate_poll_interval_s, gate_deadline_s)
            if snap.status == "permitted":
                # Decline so the side effect never fires; the meaningful signal is
                # the gate verdict, not this synthetic decline.
                await handle.signal(
                    SendInvoiceWorkflow.approve,
                    ApprovalDecision(
                        approved=False,
                        approver_id="adversarial-eval",
                        notes="adversarial probe — proposal not executed",
                    ),
                )
            await handle.result()  # drain to completion (declined / terminal)
        return ProbeResult(
            workflow_run_id=wfid,
            trace_id=trace_id,
            gate_decision=snap.status,
            proposal=snap.proposal,
            detail=snap.detail,
        )

    async def _await_gate(
        self, handle: Any, poll_interval_s: float, deadline_s: float
    ) -> GateSnapshot:
        elapsed = 0.0
        while elapsed < deadline_s:
            snap: GateSnapshot = await handle.query(SendInvoiceWorkflow.gate_snapshot)
            if snap.status != "pending":
                return snap
            await asyncio.sleep(poll_interval_s)
            elapsed += poll_interval_s
        return GateSnapshot(status="pending", detail="gate decision timed out")

    @staticmethod
    def _to_case_result(
        case: Case, wfid: str, result: WorkflowResult, *, trace_id: str | None
    ) -> CaseResult:
        return CaseResult(
            case_id=case.case_id,
            workflow_run_id=wfid,
            outcome=result.outcome,
            invoice_id=result.invoice_id,
            detail=result.detail,
            trace_id=trace_id,
        )
