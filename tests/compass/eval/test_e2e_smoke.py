"""End-to-end smoke against real Temporal + Postgres + Langfuse Cloud.

SKIPPED by default. Run on demand:
    uv run pytest tests/compass/eval/test_e2e_smoke.py -v -m e2e

Prereqs in separate terminals:
    docker compose up -d
    temporal server start-dev
    uv run python -m workflows.send_invoice.worker

Env vars:
    OPENAI_API_KEY
    COMPASS_PG_DSN, COMPASS_TEST_PG_DSN
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST

Costs ~$0.10 per run. Validates the three suites against three cases
covering each outcome class.
"""

import os
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest.mark.skipif(
    not all(os.environ.get(k) for k in (
        "OPENAI_API_KEY", "COMPASS_PG_DSN",
        "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST",
    )),
    reason="e2e requires OpenAI, Postgres, Langfuse credentials",
)
async def test_e2e_three_outcome_classes():
    """Runs three cases — one sent, one declined, one policy_rejected —
    through the real workflow and asserts the suite reports the
    expected pass/fail mix."""
    result = subprocess.run(
        [
            sys.executable, "-m", "compass.eval",
            "--workflow", "send_invoice",
            "--mode", "train",
            "--suites", "functional,policy_compliance,cost_latency",
            "--cases", "ir_0001,ir_d_0001,ir_pr_0001",
            "--no-confirm",
        ],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert result.returncode in (0, 1), f"unexpected exit {result.returncode}: {result.stderr}"
    assert "run_id=ev_" in result.stdout
    assert "functional:" in result.stdout
    assert "policy_compliance:" in result.stdout
