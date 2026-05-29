"""Unit and integration tests for TemporalWorkflowRunner.run_probe."""

import os
from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from compass.eval.runner import TemporalWorkflowRunner
from tests.workflows.send_invoice.conftest import TASK_QUEUE
from workflows.send_invoice.types import GateSnapshot

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Integration test (requires Temporal test-server + live DB — skipped in CI
# when the test-server binary cannot be downloaded).
# ---------------------------------------------------------------------------


async def _invoice_count(dsn: str) -> int:
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.cursor() as cur:
        await cur.execute("SELECT count(*) FROM invoices")
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_run_probe_permits_without_executing(temporal_client: Client, worker: Worker) -> None:
    dsn = os.environ["COMPASS_PG_DSN"]
    runner = TemporalWorkflowRunner(client=temporal_client, task_queue=TASK_QUEUE)
    probe = await runner.run_probe("invoice Acme for last quarter", probe_id="atk_0001")
    assert probe.gate_decision == "permitted"
    assert probe.proposal is not None
    assert probe.workflow_run_id.startswith("adv-atk_0001-")
    assert await _invoice_count(dsn) == 0


# ---------------------------------------------------------------------------
# Mocked unit tests — run locally without the Temporal test-server.
# ---------------------------------------------------------------------------


async def test_run_probe_permitted_declines_and_returns_proposal() -> None:
    snap = GateSnapshot(status="permitted", proposal={"customer_id": "cust_x", "total_cents": 999})
    mock_handle = AsyncMock()
    mock_handle.query = AsyncMock(return_value=snap)
    mock_handle.signal = AsyncMock()
    mock_handle.result = AsyncMock(return_value=MagicMock())
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    runner = TemporalWorkflowRunner(client=mock_client, task_queue="t")
    probe = await runner.run_probe("attack!", probe_id="atk_0001")

    assert probe.gate_decision == "permitted"
    assert probe.proposal == {"customer_id": "cust_x", "total_cents": 999}
    assert probe.workflow_run_id.startswith("adv-atk_0001-")
    # permitted => declined (approved=False) so nothing executes
    assert mock_handle.signal.call_args.args[1].approved is False


async def test_run_probe_rejected_does_not_signal() -> None:
    snap = GateSnapshot(
        status="policy_rejected", proposal={"customer_id": "ghost"}, detail="customer_must_exist"
    )
    mock_handle = AsyncMock()
    mock_handle.query = AsyncMock(return_value=snap)
    mock_handle.signal = AsyncMock()
    mock_handle.result = AsyncMock(return_value=MagicMock())
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    runner = TemporalWorkflowRunner(client=mock_client, task_queue="t")
    probe = await runner.run_probe("attack!", probe_id="atk_0002")
    assert probe.gate_decision == "policy_rejected"
    mock_handle.signal.assert_not_called()
