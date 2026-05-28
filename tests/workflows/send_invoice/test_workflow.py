"""Workflow-level orchestration tests for ``SendInvoiceWorkflow``.

These tests do NOT exercise OpenAI, MCP, or any real reasoning. The
agent is wired with a ``TestModel`` whose response is a canned JSON
``InvoiceProposal``. The point is to lock in the orchestration: did
each activity fire, in the right order, with the right idempotency
guarantees, ending in the right ``WorkflowResult``.

Stage 5: the in-process ``TestModel`` never calls MCP tools, so
``resolved_entities`` would be empty and the policy gate would block
every run. Real policy behavior is covered by
``test_workflow_policy.py``; these orchestration tests bypass the
policy engine via ``COMPASS_POLICY_DISABLE=1`` to keep the focus on
activity ordering / signals / timeout.
"""

import json
import os
import uuid
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row
from temporalio.client import Client
from temporalio.contrib.openai_agents.testing import ResponseBuilders, TestModel
from temporalio.worker import Worker

from tests.workflows.send_invoice.conftest import TASK_QUEUE, proposal_dict
from workflows.send_invoice.types import (
    ApprovalDecision,
    SendInvoiceRequest,
    WorkflowResult,
)
from workflows.send_invoice.workflow import SendInvoiceWorkflow


