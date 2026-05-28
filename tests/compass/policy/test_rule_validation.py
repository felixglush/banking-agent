"""Rule.__post_init__ enforces the severity-vs-phase invariant.

Build-plan §Policy Engine: ESCALATE is only realizable at workflow-
level phases. Stage-5 spec §Types pins the rejection to construction
time so misconfigurations are caught at module import, not at first
evaluation.
"""

from __future__ import annotations

import pytest

from compass.policy import Phase, Rule, Severity
from tests.compass.policy.conftest import make_predicate


def test_escalate_at_input_validation_rejected() -> None:
    with pytest.raises(ValueError, match="ESCALATE is not realizable"):
        Rule(
            id="bad",
            phase=Phase.input_validation,
            predicate=make_predicate(),
            severity=Severity.ESCALATE,
        )


def test_escalate_at_output_validation_rejected() -> None:
    with pytest.raises(ValueError, match="ESCALATE is not realizable"):
        Rule(
            id="bad",
            phase=Phase.output_validation,
            predicate=make_predicate(),
            severity=Severity.ESCALATE,
        )


def test_escalate_at_workflow_phase_accepted() -> None:
    rule = Rule(
        id="ok",
        phase=Phase.pre_action_proposal,
        predicate=make_predicate(),
        severity=Severity.ESCALATE,
    )
    assert rule.severity is Severity.ESCALATE


def test_block_at_input_validation_accepted() -> None:
    rule = Rule(
        id="ok",
        phase=Phase.input_validation,
        predicate=make_predicate(),
        severity=Severity.BLOCK,
    )
    assert rule.severity is Severity.BLOCK
