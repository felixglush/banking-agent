"""Cost / latency passthrough suite. Always scores 1.0 — the comment
carries the numbers. Optional warning thresholds in run_config.yaml
trigger warning lines in the run summary; never pass/fail.

Trace lookup: the workflow's worker tags every Langfuse trace with
``wf:<workflow_run_id>`` (see workflows.send_invoice.activities.
_enrich_langfuse_trace). The suite searches by that tag, picks the
most recent match, then reads its aggregates.
"""

from typing import Any, cast

from compass.eval.suites.functional import SuiteScore
from compass.eval.types import Case, CaseResult


async def score_cost_latency(
    *,
    case: Case,  # noqa: ARG001
    result: CaseResult,
    langfuse_client: Any,
) -> SuiteScore:
    try:
        traces = langfuse_client.api.trace.list(
            tags=[f"wf:{result.workflow_run_id}"],
            limit=1,
        )
    except Exception:
        return SuiteScore(passed=True, comment="trace_not_ingested")

    matches = cast(list[Any], getattr(traces, "data", None) or [])
    if not matches:
        return SuiteScore(passed=True, comment="trace_not_ingested")

    trace: Any = matches[0]
    cost = cast(float | None, getattr(trace, "total_cost", None))
    latency_s = cast(float | None, getattr(trace, "latency", None))
    tokens = cast(int | None, getattr(trace, "total_tokens", None))
    parts: list[str] = []
    if cost is not None:
        parts.append(f"cost_usd={cost:.4f}")
    if tokens is not None:
        parts.append(f"tokens={tokens}")
    if latency_s is not None:
        parts.append(f"latency_ms={int(latency_s * 1000)}")
    return SuiteScore(passed=True, comment=";".join(parts) or "no_metrics_available")
