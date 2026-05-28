"""Workflow-level orchestration tests for the Stage-6 scope gate.

The scope-gate sub-agent runs FIRST inside the workflow. These tests
exercise the routing (in-scope routes to the main agent, out-of-scope
short-circuits) and the audit-row shape, with the policy gate live
(no COMPASS_POLICY_DISABLE). Activity-level policy assertions for the
input_validation phase live in test_workflow_policy.py.

Note: the in-scope happy-path workflow with policy live would block at
pre_action_proposal because the in-process TestModel never calls MCP
(so resolved_entities is empty and customer_must_exist fires). That
test case is covered indirectly by test_workflow.py's orchestration
tests (with policy disabled) plus test_workflow_policy.py's direct-
activity input_validation tests; what's new here is the out-of-scope
short-circuit path that ONLY works with policy live.
"""

from __future__ import annotations

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

from tests.workflows.send_invoice.conftest import TASK_QUEUE
from workflows.send_invoice.types import SendInvoiceRequest, WorkflowResult
from workflows.send_invoice.workflow import SendInvoiceWorkflow


def _classification_response(classification: dict[str, Any]) -> TestModel:
    """Single-shot model: returns the classification JSON.

    Out-of-scope workflows short-circuit after the scope gate, so the
    main agent never runs and one response is enough.
    """
    text = json.dumps(classification)
    return TestModel(lambda: ResponseBuilders.output_message(text))


def _new_workflow_id() -> str:
    return f"test-scope-gate-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def model() -> TestModel:
    """Default classifier model: out-of-scope.

    Tests that need a different classification override at parametrize
    time by constructing their own TestModel in the test body.
    """
    return _classification_response(
        {
            "intent": "out_of_scope",
            "confidence": 0.99,
            "rationale": "Weather query is not a billing operation.",
        }
    )


async def test_out_of_scope_short_circuits(
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001 — used for side effect
) -> None:
    """The workflow rejects out_of_scope at input_validation, writes
    rule_fired + terminal unsupported audit rows, and never reaches
    the main agent.
    """
    workflow_id = _new_workflow_id()
    result: WorkflowResult = await temporal_client.execute_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message="What's the weather in SF?"),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    assert result.outcome == "unsupported"
    assert result.invoice_id is None

    rows = await _fetch_audit(workflow_id)
    kinds = [r["event_kind"] for r in rows]
    # rule_fired (from sink, written by evaluate_policy activity) +
    # the workflow's terminal unsupported row.
    assert "rule_fired" in kinds
    assert kinds[-1] == "unsupported"

    fired = [r for r in rows if r["event_kind"] == "rule_fired"]
    assert len(fired) == 1
    assert fired[0]["rule_id"] == "intent_must_be_send_invoice"
    assert fired[0]["phase"] == "input_validation"
    assert fired[0]["decision"] == "block"

    terminal = rows[-1]
    assert terminal["phase"] == "input_validation"
    assert terminal["payload"]["user_message"] == "What's the weather in SF?"
    assert terminal["payload"]["classification"]["intent"] == "out_of_scope"

    # No invoice rows.
    async with (
        await psycopg.AsyncConnection.connect(_dsn()) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute("SELECT count(*) FROM invoices WHERE id = %s", (f"inv-{workflow_id}",))
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == 0


# ---------------------------------------------------------------------
# DB helpers — mirror test_workflow.py
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
