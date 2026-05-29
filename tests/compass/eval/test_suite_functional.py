"""Functional suite behavior."""

from typing import Any

import pytest

from compass.eval.suites.functional import score_functional
from compass.eval.types import Case, CaseResult, Outcome

pytestmark = pytest.mark.asyncio


def _case(
    *,
    expected_outcome: Outcome = "sent",
    expected: dict[str, Any] | None = None,
) -> Case:
    return Case(
        case_id="ir_0001",
        request="x",
        expected_outcome=expected_outcome,
        expected=expected
        if expected is not None
        else {
            "customer_id": "c1",
            "contract_id": None,
            "currency": "USD",
            "source_type": "rate_card",
            "total_cents": 1_500_000,
        },
        expected_fired_rules=[],
        expected_decline_reason=None,
    )


def _result(
    *,
    outcome: Outcome = "sent",
    invoice_id: str | None = "inv-1",
) -> CaseResult:
    return CaseResult(
        case_id="ir_0001",
        workflow_run_id="wf-x",
        outcome=outcome,
        invoice_id=invoice_id,
        detail=None,
    )


async def test_outcome_class_mismatch_fails():
    case = _case(expected_outcome="sent")
    result = _result(outcome="policy_rejected")
    score = await score_functional(case=case, result=result, persisted_invoice=None)
    assert score.passed is False
    assert "outcome_class_mismatch" in score.comment


async def test_sent_with_all_fields_matching_passes():
    case = _case()
    result = _result()
    persisted: dict[str, Any] = {
        "customer_id": "c1",
        "contract_id": None,
        "currency": "USD",
        "source_type": "rate_card",
        "total_cents": 1_500_000,
    }
    score = await score_functional(case=case, result=result, persisted_invoice=persisted)
    assert score.passed is True
    assert score.comment == ""


async def test_sent_with_field_mismatch_fails():
    case = _case()
    result = _result()
    persisted: dict[str, Any] = {
        "customer_id": "c1",
        "contract_id": None,
        "currency": "USD",
        "source_type": "rate_card",
        "total_cents": 9_999_999,
    }
    score = await score_functional(case=case, result=result, persisted_invoice=persisted)
    assert score.passed is False
    assert "total_cents" in score.comment


async def test_declined_passes_on_outcome_only():
    case = _case(expected_outcome="declined")
    result = _result(outcome="declined", invoice_id=None)
    score = await score_functional(case=case, result=result, persisted_invoice=None)
    assert score.passed is True


async def test_policy_rejected_passes_on_outcome_only():
    case = _case(expected_outcome="policy_rejected")
    result = _result(outcome="policy_rejected", invoice_id=None)
    score = await score_functional(case=case, result=result, persisted_invoice=None)
    assert score.passed is True
