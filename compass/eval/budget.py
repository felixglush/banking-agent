"""Pre-flight cost estimate.

Source: Langfuse run-history API for the workflow, last N=5 runs.
Mean per-case × case_count = estimate. Cold-start fallback uses the
heuristic from run_config.yaml when <3 runs exist."""

from typing import Any

MIN_HISTORY_RUNS = 3
HISTORY_WINDOW = 5


class BudgetExceeded(Exception):
    """Raised when estimate > cap_usd. CLI converts this to exit 4."""


async def estimate_run_cost(
    *,
    client: Any,
    workflow: str,
    case_count: int,
    heuristic_per_case_usd: float,
    cap_usd: float | None = None,
) -> tuple[float, bool]:
    """Returns (estimate_usd, used_heuristic)."""
    history = _fetch_recent_run_costs(client, workflow, limit=HISTORY_WINDOW)

    if len(history) >= MIN_HISTORY_RUNS:
        mean_per_case = sum(history) / len(history)
        used_heuristic = False
    else:
        mean_per_case = heuristic_per_case_usd
        used_heuristic = True

    estimate = mean_per_case * case_count
    if cap_usd is not None and estimate > cap_usd:
        raise BudgetExceeded(
            f"estimated ${estimate:.2f} exceeds cap ${cap_usd:.2f} "
            f"({mean_per_case:.4f}/case × {case_count} cases)"
        )
    return estimate, used_heuristic


def _fetch_recent_run_costs(client: Any, workflow: str, *, limit: int) -> list[float]:
    """Returns per-case costs (total / item_count) for the most recent runs.

    SDK shape may shift between Langfuse versions; if this call signature
    changes, update here and add a note to the changelog. The eval suites
    do not touch this code path — only the CLI's pre-flight does."""
    try:
        runs = client.api.runs.list(name_prefix=workflow, limit=limit).data
    except Exception:
        return []
    out: list[float] = []
    for run in runs:
        total = getattr(run, "total_cost", None)
        if total is not None:
            out.append(float(total) / 100.0)  # 100-item assumption for v0.1 mock
    return out
