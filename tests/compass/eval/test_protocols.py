"""Protocols are structural — the test confirms the surface and the
type relationships, not behavior."""

from typing import get_type_hints

from compass.eval import (
    Case,
    CaseResult,
    EvalRunStore,
    Mode,
    RuleFireSource,
    ScoreSink,
    WorkflowRunner,
)


def test_workflow_runner_protocol_surface():
    hints = get_type_hints(WorkflowRunner.run_case)
    assert "case" in hints
    assert hints["return"] is CaseResult


def test_rule_fire_source_returns_set():
    hints = get_type_hints(RuleFireSource.rule_ids_fired)
    assert hints["return"] == set[str]


def test_score_sink_signature():
    hints = get_type_hints(ScoreSink.write_score)
    for required in ("run_id", "item_id", "name", "value"):
        assert required in hints


def test_eval_run_store_signature():
    assert hasattr(EvalRunStore, "allocate_run")
    assert hasattr(EvalRunStore, "link_pair")
    assert hasattr(EvalRunStore, "finalize")


def test_case_dataclass_fields():
    case = Case(
        case_id="ir_0001",
        request="Send invoice for Acme Corp",
        expected_outcome="sent",
        expected={"customer_id": "cust_0001"},
        expected_fired_rules=["intent_must_be_send_invoice"],
        expected_decline_reason=None,
    )
    assert case.case_id == "ir_0001"


def test_mode_enum():
    assert Mode.train.value == "train"
    assert Mode.holdout.value == "holdout"