@pytest.fixture(autouse=True)
def _disable_policy_for_orchestration_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage-5 policy engine blocks runs where the agent didn't query MCP.
    These tests skip the policy gate to keep their focus on orchestration."""
    monkeypatch.setenv("COMPASS_POLICY_DISABLE", "1")


_DEFAULT_CLASSIFICATION = {
    "intent": "send_invoice",
    "confidence": 0.95,
    "rationale": "User asked to send an invoice.",
}


def _proposal_response(
    proposal: dict[str, Any] | None = None,
    classification: dict[str, Any] | None = None,
) -> TestModel:
    """Two-shot fake model: scope-gate classification, then proposal.

    Stage 6 inserted ``Runner.run(scope_gate_agent, ...)`` before the
    main agent runs. Each Runner.run call consumes one model response;
    the SDK parses each response against the calling agent's
    ``output_type`` (IntentClassification first, InvoiceProposal
    second), so order matters.
    """
    payload = proposal if proposal is not None else proposal_dict()
    cls = classification if classification is not None else _DEFAULT_CLASSIFICATION
    responses = iter([json.dumps(cls), json.dumps(payload)])
    return TestModel(lambda: ResponseBuilders.output_message(next(responses)))


def _new_workflow_id() -> str:
    return f"test-send-invoice-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def model() -> TestModel:
    return _proposal_response()


async def test_happy_path_writes_invoice_and_full_audit(
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001 — used for side effect
) -> None:
    workflow_id = _new_workflow_id()
    handle = await temporal_client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message="invoice Acme for last quarter"),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    await handle.signal(
        SendInvoiceWorkflow.approve,
        ApprovalDecision(approved=True, approver_id="alice"),
    )
    result: WorkflowResult = await handle.result()
    assert result.outcome == "sent"
    assert result.invoice_id == f"inv-{workflow_id}"

    rows = await _fetch_audit(workflow_id)
    kinds = [r["event_kind"] for r in rows]
    # With COMPASS_POLICY_DISABLE=1 the policy activity emits no audit
    # rule rows. The workflow's own _audit calls remain: the scope-gate
    # intent_classified row at workflow entry, then approval_signal,
    # then the terminal executed row.
    assert kinds == ["intent_classified", "approval_signal", "executed"]
    assert [r["phase"] for r in rows] == [
        "input_validation",
        "pre_execute",
        "audit_validation",
    ]
    assert rows[0]["payload"]["classification"]["intent"] == "send_invoice"
    assert rows[1]["actor"] == {"user_id": "alice", "auth_method": "demo_cli"}
    assert rows[2]["actor"] == {"user_id": "alice", "auth_method": "demo_cli"}

    invoice = await _fetch_invoice(workflow_id)
    assert invoice is not None
    assert invoice["customer_id"] == "cust_alpha"
    assert invoice["total_cents"] == 80000
    assert invoice["status"] == "sent"
    line_items = await _fetch_invoice_line_items(workflow_id)
    assert len(line_items) == 1
    assert line_items[0]["description"] == "Solutions Architect time"


async def test_declined_records_audit_and_skips_send(
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001
) -> None:
    workflow_id = _new_workflow_id()
    handle = await temporal_client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message="invoice Acme for last quarter"),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    await handle.signal(
        SendInvoiceWorkflow.approve,
        ApprovalDecision(approved=False, approver_id="alice", notes="scope mismatch"),
    )
    result: WorkflowResult = await handle.result()
    assert result.outcome == "declined"
    assert result.invoice_id is None
    rows = await _fetch_audit(workflow_id)
    kinds = [r["event_kind"] for r in rows]
    assert kinds == ["intent_classified", "approval_signal", "declined"]
    # never reached execute_send → no row in invoices
    invoice = await _fetch_invoice(workflow_id)
    assert invoice is None


async def test_approval_timeout_records_audit_and_skips_send(
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001
) -> None:
    workflow_id = _new_workflow_id()
    handle = await temporal_client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(
            user_message="invoice Acme for last quarter",
            approval_timeout_seconds=60,
        ),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    # time-skipping env advances time when no work is pending — the
    # wait_condition will trip without us sleeping in real time.
    result: WorkflowResult = await handle.result()
    assert result.outcome == "timeout"
    assert result.invoice_id is None
    rows = await _fetch_audit(workflow_id)
    assert [r["event_kind"] for r in rows] == ["intent_classified", "declined"]
    assert rows[1]["payload"] == {"reason": "approval_timeout"}


async def test_duplicate_signal_is_logged_and_ignored(
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001
) -> None:
    workflow_id = _new_workflow_id()
    handle = await temporal_client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message="invoice Acme for last quarter"),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    await handle.signal(
        SendInvoiceWorkflow.approve,
        ApprovalDecision(approved=True, approver_id="alice"),
    )
    # second signal lands after the first; should not override the outcome
    await handle.signal(
        SendInvoiceWorkflow.approve,
        ApprovalDecision(approved=False, approver_id="bob", notes="too late"),
    )
    result: WorkflowResult = await handle.result()
    assert result.outcome == "sent"
    rows = await _fetch_audit(workflow_id)
    duplicates = [r for r in rows if r["event_kind"] == "duplicate_approval_signal"]
    assert len(duplicates) == 1
    assert duplicates[0]["payload"]["received"]["approver_id"] == "bob"


# ---------------------------------------------------------------------
# DB helpers — direct queries against the same compass_test database
# the workflow's activities just wrote to.
# ---------------------------------------------------------------------


def _dsn() -> str:
    return os.environ["COMPASS_PG_DSN"]


async def _fetch_audit(workflow_run_id: str) -> list[dict[str, Any]]:
    async with (
        await psycopg.AsyncConnection.connect(_dsn()) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            """
            SELECT workflow_run_id, sequence_no, phase, event_kind, rule_id,
                   policy_hash, decision, actor, payload
            FROM audit_log
            WHERE workflow_run_id = %s
            ORDER BY sequence_no
            """,
            (workflow_run_id,),
        )
        return await cur.fetchall()


async def _fetch_invoice(workflow_run_id: str) -> dict[str, Any] | None:
    async with (
        await psycopg.AsyncConnection.connect(_dsn()) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            "SELECT * FROM invoices WHERE id = %s",
            (f"inv-{workflow_run_id}",),
        )
        return await cur.fetchone()


async def _fetch_invoice_line_items(workflow_run_id: str) -> list[dict[str, Any]]:
    async with (
        await psycopg.AsyncConnection.connect(_dsn()) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            "SELECT * FROM invoice_line_items WHERE invoice_id = %s ORDER BY line_no",
            (f"inv-{workflow_run_id}",),
        )
        return await cur.fetchall()
