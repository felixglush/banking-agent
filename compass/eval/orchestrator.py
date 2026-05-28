"""run_eval — top-level entry that wires runner → suites → sinks.

This file is intentionally thin: orchestration only. Scoring logic lives
in suites/, storage in sources/, runner in runner.py.
"""

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
) -> EvalReport:
    run_id = await eval_run_store.allocate_run(
        git_sha=git_sha, mode=mode.value,
        holdout_justification=holdout_justification,
        policy_enabled=policy_enabled, suite_names=suites,
        host_git_dirty=host_git_dirty,
    )
    summaries: dict[str, SuiteSummary] = {s: SuiteSummary() for s in suites}
    case_results: list[CaseResult] = []

    for case in cases:
        try:
            result = await runner.run_case(case)
        except Exception as e:
            for s in suites:
                summaries[s].fails += 1
                summaries[s].failure_details.append(
                    (case.case_id, f"workflow_error:{type(e).__name__}:{e}")
                )
                await score_sink.write_score(
                    run_id=run_id, item_id=case.case_id,
                    name=s, value=0.0,
                    comment=f"workflow_error:{type(e).__name__}",
                )
            continue
        case_results.append(result)

        persisted = (
            await invoice_lookup(result.invoice_id)
            if result.invoice_id is not None else None
        )

        for suite in suites:
            score = await _run_suite(
                suite=suite, case=case, result=result,
                persisted=persisted, rule_fire_source=rule_fire_source,
                langfuse_client=langfuse_client,
            )
            if score.passed:
                summaries[suite].passes += 1
            else:
                summaries[suite].fails += 1
                summaries[suite].failure_details.append((case.case_id, score.comment))
            await score_sink.write_score(
                run_id=run_id, item_id=case.case_id,
                name=suite, value=1.0 if score.passed else 0.0,
                comment=score.comment or None,
            )

    await eval_run_store.finalize(run_id)
    return EvalReport(
        run_id=run_id, mode=mode,
        suite_summaries=summaries, case_results=case_results,
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
            case=case, result=result, rule_fire_source=rule_fire_source,
        )
    if suite == "cost_latency":
        return await score_cost_latency(
            case=case, result=result, langfuse_client=langfuse_client,
        )
    raise ValueError(f"unknown suite: {suite}")
