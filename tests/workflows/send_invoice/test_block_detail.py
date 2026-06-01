"""Unit tests for _block_detail — the legible block-reason string built from a
PolicyDecisionError's structured details (pure; no Temporal)."""

from __future__ import annotations

from temporalio.exceptions import ApplicationError

from workflows.send_invoice.workflow import _block_detail  # pyright: ignore[reportPrivateUsage]


def _policy_block(rule_ids: list[str]) -> ApplicationError:
    return ApplicationError(
        "policy blocked",
        {"phase": "input_validation", "rule_ids_fired": rule_ids, "violations": []},
        type="PolicyDecisionError",
    )


def test_names_the_fired_rule() -> None:
    detail = _block_detail(_policy_block(["intent_must_be_send_invoice"]))
    assert detail == "policy blocked: intent_must_be_send_invoice"


def test_joins_multiple_fired_rules() -> None:
    detail = _block_detail(_policy_block(["invoice_amount_cap", "customer_must_exist"]))
    assert detail == "policy blocked: invoice_amount_cap, customer_must_exist"


def test_falls_back_to_error_type_when_no_rule_ids() -> None:
    # A genuine engine/infra failure carries no rule_ids_fired — surface the type
    # so it doesn't masquerade as a clean policy block.
    err = ApplicationError("boom", {"phase": "input_validation"}, type="PolicyInfraError")
    assert _block_detail(err) == "blocked (PolicyInfraError)"


def test_handles_missing_cause() -> None:
    assert _block_detail(None) == "blocked"


def test_pre_action_proposal_block_names_the_billing_rule() -> None:
    # Same helper serves the pre_action_proposal + pre_execute block sites, where
    # the realistic fired rules are billing-integrity / amount-cap rules.
    err = ApplicationError(
        "policy blocked",
        {
            "phase": "pre_action_proposal",
            "rule_ids_fired": ["prohibit_exceed_contract_cap"],
            "violations": [{"rule_id": "prohibit_exceed_contract_cap", "message": "over cap"}],
        },
        type="PolicyDecisionError",
    )
    assert _block_detail(err) == "policy blocked: prohibit_exceed_contract_cap"
