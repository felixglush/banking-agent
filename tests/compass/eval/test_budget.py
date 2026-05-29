"""Budget pre-flight."""

from unittest.mock import MagicMock

import pytest

from compass.eval.budget import BudgetExceeded, estimate_run_cost

pytestmark = pytest.mark.asyncio


def _client_with_history(per_case_usds: list[float]) -> MagicMock:
    client = MagicMock()
    client.api = MagicMock()
    client.api.runs = MagicMock()
    client.api.runs.list = MagicMock(
        return_value=MagicMock(
            data=[MagicMock(total_cost=c * 100) for c in per_case_usds],
        )
    )
    return client


async def test_uses_history_when_enough_runs():
    client = _client_with_history([0.04, 0.05, 0.045, 0.038, 0.042])
    estimate, used_heuristic = await estimate_run_cost(
        client=client,
        workflow="send_invoice",
        case_count=100,
        heuristic_per_case_usd=0.30,
    )
    assert abs(estimate - 4.30) < 0.05
    assert used_heuristic is False


async def test_falls_back_to_heuristic_when_history_thin():
    client = _client_with_history([0.04, 0.05])  # < 3 runs
    estimate, used_heuristic = await estimate_run_cost(
        client=client,
        workflow="send_invoice",
        case_count=100,
        heuristic_per_case_usd=0.30,
    )
    assert abs(estimate - 30.00) < 0.01
    assert used_heuristic is True


async def test_budget_exceeded_raises():
    client = _client_with_history([1.00, 1.00, 1.00, 1.00, 1.00])  # $1/case
    with pytest.raises(BudgetExceeded) as exc:
        await estimate_run_cost(
            client=client,
            workflow="send_invoice",
            case_count=100,
            heuristic_per_case_usd=0.30,
            cap_usd=40.00,
        )
    assert "$100" in str(exc.value)
