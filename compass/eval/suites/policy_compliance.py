"""Policy compliance suite. Set-equality between expected and observed
rule_ids. Reads observed via RuleFireSource."""

from compass.eval.protocols import RuleFireSource
from compass.eval.suites.functional import SuiteScore
from compass.eval.types import Case, CaseResult


async def score_policy_compliance(
    *,
    case: Case,
    result: CaseResult,
    rule_fire_source: RuleFireSource,
) -> SuiteScore:
    expected = set(case.expected_fired_rules)
    observed = await rule_fire_source.rule_ids_fired(result.workflow_run_id)
    if observed == expected:
        return SuiteScore(passed=True, comment="")
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    return SuiteScore(
        passed=False,
        comment=f"missing:{missing};extra:{extra}",
    )
