"""Activity retry semantics for ``evaluate_policy``.

A transient infra error (a DB blip) is NOT a deterministic policy
decision — re-running can succeed — so it must be retried. The activity
already classifies its ``psycopg.Error`` path as retryable
(``PolicyInfraError``, ``non_retryable=False``); this regression test
guards the call-site retry policy that makes that classification take
effect. The previous ``maximum_attempts=1`` capped attempts at one,
turning the retryable flag into dead code.
"""

import json
import uuid
from typing import Any

import psycopg
import pytest
from temporalio.client import Client
from temporalio.contrib.openai_agents.testing import ResponseBuilders, TestModel
from temporalio.worker import Worker

from tests.workflows.send_invoice.conftest import TASK_QUEUE, proposal_dict
from workflows.send_invoice.types import SendInvoiceRequest, WorkflowResult
from workflows.send_invoice.workflow import (
    _POLICY_ACTIVITY_RETRY,  # pyright: ignore[reportPrivateUsage] — test guards internal retry config
    SendInvoiceWorkflow,
)


def test_policy_activity_retry_allows_infra_retries_but_not_decisions() -> None:
    """The evaluate_policy retry policy must give infra errors more than one
    attempt while never retrying a deterministic policy decision.

    Runs offline (no Temporal server). Guards against regressing to
    ``maximum_attempts=1``, which made the activity's retryable
    ``PolicyInfraError`` classification dead code.
    """
    # 0 = unlimited in Temporal; anything > 1 gives infra blips a second shot.
    assert _POLICY_ACTIVITY_RETRY.maximum_attempts != 1
    # A policy block is deterministic — re-running yields the same block.
    assert "PolicyDecisionError" in (_POLICY_ACTIVITY_RETRY.non_retryable_error_types or [])


_CLASSIFICATION = {
    "intent": "send_invoice",
    "confidence": 0.95,
    "rationale": "User asked to send an invoice.",
}


@pytest.fixture
def model() -> TestModel:
    """Two-shot fake model: scope-gate classification, then proposal."""
    responses = iter([json.dumps(_CLASSIFICATION), json.dumps(proposal_dict())])
    return TestModel(lambda: ResponseBuilders.output_message(next(responses)))


def _new_workflow_id() -> str:
    return f"test-retry-{uuid.uuid4().hex[:8]}"


async def test_transient_infra_error_in_evaluate_policy_is_retried(
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001 — used for side effect
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first DB connect — input_validation's ``evaluate_policy`` — raises
    a transient ``OperationalError``. With retries enabled the activity
    succeeds on a later attempt and the workflow advances to the
    pre_action_proposal gate, which then blocks (the ``TestModel`` queries
    no MCP tools, so ``resolved_entities`` is empty). Outcome
    ``policy_rejected`` — not ``unsupported`` — proves the retry happened:
    a single-attempt policy would have died at input_validation.
    """
    state = {"connects": 0}
    orig_connect = psycopg.AsyncConnection.connect

    async def flaky_connect(*args: Any, **kwargs: Any) -> Any:
        state["connects"] += 1
        if state["connects"] == 1:
            raise psycopg.OperationalError("transient blip")
        return await orig_connect(*args, **kwargs)

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", flaky_connect)

    workflow_id = _new_workflow_id()
    handle = await temporal_client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message="invoice Acme for last quarter"),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    result: WorkflowResult = await handle.result()

    assert result.outcome == "policy_rejected"
