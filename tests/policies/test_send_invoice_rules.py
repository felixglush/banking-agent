"""RULES drive evaluate() directly. No Temporal, no DB."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from compass.policy import Phase, RuleFiredEvent, SinkEvent, evaluate
from compass.policy.sink import InMemorySink
from policies.send_invoice import RULES
from tests.policies.conftest import (
    happy_input_validation_ctx,
    out_of_scope_input_validation_ctx,
)


def _only_fired(events: list[SinkEvent]) -> list[RuleFiredEvent]:
    return [e for e in events if e["event_kind"] == "rule_fired"]


# ---------------------------------------------------------------------
# input_validation phase
# ---------------------------------------------------------------------


async def test_input_validation_permits_send_invoice() -> None:
    sink = InMemorySink()
    decision = await evaluate(
        RULES,
        Phase.input_validation,
        happy_input_validation_ctx(),
        sink=sink,
    )
    assert decision.permit is True
    assert decision.rule_ids_fired == ()
    skipped = {e["rule_id"] for e in sink.events if e["event_kind"] == "rule_skipped"}
    assert skipped == {"intent_must_be_send_invoice"}


async def test_input_validation_blocks_out_of_scope() -> None:
    sink = InMemorySink()
    decision = await evaluate(
        RULES,
        Phase.input_validation,
        out_of_scope_input_validation_ctx(),
        sink=sink,
    )
    assert decision.permit is False
    assert decision.rule_ids_fired == ("intent_must_be_send_invoice",)
    fired = _only_fired(sink.events)
    assert len(fired) == 1
    assert fired[0]["rule_id"] == "intent_must_be_send_invoice"
    assert fired[0]["evidence"]["value"] == "out_of_scope"


# ---------------------------------------------------------------------
# pre_action_proposal phase
# ---------------------------------------------------------------------


async def test_happy_proposal_permits_all_pre_action_proposal_rules(
    base_ctx: dict[str, Any],
) -> None:
    sink = InMemorySink()
    decision = await evaluate(
        RULES,
        Phase.pre_action_proposal,
        base_ctx,
        sink=sink,
    )
    assert decision.permit is True
    assert decision.rule_ids_fired == ()
    assert [e for e in sink.events if e["event_kind"] == "rule_fired"] == []
    skipped = {e["rule_id"] for e in sink.events if e["event_kind"] == "rule_skipped"}
    assert skipped == {
        "customer_must_exist",
        "customer_kyc_verified",
        "invoice_amount_cap",
        "require_amount_source",
        "require_evidence_citation",
        "contract_consistency",
        "prohibit_exceed_contract_cap",
        "currency_consistency",
    }


# ---- table: each row exercises one BLOCK rule ----


def _mut_no_customer(ctx: dict[str, Any]) -> None:
    ctx["resolved_entities"]["customer"] = None


def _mut_pending_kyc(ctx: dict[str, Any]) -> None:
    ctx["resolved_entities"]["customer"]["kyc_status"] = "pending"


def _mut_invalid_source_type(ctx: dict[str, Any]) -> None:
    ctx["proposal"]["line_items"][0]["source_type"] = "made_up"


def _mut_empty_source_refs(ctx: dict[str, Any]) -> None:
    ctx["proposal"]["line_items"][0]["source_refs"] = []


def _mut_currency_mismatch(ctx: dict[str, Any]) -> None:
    ctx["proposal"]["currency"] = "EUR"  # contract is USD


def _mut_exceed_cap(ctx: dict[str, Any]) -> None:
    ctx["resolved_entities"]["contract"]["monthly_hour_cap"] = 1  # line is 2h


def _mut_rate_card_currency_mismatch(ctx: dict[str, Any]) -> None:
    ctx["resolved_entities"]["rate_card_entries"] = [
        {"id": "rc_eur", "currency": "EUR"},
    ]


@pytest.mark.parametrize(
    "mutator,expected_rule_id",
    [
        (_mut_no_customer, "customer_must_exist"),
        (_mut_pending_kyc, "customer_kyc_verified"),
        (_mut_invalid_source_type, "require_amount_source"),
        (_mut_empty_source_refs, "require_evidence_citation"),
        (_mut_currency_mismatch, "contract_consistency"),
        (_mut_exceed_cap, "prohibit_exceed_contract_cap"),
        (_mut_rate_card_currency_mismatch, "currency_consistency"),
    ],
)
async def test_pre_action_proposal_block_rule_fires(
    base_ctx: dict[str, Any],
    mutator: Callable[[dict[str, Any]], None],
    expected_rule_id: str,
) -> None:
    mutator(base_ctx)
    decision = await evaluate(
        RULES,
        Phase.pre_action_proposal,
        base_ctx,
        sink=InMemorySink(),
    )
    assert decision.permit is False
    assert expected_rule_id in decision.rule_ids_fired


async def test_amount_above_cap_escalates_but_permits(
    base_ctx: dict[str, Any],
) -> None:
    """invoice_amount_cap is ESCALATE, so we test it separately — permit
    stays True because no BLOCK fired."""
    base_ctx["proposal"]["total_cents"] = 15_000_000  # > $100k cap
    decision = await evaluate(
        RULES,
        Phase.pre_action_proposal,
        base_ctx,
        sink=InMemorySink(),
    )
    assert decision.permit is True
    assert "invoice_amount_cap" in decision.rule_ids_fired
    assert len(decision.escalations) == 1


# ---------------------------------------------------------------------
# pre_execute phase
# ---------------------------------------------------------------------


_PRE_EXEC_HAPPY = {
    "proposal_hash_at_proposal": "h1",
    "current_proposal_hash": "h1",
    "policy_hash_at_proposal": "p1",
    "current_policy_hash": "p1",
}


async def test_pre_execute_happy_path_permits() -> None:
    decision = await evaluate(
        RULES,
        Phase.pre_execute,
        dict(_PRE_EXEC_HAPPY),
        sink=InMemorySink(),
    )
    assert decision.permit is True
    assert decision.rule_ids_fired == ()


async def test_silent_modification_fires_block() -> None:
    ctx = {**_PRE_EXEC_HAPPY, "current_proposal_hash": "h2"}
    decision = await evaluate(
        RULES,
        Phase.pre_execute,
        ctx,
        sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "no_silent_modification_after_confirmation" in decision.rule_ids_fired


async def test_policy_drift_fires_escalate() -> None:
    ctx = {**_PRE_EXEC_HAPPY, "current_policy_hash": "p2"}
    decision = await evaluate(
        RULES,
        Phase.pre_execute,
        ctx,
        sink=InMemorySink(),
    )
    # ESCALATE does not flip permit.
    assert decision.permit is True
    assert "no_policy_drift_after_confirmation" in decision.rule_ids_fired
    assert len(decision.escalations) == 1


# ---------------------------------------------------------------------
# audit_validation phase
# ---------------------------------------------------------------------


_AUDIT_HAPPY: dict[str, Any] = {
    "audit_entry_candidate": {"phase": "audit_validation", "event_kind": "executed", "payload": {}},
    "policy_hash": "abc",
    "tool_calls": [{"tool_name": "list_customers", "args": {}, "result": []}],
    "reasoning_text": "ok",
}


async def test_audit_validation_skips_complete_candidate() -> None:
    decision = await evaluate(
        RULES,
        Phase.audit_validation,
        dict(_AUDIT_HAPPY),
        sink=InMemorySink(),
    )
    assert decision.permit is True


@pytest.mark.parametrize(
    "override,expected_rule_id",
    [
        ({"policy_hash": ""}, "audit_has_policy_version"),
        ({"tool_calls": []}, "audit_has_data_sources"),
    ],
)
async def test_audit_validation_block_rule_fires(
    override: dict[str, Any],
    expected_rule_id: str,
) -> None:
    ctx = {**_AUDIT_HAPPY, **override}
    decision = await evaluate(
        RULES,
        Phase.audit_validation,
        ctx,
        sink=InMemorySink(),
    )
    assert decision.permit is False
    assert expected_rule_id in decision.rule_ids_fired
