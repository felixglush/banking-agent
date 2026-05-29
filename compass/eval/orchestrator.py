"""run_eval — top-level entry that wires runner → suites → sinks.

This file is intentionally thin: orchestration only. Scoring logic lives
in suites/, storage in sources/, runner in runner.py.
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from compass.eval.protocols import (
    EvalRunStore,
    RuleFireSource,
    ScoreSink,
    WorkflowRunner,
)
from compass.eval.suites.cost_latency import score_cost_latency
from compass.eval.suites.functional import SuiteScore, score_functional
from compass.eval.suites.policy_compliance import score_policy_compliance
from compass.eval.types import Case, CaseResult, Mode

InvoiceLookup = Callable[[str], Coroutine[Any, Any, dict[str, Any] | None]]


@dataclass
class SuiteSummary:
    passes: int = 0
    fails: int = 0
    failure_details: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])


@dataclass
class EvalReport:
    run_id: str
    mode: Mode
    suite_summaries: dict[str, SuiteSummary]
    case_results: list[CaseResult]


@dataclass
class _CaseOutcome:
    """One case's result, collected concurrently and aggregated in order."""

    case: Case
    result: CaseResult | None
    suite_scores: list[tuple[str, bool, str]]  # (suite, passed, comment)
    error: str | None  # workflow_error message when run_case raised


async def run_eval(
    *,
    runner: WorkflowRunner,
    cases: list[Case],
    suites: list[str],
    mode: Mode,
    git_sha: str,
    rule_fire_source: RuleFireSource,
    score_sink: ScoreSink,
    eval_run_store: EvalRunStore,
    langfuse_client: Any,
    invoice_lookup: InvoiceLookup,
    holdout_justification: str | None,
    host_git_dirty: bool,
    policy_enabled: bool = True,
    concurrency: int = 1,
) -> EvalReport:
    run_id = await eval_run_store.allocate_run(
        git_sha=git_sha,
        mode=mode.value,
        holdout_justification=holdout_justification,
        policy_enabled=policy_enabled,
        suite_names=suites,
        host_git_dirty=host_git_dirty,
    )
    summaries: dict[str, SuiteSummary] = {s: SuiteSummary() for s in suites}
    case_results: list[CaseResult] = []

    # Cases are independent (own workflow id, own trace, commutative score
    # writes), so run up to `concurrency` at once. Per-case scoring + score
    # writes happen inside the coroutine; aggregation into `summaries` is done
    # afterward, in input order, so the report stays deterministic regardless
    # of completion order.
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _process(case: Case) -> _CaseOutcome:
        async with sem:
            try:
                result = await runner.run_case(case)
            except Exception as e:
                comment = f"workflow_error:{type(e).__name__}"
                for s in suites:
                    await score_sink.write_score(
                        run_id=run_id,
                        item_id=case.case_id,
                        name=s,
                        value=0.0,
                        comment=comment,
                    )
                return _CaseOutcome(case=case, result=None, suite_scores=[], error=f"{comment}:{e}")
            persisted = (
                await invoice_lookup(result.invoice_id) if result.invoice_id is not None else None
            )
            suite_scores: list[tuple[str, bool, str]] = []
            for suite in suites:
                score = await _run_suite(
                    suite=suite,
                    case=case,
                    result=result,
                    persisted=persisted,
                    rule_fire_source=rule_fire_source,
                    langfuse_client=langfuse_client,
                )
                await score_sink.write_score(
                    run_id=run_id,
                    item_id=case.case_id,
                    name=suite,
                    value=1.0 if score.passed else 0.0,
                    comment=score.comment or None,
                    trace_id=result.trace_id,
                )
                suite_scores.append((suite, score.passed, score.comment))
            return _CaseOutcome(case=case, result=result, suite_scores=suite_scores, error=None)

    outcomes = await asyncio.gather(*(_process(c) for c in cases))

    for oc in outcomes:
        if oc.error is not None:
            for s in suites:
                summaries[s].fails += 1
                summaries[s].failure_details.append((oc.case.case_id, oc.error))
            continue
        assert oc.result is not None
        case_results.append(oc.result)
        for suite, passed, comment in oc.suite_scores:
            if passed:
                summaries[suite].passes += 1
            else:
                summaries[suite].fails += 1
                summaries[suite].failure_details.append((oc.case.case_id, comment))

    # Run-level aggregate scores: each suite's pass rate, anchored to the
    # dataset run so the Experiments view shows the run's headline
    # performance (per-case scores live on the individual traces).
    for suite, summary in summaries.items():
        total = summary.passes + summary.fails
        rate = summary.passes / total if total else 0.0
        await score_sink.write_run_score(
            run_id=run_id,
            name=suite,
            value=rate,
            comment=f"{summary.passes}/{total} passed",
        )

    await eval_run_store.finalize(run_id)
    return EvalReport(
        run_id=run_id,
        mode=mode,
        suite_summaries=summaries,
        case_results=case_results,
    )


async def _run_suite(
    *,
    suite: str,
    case: Case,
    result: CaseResult,
    persisted: dict[str, Any] | None,
    rule_fire_source: RuleFireSource,
    langfuse_client: Any,
) -> SuiteScore:
    if suite == "functional":
        return await score_functional(case=case, result=result, persisted_invoice=persisted)
    if suite == "policy_compliance":
        return await score_policy_compliance(
            case=case,
            result=result,
            rule_fire_source=rule_fire_source,
        )
    if suite == "cost_latency":
        return await score_cost_latency(
            case=case,
            result=result,
            langfuse_client=langfuse_client,
        )
    raise ValueError(f"unknown suite: {suite}")
