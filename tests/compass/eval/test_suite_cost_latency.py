"""Cost/latency suite behavior."""

from unittest.mock import MagicMock

import pytest

from compass.eval.suites.cost_latency import score_cost_latency
from compass.eval.types import Case, CaseResult

pytestmark = pytest.mark.asyncio


def _ctx() -> tuple[Case, CaseResult]:
    case = Case(case_id="ir_0001", request="x", expected_outcome="sent",
                expected={}, expected_fired_rules=[], expected_decline_reason=None)
    result = CaseResult(case_id="ir_0001", workflow_run_id="wf-1",
                        outcome="sent", invoice_id="inv-1", detail=None)
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
    # Searched by the wf:<workflow_run_id> tag
    assert client.api.trace.list.call_args.kwargs["tags"] == ["wf:wf-1"]


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
