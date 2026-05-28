"""Unit tests for the intent_in_allowlist primitive."""

from __future__ import annotations

from typing import Any

import pytest

from compass.policy import InMemorySink, Phase, Rule, evaluate, list_primitives
from compass.policy.primitives.intent import intent_in_allowlist
from compass.policy.types import Violation


def _ctx(intent: str | None) -> dict[str, Any]:
    if intent is None:
        return {"workflow_run_id": "wf-test"}
    return {
        "user_message": "anything",
        "classification": {
            "intent": intent,
            "confidence": 0.9,
            "rationale": "test",
        },
    }


async def _run(allowed: frozenset[str], intent: str | None) -> Violation | None:
    pred = intent_in_allowlist(field="classification.intent", allowed=allowed)
    return await pred(_ctx(intent))


async def test_value_in_allowlist_skips() -> None:
    result = await _run(frozenset({"send_invoice"}), "send_invoice")
    assert result is None


async def test_value_not_in_allowlist_blocks() -> None:
    result = await _run(frozenset({"send_invoice"}), "out_of_scope")
    assert result is not None
    assert result.evidence["value"] == "out_of_scope"
    assert result.evidence["allowed"] == ["send_invoice"]


async def test_missing_field_blocks() -> None:
    result = await _run(frozenset({"send_invoice"}), None)
    assert result is not None
    assert result.evidence["reason"] == "missing"
    assert result.evidence["field"] == "classification.intent"


async def test_multi_class_allowlist_skips() -> None:
    result = await _run(
        frozenset({"send_invoice", "dispute_investigation"}),
        "dispute_investigation",
    )
    assert result is None


def test_primitive_registered() -> None:
    assert "intent_in_allowlist" in list_primitives()


@pytest.mark.parametrize(
    "intent,expected_permit",
    [("send_invoice", True), ("out_of_scope", False)],
)
async def test_engine_routes_through_intent_rule(
    intent: str,
    expected_permit: bool,
) -> None:
    """The full evaluate() loop with the rule actually wired."""
    rules = [
        Rule(
            id="intent_must_be_send_invoice",
            phase=Phase.input_validation,
            predicate=intent_in_allowlist(
                field="classification.intent",
                allowed=frozenset({"send_invoice"}),
            ),
        ),
    ]
    sink = InMemorySink()
    decision = await evaluate(
        rules,
        Phase.input_validation,
        _ctx(intent),
        sink=sink,
    )
    assert decision.permit is expected_permit
    assert len(sink.events) == 1
    assert sink.events[0]["event_kind"] == ("rule_skipped" if expected_permit else "rule_fired")
