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

from datetime import timedelta
from typing import Any, Literal
from uuid import uuid4

from langfuse import Langfuse

from compass.eval.types import Case, CaseResult
from workflows.send_invoice.types import (
    ApprovalDecision,
    ClarificationResponse,
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

    async def run_case(self, case: Case) -> CaseResult:
        wfid = f"eval-{case.case_id}-{uuid4().hex[:8]}"
        if self._lf is None:
            result = await self._drive(case, wfid)
            return self._to_case_result(case, wfid, result, trace_id=None)

        trace_id = Langfuse.create_trace_id(seed=wfid)
        with self._lf.start_as_current_observation(
            name=f"eval:{case.case_id}",
            trace_context={"trace_id": trace_id},
            input=case.request,
        ) as span:
            result = await self._drive(case, wfid)
            # Set trace I/O authoritatively from the root observation: Langfuse
            # derives trace input/output from the root, so without this the
            # worker's terminal-event output attribute is overridden to null.
            span.set_trace_io(
                input=case.request,
                output={
                    "outcome": result.outcome,
                    "invoice_id": result.invoice_id,
                    "detail": result.detail,
                },
            )
        return self._to_case_result(case, wfid, result, trace_id=trace_id)

    async def _drive(self, case: Case, wfid: str) -> WorkflowResult:
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
        return await handle.result()

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
