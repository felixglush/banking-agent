"""Policy compliance suite behavior."""

from unittest.mock import AsyncMock

import pytest

from compass.eval.suites.policy_compliance import score_policy_compliance
from compass.eval.types import Case, CaseResult

pytestmark = pytest.mark.asyncio


def _case(expected_rules: list[str]) -> Case:
    return Case(
        case_id="ir_0001",
        request="x",
        expected_outcome="sent",
        expected={},
        expected_fired_rules=expected_rules,
        expected_decline_reason=None,
    )


def _result() -> CaseResult:
    return CaseResult(
        case_id="ir_0001",
        workflow_run_id="wf-1",
        outcome="sent",
        invoice_id="inv-1",
        detail=None,
    )


async def test_exact_match_passes():
    src = AsyncMock()
    src.rule_ids_fired = AsyncMock(return_value={"A", "B", "C"})
    score = await score_policy_compliance(
        case=_case(["A", "B", "C"]),
        result=_result(),
        rule_fire_source=src,
    )
    assert score.passed is True


async def test_missing_rule_fails_with_detail():
    src = AsyncMock()
    src.rule_ids_fired = AsyncMock(return_value={"A", "B"})
    score = await score_policy_compliance(
        case=_case(["A", "B", "C"]),
        result=_result(),
        rule_fire_source=src,
    )
    assert score.passed is False
    assert "missing" in score.comment
    assert "C" in score.comment


async def test_extra_rule_fails_with_detail():
    src = AsyncMock()
    src.rule_ids_fired = AsyncMock(return_value={"A", "B", "X"})
    score = await score_policy_compliance(
        case=_case(["A", "B"]),
        result=_result(),
        rule_fire_source=src,
    )
    assert score.passed is False
    assert "extra" in score.comment
    assert "X" in score.comment


async def test_empty_observed_when_expected_nonempty_fails():
    src = AsyncMock()
    src.rule_ids_fired = AsyncMock(return_value=set())
    score = await score_policy_compliance(
        case=_case(["A"]),
        result=_result(),
        rule_fire_source=src,
    )
    assert score.passed is False
