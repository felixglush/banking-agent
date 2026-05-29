"""Cost / latency passthrough suite. Always scores 1.0 — the comment
carries the numbers. Optional warning thresholds in run_config.yaml
trigger warning lines in the run summary; never pass/fail.

Trace lookup: when the runner seeded a deterministic trace id
(``result.trace_id``), the suite fetches that trace directly. Otherwise it
falls back to the ``wf:<workflow_run_id>`` tag the worker sets on every
trace (see workflows.send_invoice.activities._enrich_langfuse_trace),
picking the most recent match. Either way it then reads the aggregates.
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
        if result.trace_id is not None:
            # Deterministic id seeded by the runner — direct lookup, no tag scan.
            trace: Any = langfuse_client.api.trace.get(result.trace_id)
        else:
            traces = langfuse_client.api.trace.list(
                tags=[f"wf:{result.workflow_run_id}"],
                limit=1,
            )
            matches = cast(list[Any], getattr(traces, "data", None) or [])
            if not matches:
                return SuiteScore(passed=True, comment="trace_not_ingested")
            trace = matches[0]
    except Exception:
        return SuiteScore(passed=True, comment="trace_not_ingested")

    if trace is None:
        return SuiteScore(passed=True, comment="trace_not_ingested")
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
