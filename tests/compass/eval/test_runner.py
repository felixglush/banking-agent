"""Unit test using mocked Temporal client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from compass.eval.runner import TemporalWorkflowRunner
from compass.eval.types import Case

pytestmark = pytest.mark.asyncio


def _case(case_id: str = "ir_0001", outcome: str = "sent") -> Case:
    return Case(
        case_id=case_id,
        request="Send invoice for Acme Corp",
        expected_outcome=outcome,  # type: ignore[arg-type]
        expected={"customer_id": "cust_0001"},
        expected_fired_rules=[],
        expected_decline_reason=None,
    )


async def test_sent_outcome_sends_approve_signal():
    mock_handle = AsyncMock()
    mock_handle.signal = AsyncMock()
    mock_handle.result = AsyncMock(return_value=MagicMock(
        outcome="sent", invoice_id="inv-test", detail=None,
    ))
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    runner = TemporalWorkflowRunner(client=mock_client, task_queue="t")
    result = await runner.run_case(_case())

    mock_handle.signal.assert_called_once()
    args = mock_handle.signal.call_args
    assert args.args[0] == "approve"
    assert args.args[1].approved is True
    assert result.outcome == "sent"


async def test_declined_outcome_sends_decline_signal():
    mock_handle = AsyncMock()
    mock_handle.signal = AsyncMock()
    mock_handle.result = AsyncMock(return_value=MagicMock(
        outcome="declined", invoice_id=None, detail="approver said no",
    ))
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    runner = TemporalWorkflowRunner(client=mock_client, task_queue="t")
    await runner.run_case(_case(outcome="declined"))

    args = mock_handle.signal.call_args
    assert args.args[1].approved is False


async def test_policy_rejected_does_not_send_signal():
    """policy_rejected cases short-circuit before wait_condition; no signal needed."""
    mock_handle = AsyncMock()
    mock_handle.signal = AsyncMock()
    mock_handle.result = AsyncMock(return_value=MagicMock(
        outcome="policy_rejected", invoice_id=None, detail="blocked",
    ))
    mock_client = AsyncMock()
    mock_client.start_workflow = AsyncMock(return_value=mock_handle)

    runner = TemporalWorkflowRunner(client=mock_client, task_queue="t")
    await runner.run_case(_case(outcome="policy_rejected"))

    mock_handle.signal.assert_not_called()
