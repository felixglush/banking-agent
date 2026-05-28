"""run_eval orchestrator with all dependencies mocked."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from compass.eval.orchestrator import run_eval
from compass.eval.types import Case, CaseResult, Mode, Outcome

pytestmark = pytest.mark.asyncio


def _case(case_id: str, outcome: Outcome = "sent") -> Case:
    return Case(
        case_id=case_id, request="x", expected_outcome=outcome,
        expected={"customer_id": "c1", "contract_id": None, "currency": "USD",
                  "source_type": "rate_card", "total_cents": 100},
        expected_fired_rules=["A"], expected_decline_reason=None,
    )


async def test_runs_each_case_through_each_suite():
    cases = [_case("ir_001"), _case("ir_002")]
    runner = AsyncMock()
    runner.run_case = AsyncMock(side_effect=[
        CaseResult(case_id="ir_001", workflow_run_id="wf-1",
                   outcome="sent", invoice_id="inv-1", detail=None),
        CaseResult(case_id="ir_002", workflow_run_id="wf-2",
                   outcome="sent", invoice_id="inv-2", detail=None),
    ])
    rule_src = AsyncMock()
    rule_src.rule_ids_fired = AsyncMock(return_value={"A"})
    score_sink = AsyncMock()
    score_sink.write_score = AsyncMock()
    eval_store = AsyncMock()
    eval_store.allocate_run = AsyncMock(return_value="ev_test")
    eval_store.finalize = AsyncMock()

    invoice_lookup = AsyncMock(side_effect=[
        {"customer_id": "c1", "contract_id": None, "currency": "USD",
         "source_type": "rate_card", "total_cents": 100},
        {"customer_id": "c1", "contract_id": None, "currency": "USD",
         "source_type": "rate_card", "total_cents": 100},
    ])

    report = await run_eval(
        runner=runner,
        cases=cases,
        suites=["functional", "policy_compliance"],
        mode=Mode.train,
        git_sha="abc123",
        rule_fire_source=rule_src,
        score_sink=score_sink,
        eval_run_store=eval_store,
        langfuse_client=MagicMock(),
        invoice_lookup=invoice_lookup,
        holdout_justification=None,
        host_git_dirty=False,
    )

    assert report.run_id == "ev_test"
    assert report.suite_summaries["functional"].passes == 2
    assert report.suite_summaries["policy_compliance"].passes == 2
    assert score_sink.write_score.await_count == 4


async def test_failures_do_not_abort():
    cases = [_case("ir_001"), _case("ir_002", outcome="sent")]
    runner = AsyncMock()
    runner.run_case = AsyncMock(side_effect=[
        CaseResult(case_id="ir_001", workflow_run_id="wf-1",
                   outcome="policy_rejected", invoice_id=None, detail=None),
        CaseResult(case_id="ir_002", workflow_run_id="wf-2",
                   outcome="sent", invoice_id="inv-2", detail=None),
    ])
    rule_src = AsyncMock()
    rule_src.rule_ids_fired = AsyncMock(return_value={"A"})
    score_sink = AsyncMock()
    score_sink.write_score = AsyncMock()
    eval_store = AsyncMock()
    eval_store.allocate_run = AsyncMock(return_value="ev_test")
    eval_store.finalize = AsyncMock()
    invoice_lookup = AsyncMock(return_value={
        "customer_id": "c1", "contract_id": None, "currency": "USD",
        "source_type": "rate_card", "total_cents": 100,
    })

    report = await run_eval(
        runner=runner, cases=cases, suites=["functional"],
        mode=Mode.train, git_sha="abc",
        rule_fire_source=rule_src, score_sink=score_sink,
        eval_run_store=eval_store, langfuse_client=MagicMock(),
        invoice_lookup=invoice_lookup, holdout_justification=None,
        host_git_dirty=False,
    )
    assert report.suite_summaries["functional"].passes == 1
    assert report.suite_summaries["functional"].fails == 1
