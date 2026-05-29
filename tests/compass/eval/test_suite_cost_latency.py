"""Cost/latency suite behavior."""

from unittest.mock import MagicMock

import pytest

from compass.eval.suites.cost_latency import score_cost_latency
from compass.eval.types import Case, CaseResult

pytestmark = pytest.mark.asyncio


def _ctx(trace_id: str | None = None) -> tuple[Case, CaseResult]:
    case = Case(case_id="ir_0001", request="x", expected_outcome="sent",
                expected={}, expected_fired_rules=[], expected_decline_reason=None)
    result = CaseResult(case_id="ir_0001", workflow_run_id="wf-1",
                        outcome="sent", invoice_id="inv-1", detail=None,
                        trace_id=trace_id)
    return case, result


async def test_passthrough_with_trace():
    client = MagicMock()
    trace = MagicMock(total_cost=0.04, latency=1.82, total_tokens=2456)
    client.api.trace.list = MagicMock(return_value=MagicMock(data=[trace]))
    case, result = _ctx()
    score = await score_cost_latency(case=case, result=result, langfuse_client=client)
    assert score.passed is True
    assert "0.04" in score.comment
    assert "tokens=2456" in score.comment
    assert "latency_ms=1820" in score.comment
    # No deterministic trace_id → fell back to the wf:<workflow_run_id> tag.
    assert client.api.trace.list.call_args.kwargs["tags"] == ["wf:wf-1"]


async def test_deterministic_trace_id_uses_direct_get():
    client = MagicMock()
    client.api.trace.get = MagicMock(
        return_value=MagicMock(total_cost=0.04, latency=1.82, total_tokens=2456)
    )
    client.api.trace.list = MagicMock(return_value=MagicMock(data=[]))
    case, result = _ctx(trace_id="t_deadbeef")
    score = await score_cost_latency(case=case, result=result, langfuse_client=client)
    assert score.passed is True
    assert "tokens=2456" in score.comment
    client.api.trace.get.assert_called_once_with("t_deadbeef")
    client.api.trace.list.assert_not_called()


async def test_no_matching_trace_does_not_fail():
    client = MagicMock()
    client.api.trace.list = MagicMock(return_value=MagicMock(data=[]))
    case, result = _ctx()
    score = await score_cost_latency(case=case, result=result, langfuse_client=client)
    assert score.passed is True
    assert "trace_not_ingested" in score.comment


async def test_list_raises_does_not_fail():
    client = MagicMock()
    client.api.trace.list = MagicMock(side_effect=Exception("server down"))
    case, result = _ctx()
    score = await score_cost_latency(case=case, result=result, langfuse_client=client)
    assert score.passed is True
    assert "trace_not_ingested" in score.comment
