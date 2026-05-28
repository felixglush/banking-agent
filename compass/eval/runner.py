"""Default WorkflowRunner impl: drives the SendInvoiceWorkflow via Temporal."""

from typing import Any
from uuid import uuid4

from compass.eval.types import Case, CaseResult
from workflows.send_invoice.types import (
    ApprovalDecision,
    SendInvoiceRequest,
    WorkflowResult,
)
from workflows.send_invoice.workflow import SendInvoiceWorkflow


class TemporalWorkflowRunner:
    def __init__(self, *, client: Any, task_queue: str) -> None:
        self._client = client
        self._task_queue = task_queue

    async def run_case(self, case: Case) -> CaseResult:
        wfid = f"eval-{case.case_id}-{uuid4().hex[:8]}"
        handle = await self._client.start_workflow(
            SendInvoiceWorkflow.run,
            SendInvoiceRequest(user_message=case.request, approval_timeout_seconds=30),
            id=wfid,
            task_queue=self._task_queue,
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
        result: WorkflowResult = await handle.result()
        return CaseResult(
            case_id=case.case_id,
            workflow_run_id=wfid,
            outcome=result.outcome,
            invoice_id=result.invoice_id,
            detail=result.detail,
        )
