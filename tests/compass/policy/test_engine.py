"""Engine: evaluate() loop semantics, severity routing, exception wrapping."""

from __future__ import annotations

import pytest

from compass.policy import (
    Decision,
    Phase,
    PolicyEngineError,
    Rule,
    Severity,
)
from compass.policy.engine import (
    evaluate,
    evaluate_audit_validation,
    evaluate_pre_action_proposal,
    evaluate_pre_execute,
)
from compass.policy.sink import InMemorySink
from compass.policy.types import Predicate
from tests.compass.policy.conftest import make_predicate


async def test_no_matching_rules_permits() -> None:
    sink = InMemorySink()
    decision = await evaluate([], Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is True
    assert decision.rule_ids_fired == ()
    assert sink.events == []


async def test_phase_mismatch_skipped_silently() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r_pre_exec_only",
        phase=Phase.pre_execute,
        predicate=make_predicate(fires=True),
    )
    decision = await evaluate([rule], Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is True
    assert sink.events == []  # different-phase rules emit no events


async def test_skipped_rule_emits_event() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r1",
        phase=Phase.pre_action_proposal,
        predicate=make_predicate(fires=False),
    )
    decision = await evaluate([rule], Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is True
    assert decision.rule_ids_fired == ()
    assert sink.events == [
        {
            "event_kind": "rule_skipped",
            "rule_id": "r1",
            "phase": "pre_action_proposal",
        }
    ]


async def test_block_rule_fires_and_blocks() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r_block",
        phase=Phase.pre_action_proposal,
        predicate=make_predicate(fires=True, message="bad", evidence={"x": 1}),
        regulatory_basis=("SOP-1",),
        severity=Severity.BLOCK,
    )
    decision = await evaluate([rule], Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is False
    assert decision.rule_ids_fired == ("r_block",)
    assert len(decision.violations) == 1
    assert decision.violations[0].rule_id == "r_block"
    assert decision.violations[0].message == "bad"
    assert decision.violations[0].evidence == {"x": 1}
    fired_events = [e for e in sink.events if e["event_kind"] == "rule_fired"]
    assert len(fired_events) == 1
    assert fired_events[0]["rule_id"] == "r_block"
    assert fired_events[0]["decision"] == "block"
    assert fired_events[0]["regulatory_basis"] == ["SOP-1"]


async def test_escalate_rule_fires_but_permits() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r_esc",
        phase=Phase.pre_action_proposal,
        predicate=make_predicate(fires=True),
        severity=Severity.ESCALATE,
    )
    decision = await evaluate([rule], Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is True       # escalation does not block
    assert decision.rule_ids_fired == ("r_esc",)
    assert len(decision.escalations) == 1
    assert decision.violations == ()


async def test_declaration_order_preserved() -> None:
    sink = InMemorySink()
    rules = [
        Rule(id=f"r{i}", phase=Phase.pre_action_proposal,
             predicate=make_predicate(fires=True))
        for i in range(3)
    ]
    decision = await evaluate(rules, Phase.pre_action_proposal, {}, sink=sink)
    assert decision.rule_ids_fired == ("r0", "r1", "r2")
    assert [e["rule_id"] for e in sink.events] == ["r0", "r1", "r2"]


async def test_predicate_exception_wrapped_as_engine_error() -> None:
    def raises(_ctx):
        raise RuntimeError("predicate exploded")

    pred = Predicate(primitive_name="bad", params={}, fn=raises)
    rule = Rule(id="r_bad", phase=Phase.pre_action_proposal, predicate=pred)
    sink = InMemorySink()
    with pytest.raises(PolicyEngineError) as exc:
        await evaluate([rule], Phase.pre_action_proposal, {}, sink=sink)
    assert exc.value.rule_id == "r_bad"
    assert isinstance(exc.value.cause, RuntimeError)


async def test_evaluate_pre_action_proposal_wrapper() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r1",
        phase=Phase.pre_action_proposal,
        predicate=make_predicate(fires=False),
    )
    decision = await evaluate_pre_action_proposal([rule], {}, sink=sink)
    assert isinstance(decision, Decision)
    assert decision.permit is True


async def test_evaluate_pre_execute_wrapper_filters_phase() -> None:
    sink = InMemorySink()
    rules = [
        Rule(id="r_proposal", phase=Phase.pre_action_proposal,
             predicate=make_predicate(fires=True)),
        Rule(id="r_exec", phase=Phase.pre_execute,
             predicate=make_predicate(fires=False)),
    ]
    decision = await evaluate_pre_execute(rules, {}, sink=sink)
    # Only the pre_execute rule was evaluated.
    assert [e["rule_id"] for e in sink.events] == ["r_exec"]
    assert decision.permit is True


async def test_evaluate_audit_validation_wrapper() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r_audit",
        phase=Phase.audit_validation,
        predicate=make_predicate(fires=False),
    )
    decision = await evaluate_audit_validation([rule], {}, sink=sink)
    assert decision.permit is True


async def test_mixed_block_and_escalate_blocks() -> None:
    sink = InMemorySink()
    rules = [
        Rule(id="r_block", phase=Phase.pre_action_proposal,
             predicate=make_predicate(fires=True), severity=Severity.BLOCK),
        Rule(id="r_esc", phase=Phase.pre_action_proposal,
             predicate=make_predicate(fires=True), severity=Severity.ESCALATE),
    ]
    decision = await evaluate(rules, Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is False
    assert decision.rule_ids_fired == ("r_block", "r_esc")
    assert len(decision.violations) == 1
    assert len(decision.escalations) == 1
