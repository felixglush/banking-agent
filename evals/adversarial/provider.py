"""Promptfoo Python provider for the Stage-8 adversarial eval.

Promptfoo calls ``call_api`` once per generated attack. The provider drives the
attack to the pre_action_proposal gate via the shared
``TemporalWorkflowRunner.run_probe`` (no auto-approve, no side effect) and
returns the proposal/verdict for grading plus metadata for the policy-fire
assertion and Langfuse linkage.

Lives in evals/ (adopter code): imports compass (public API) + temporalio. The
Temporal client/runner is built once per process and cached."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from compass.eval.runner import TemporalWorkflowRunner
from compass.eval.types import ProbeResult

_RUNNER: TemporalWorkflowRunner | None = None
_RUNNER_LOCK = asyncio.Lock()
_PROBE_SEQ = 0


async def _get_runner(config: dict[str, Any]) -> TemporalWorkflowRunner:  # noqa: PLC0415
    """Build (once per process) the Temporal client + runner from config/env."""
    global _RUNNER  # noqa: PLW0603
    if _RUNNER is not None:
        return _RUNNER
    async with _RUNNER_LOCK:
        if _RUNNER is not None:
            return _RUNNER
        from temporalio.client import Client  # noqa: PLC0415 local: optional at test time
        from temporalio.contrib.opentelemetry import OpenTelemetryPlugin  # noqa: PLC0415

        target = config.get("temporal_target") or os.environ.get(
            "TEMPORAL_TARGET", "localhost:7233"
        )
        task_queue = config.get("task_queue") or os.environ.get(
            "ADVERSARIAL_TASK_QUEUE", "send-invoice"
        )
        client = await Client.connect(target, plugins=[OpenTelemetryPlugin()])

        langfuse_client: Any = None
        if os.environ.get("ADVERSARIAL_LANGFUSE", "1") != "0":
            from langfuse import get_client  # noqa: PLC0415

            langfuse_client = get_client()

        _RUNNER = TemporalWorkflowRunner(  # pyright: ignore[reportConstantRedefinition]
            client=client,
            task_queue=task_queue,
            langfuse_client=langfuse_client,
        )
        return _RUNNER


def _render_output(probe: ProbeResult) -> str:
    """Text Promptfoo's grader judges. Permit → show the accepted proposal so the
    grader can decide if it is actually harmful; else state the block."""
    if probe.gate_decision == "permitted":
        return "PROPOSAL ACCEPTED BY POLICY GATE:\n" + json.dumps(probe.proposal or {})
    return f"BLOCKED ({probe.gate_decision}): {probe.detail or ''}".rstrip()


async def call_api(
    prompt: str,
    options: dict[str, Any],
    context: dict[str, Any] | None = None,  # noqa: ARG001 — promptfoo contract
) -> dict[str, Any]:
    global _PROBE_SEQ  # noqa: PLW0603
    _PROBE_SEQ += 1  # pyright: ignore[reportConstantRedefinition]
    runner = await _get_runner(options.get("config", {}))
    probe = await runner.run_probe(prompt, probe_id=f"{_PROBE_SEQ:05d}")
    return {
        "output": _render_output(probe),
        "metadata": {
            "workflow_run_id": probe.workflow_run_id,
            "trace_id": probe.trace_id,
            "gate_decision": probe.gate_decision,
        },
    }
