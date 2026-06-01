"""Unit tests for the scope-gate classification model (no Temporal).

The workflow-level routing test lives in test_scope_gate.py (needs a live
Temporal server). These pin the IntentClassification value-object contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from workflows.send_invoice.scope_gate import IntentClassification


def test_accepts_embedded_instruction_label() -> None:
    c = IntentClassification(
        intent="embedded_instruction",
        confidence=0.9,
        rationale="Invoice request carries an out-of-scope side instruction.",
    )
    assert c.intent == "embedded_instruction"


@pytest.mark.parametrize("label", ["send_invoice", "out_of_scope", "embedded_instruction"])
def test_accepts_all_three_intent_labels(label: str) -> None:
    c = IntentClassification(intent=label, confidence=0.5, rationale="x")  # type: ignore[arg-type]
    assert c.intent == label


def test_rejects_unknown_label() -> None:
    with pytest.raises(ValidationError):
        IntentClassification(intent="delete_everything", confidence=0.5, rationale="x")  # type: ignore[arg-type]
