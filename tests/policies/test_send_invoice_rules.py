"""RULES drive evaluate() directly. No Temporal, no DB."""

from __future__ import annotations

from typing import Any

from compass.policy import Phase, evaluate
from compass.policy.sink import InMemorySink
from policies.send_invoice import RULES

# ---- pre_action_proposal: happy path ----


async def test_happy_proposal_permits_all_pre_action_proposal_rules(
    base_ctx: dict[str, Any],
) -> None:
    sink = InMemorySink()
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=sink,
    )
    assert decision.permit is True
    assert decision.rule_ids_fired == ()
    fired = [e for e in sink.events if e["event_kind"] == "rule_fired"]
    assert fired == []
    skipped = [e for e in sink.events if e["event_kind"] == "rule_skipped"]
    expected_ids = {
        "customer_must_exist", "customer_kyc_verified", "invoice_amount_cap",
        "require_amount_source", "require_evidence_citation",
        "contract_consistency", "prohibit_exceed_contract_cap",
        "currency_consistency",
    }
    assert {e["rule_id"] for e in skipped} == expected_ids


# ---- pre_action_proposal: per-rule fail cases ----


async def test_missing_customer_fires_customer_must_exist(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["resolved_entities"]["customer"] = None
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "customer_must_exist" in decision.rule_ids_fired


async def test_pending_kyc_fires_customer_kyc_verified(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["resolved_entities"]["customer"]["kyc_status"] = "pending"
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "customer_kyc_verified" in decision.rule_ids_fired


async def test_amount_above_cap_escalates_but_permits(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["proposal"]["total_cents"] = 15_000_000  # > $100k cap
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    # Escalation does not block — permit stays True (no BLOCK fired).
    assert decision.permit is True
    assert "invoice_amount_cap" in decision.rule_ids_fired
    assert len(decision.escalations) == 1


async def test_empty_source_refs_fires_require_evidence_citation(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["proposal"]["line_items"][0]["source_refs"] = []
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "require_evidence_citation" in decision.rule_ids_fired


async def test_invalid_source_type_fires_require_amount_source(
    base_ctx: dict[str, Any],
) -> None:
    # Bypass Pydantic by tweaking the raw dict directly.
    base_ctx["proposal"]["line_items"][0]["source_type"] = "made_up"
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "require_amount_source" in decision.rule_ids_fired


async def test_currency_mismatch_fires_contract_consistency(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["proposal"]["currency"] = "EUR"
    base_ctx["resolved_entities"]["contract"]["currency"] = "USD"
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "contract_consistency" in decision.rule_ids_fired


async def test_exceed_contract_cap_fires(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["resolved_entities"]["contract"]["monthly_hour_cap"] = 1
    # The line item is 2h; cap is 1h.
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "prohibit_exceed_contract_cap" in decision.rule_ids_fired


async def test_rate_card_currency_mismatch_fires_currency_consistency(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["resolved_entities"]["rate_card_entries"] = [
        {"id": "rc_other_ccy", "currency": "EUR"},
    ]
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "currency_consistency" in decision.rule_ids_fired


# ---- pre_execute ----


async def test_no_silent_modification_skips_when_hash_matches() -> None:
    ctx = {
        "proposal_hash_at_proposal": "h1",
        "current_proposal_hash": "h1",
        "policy_hash_at_proposal": "p1",
        "current_policy_hash": "p1",
    }
    decision = await evaluate(
        RULES, Phase.pre_execute, ctx, sink=InMemorySink(),
    )
    assert decision.permit is True
    assert decision.rule_ids_fired == ()


async def test_silent_modification_fires_block() -> None:
    ctx = {
        "proposal_hash_at_proposal": "h1",
        "current_proposal_hash": "h2",
        "policy_hash_at_proposal": "p1",
        "current_policy_hash": "p1",
    }
    decision = await evaluate(
        RULES, Phase.pre_execute, ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "no_silent_modification_after_confirmation" in decision.rule_ids_fired


async def test_policy_drift_fires_escalate() -> None:
    ctx = {
        "proposal_hash_at_proposal": "h1",
        "current_proposal_hash": "h1",
        "policy_hash_at_proposal": "p1",
        "current_policy_hash": "p2",
    }
    decision = await evaluate(
        RULES, Phase.pre_execute, ctx, sink=InMemorySink(),
    )
    # Escalation only — does not flip permit.
    assert decision.permit is True
    assert "no_policy_drift_after_confirmation" in decision.rule_ids_fired
    assert len(decision.escalations) == 1


# ---- audit_validation ----


async def test_audit_validation_skips_complete_candidate() -> None:
    ctx = {
        "audit_entry_candidate": {"phase": "audit_validation",
                                  "event_kind": "executed",
                                  "payload": {}},
        "policy_hash": "abc",
        "tool_calls": [{"tool_name": "list_customers", "args": {}, "result": []}],
        "reasoning_text": "ok",
    }
    decision = await evaluate(
        RULES, Phase.audit_validation, ctx, sink=InMemorySink(),
    )
    assert decision.permit is True


async def test_missing_policy_hash_fires_audit_has_policy_version() -> None:
    ctx = {
        "audit_entry_candidate": {},
        "policy_hash": "",
        "tool_calls": [{"x": 1}],
        "reasoning_text": "",
    }
    decision = await evaluate(
        RULES, Phase.audit_validation, ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "audit_has_policy_version" in decision.rule_ids_fired


async def test_empty_tool_calls_fires_audit_has_data_sources() -> None:
    ctx = {
        "audit_entry_candidate": {},
        "policy_hash": "abc",
        "tool_calls": [],
        "reasoning_text": "",
    }
    decision = await evaluate(
        RULES, Phase.audit_validation, ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "audit_has_data_sources" in decision.rule_ids_fired
