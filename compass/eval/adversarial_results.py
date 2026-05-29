"""Parse Promptfoo's EvaluateSummaryV3 results JSON into the harness's
adversarial case model. Pure; defensive about optional keys (the schema carries
many fields we don't use)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from compass.eval.types import AdversarialCaseResult

_Dict = Mapping[str, Any]


def _named_score(result: _Dict, name: str) -> float:
    named = cast(_Dict, result.get("namedScores") or {})
    if name in named:
        return float(named[name])
    # Fallback: scan per-assertion componentResults for the metric.
    grading = cast(_Dict, result.get("gradingResult") or {})
    for comp in cast("list[_Dict]", grading.get("componentResults") or []):
        assertion = cast(_Dict, comp.get("assertion") or {})
        if assertion.get("metric") == name:
            return float(comp.get("score", 0.0))
    return 0.0


def parse_results(data: _Dict) -> list[AdversarialCaseResult]:
    out: list[AdversarialCaseResult] = []
    for idx, r in enumerate(cast("list[_Dict]", data.get("results") or [])):
        test_case = cast(_Dict, r.get("testCase") or {})
        test_md = cast(_Dict, test_case.get("metadata") or {})
        response = cast(_Dict, r.get("response") or {})
        resp_md = cast(_Dict, response.get("metadata") or {})
        vars_ = cast(_Dict, r.get("vars") or {})
        out.append(
            AdversarialCaseResult(
                case_id=str(r.get("id") or test_md.get("case_id") or f"adv_{idx:04d}"),
                category=str(test_md.get("category", "unknown")),
                attack=str(vars_.get("prompt", "")),
                repelled=bool(r.get("success", False)),
                expected_rule_fired=_named_score(r, "adversarial_policy_fire") >= 1.0,
                trace_id=cast("str | None", resp_md.get("trace_id")),
                workflow_run_id=cast("str | None", resp_md.get("workflow_run_id")),
            )
        )
    return out
