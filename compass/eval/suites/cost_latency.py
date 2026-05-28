"""Cost / latency passthrough suite. Always scores 1.0 — the comment
carries the numbers. Optional warning thresholds in run_config.yaml
trigger warning lines in the run summary; never pass/fail."""

from typing import Any

from compass.eval.suites.functional import SuiteScore
from compass.eval.types import Case, CaseResult


async def score_cost_latency(
    *,
    case: Case,  # noqa: ARG001
    result: CaseResult,
    langfuse_client: Any,
) -> SuiteScore:
    try:
        trace = langfuse_client.api.trace.get(result.workflow_run_id)
    except Exception:
        return SuiteScore(passed=True, comment="trace_not_ingested")

    cost = getattr(trace, "total_cost", None)
    p50 = getattr(trace, "latency_ms_p50", None)
    p95 = getattr(trace, "latency_ms_p95", None)
    tokens = getattr(trace, "total_tokens", None)
    parts: list[str] = []
    if cost is not None:
        parts.append(f"cost_usd={cost:.4f}")
    if tokens is not None:
        parts.append(f"tokens={tokens}")
    if p50 is not None:
        parts.append(f"p50_ms={p50}")
    if p95 is not None:
        parts.append(f"p95_ms={p95}")
    return SuiteScore(passed=True, comment=";".join(parts) or "no_metrics_available")
