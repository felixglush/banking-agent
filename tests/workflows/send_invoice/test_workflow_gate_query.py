import asyncio
from typing import Any
from uuid import uuid4

from temporalio.client import Client, WorkflowHandle
from temporalio.worker import Worker

from workflows.send_invoice.types import GateSnapshot, SendInvoiceRequest
from workflows.send_invoice.workflow import SendInvoiceWorkflow

from .conftest import TASK_QUEUE


def _wfid() -> str:
    return f"gatequery-{uuid4().hex[:8]}"


async def _poll_until_decided(handle: WorkflowHandle[Any, Any], deadline_s: float = 10.0) -> str:
    elapsed = 0.0
    while elapsed < deadline_s:
        snap = await handle.query(SendInvoiceWorkflow.gate_snapshot)
        if snap.status != "pending":
            return snap.status
        await asyncio.sleep(0.05)
        elapsed += 0.05
    raise AssertionError("gate never left 'pending'")


async def test_query_reports_permitted_with_proposal(
    temporal_client: Client, worker: Worker
) -> None:
    handle = await temporal_client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message="invoice Acme for last quarter"),
        id=_wfid(),
        task_queue=TASK_QUEUE,
    )
    status = await _poll_until_decided(handle)
    snap = await handle.query(SendInvoiceWorkflow.gate_snapshot)
    assert status == "permitted"
    assert snap.proposal is not None
    assert snap.proposal["customer_id"]


def test_gate_snapshot_query_returns_state() -> None:
    wf = SendInvoiceWorkflow()
    wf._gate = GateSnapshot(status="permitted", proposal={"customer_id": "c1"})  # pyright: ignore[reportPrivateUsage]
    out = wf.gate_snapshot()
    assert out.status == "permitted"
    assert out.proposal == {"customer_id": "c1"}


def test_gate_snapshot_pending_by_default() -> None:
    assert SendInvoiceWorkflow().gate_snapshot().status == "pending"
