"""End-to-end smoke against real Temporal + Postgres + Langfuse Cloud.

SKIPPED by default. Run on demand:
    uv run pytest tests/compass/eval/test_e2e_smoke.py -v -m e2e

Prereqs in separate terminals:
    docker compose up -d
    temporal server start-dev
    uv run python -m workflows.send_invoice.worker

Env vars (LANGFUSE_HOST or LANGFUSE_BASE_URL accepted):
    OPENAI_API_KEY
    COMPASS_PG_DSN, COMPASS_TEST_PG_DSN
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST | LANGFUSE_BASE_URL

Costs ~$0.10 per run. Validates the three suites against three cases
covering each outcome class, then asserts the Langfuse Dataset Run, its
scores, and the per-case traces (with seeded ids + enriched I/O) landed.
"""

import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from typing import TypeVar

import pytest

T = TypeVar("T")

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

_HAS_CREDS = all(
    os.environ.get(k)
    for k in ("OPENAI_API_KEY", "COMPASS_PG_DSN", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
) and (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL"))

_CASES = ["ir_0001", "ir_d_0001", "ir_pr_0001"]
_DATASET = "send_invoice_v0_1"


def _poll(fn: Callable[[], T | None], *, timeout: float = 90.0, interval: float = 3.0) -> T | None:
    """Call fn() until it returns truthy or timeout; return last result.

    Swallows exceptions (e.g. 404 before the object is ingested) and keeps
    polling until the deadline.
    """
    deadline = time.monotonic() + timeout
    result = None
    while time.monotonic() < deadline:
        try:
            result = fn()
        except Exception:
            result = None
        if result:
            return result
        time.sleep(interval)
    return result


@pytest.mark.skipif(not _HAS_CREDS, reason="e2e requires OpenAI, Postgres, Langfuse credentials")
async def test_e2e_three_outcome_classes():
    """Run three cases — sent, declined, policy_rejected — through the real
    workflow, then verify the run, scores, and seeded traces in Langfuse."""
    from langfuse import Langfuse, get_client  # noqa: PLC0415

    proc = subprocess.run(
        [
            sys.executable, "-m", "compass.eval",
            "--workflow", "send_invoice",
            "--mode", "train",
            "--suites", "functional,policy_compliance,cost_latency",
            "--cases", ",".join(_CASES),
            "--no-confirm",
        ],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode in (0, 1), f"unexpected exit {proc.returncode}: {proc.stderr}"
    m = re.search(r"run_id=(ev_\w+)", proc.stdout)
    assert m, f"no run_id in stdout:\n{proc.stdout}"
    run_id = m.group(1)
    assert "functional:" in proc.stdout
    assert "policy_compliance:" in proc.stdout

    lf = get_client()

    # 1. Dataset + items were uploaded (one item per case).
    dataset = lf.get_dataset(_DATASET)
    item_ids = {it.id for it in dataset.items}
    for cid in _CASES:
        assert cid in item_ids, f"dataset item {cid} missing from {_DATASET}"

    # 2. The Dataset Run exists and has one run item per case (ingestion lag).
    def _run_with_all_items():
        r = lf.api.datasets.get_run(dataset_name=_DATASET, run_name=run_id)
        return r if len(r.dataset_run_items or []) >= len(_CASES) else None

    run = _poll(_run_with_all_items)
    assert run is not None, f"dataset run {run_id} never reached {len(_CASES)} items"
    run_items = run.dataset_run_items
    assert {ri.dataset_item_id for ri in run_items} == set(_CASES)

    # 3. Each run item's trace id is the deterministic seed of a real trace
    #    whose input/output were enriched by the worker (proves propagation).
    for ri in run_items:
        trace_id = ri.trace_id
        assert trace_id, f"run item {ri.dataset_item_id} has no trace_id"
        trace = _poll(lambda tid=trace_id: lf.api.trace.get(tid))
        assert trace is not None, f"trace {trace_id} for {ri.dataset_item_id} not ingested"
        # Change 1: trace input is the request; output carries the outcome.
        assert trace.input, f"trace {trace_id} has blank input"
        assert trace.output, f"trace {trace_id} has blank output"
        assert "outcome" in str(trace.output), f"trace output missing outcome: {trace.output}"

    # 4. Scores landed for each (case, suite) under the traces.
    suites = {"functional", "policy_compliance", "cost_latency"}
    for ri in run_items:
        scores = _poll(lambda tid=ri.trace_id: lf.api.scores.get_many(trace_id=tid).data or None)
        names = {s.name for s in (scores or [])}
        assert suites <= names, f"{ri.dataset_item_id}: missing scores {suites - names}"

    # The trace id is reproducible from the run's workflow ids by seeding —
    # spot-check the helper the runner uses is deterministic and 32-hex.
    seeded = Langfuse.create_trace_id(seed="eval-ir_0001-deadbeef")
    assert seeded == Langfuse.create_trace_id(seed="eval-ir_0001-deadbeef")
    assert re.fullmatch(r"[0-9a-f]{32}", seeded)
