"""Billing integrity primitives — application-specific to send_invoice.

Self-register at import via @primitive. Imported by
policies/send_invoice.py (which is itself imported by the
evaluate_policy activity) — that's the chain that populates the
registry before the first hash_rules() call.

All four primitives are pre_action_proposal phase, BLOCK severity.
They read the agent's resolved entities and proposal from the context
dict; the workflow's context.py module is responsible for projecting
those into the expected shape.
"""

from __future__ import annotations

from typing import Any, cast

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import PolicyContext, Violation


@primitive("require_amount_source")
def require_amount_source():
    """Every line item carries a valid source_type + non-empty source_refs.

    Domain-specific instantiation of require_evidence_citation. The
    valid source_type set matches workflows/send_invoice/types.py
    LineItemSourceType.
    """
    VALID = {"contract", "rate_card", "time_tracking", "user_specified"}

    def check(ctx: PolicyContext) -> Violation | None:
        lines = resolve_dotted(ctx, "proposal.line_items")
        if lines is MISSING or not isinstance(lines, list):
            return Violation(
                rule_id="",
                message="proposal.line_items missing or not a list",
                evidence={"reason": "missing_line_items"},
            )
        for i, line in enumerate(cast(list[dict[str, Any]], lines)):
            stype = line.get("source_type")
            if stype not in VALID:
                return Violation(
                    rule_id="",
                    message=f"line {i} has invalid source_type {stype!r}",
                    evidence={"line_no": i, "source_type": stype, "valid": sorted(VALID)},
                )
            refs: list[Any] = line.get("source_refs") or []
            if not refs:
                return Violation(
                    rule_id="",
                    message=f"line {i} has empty source_refs",
                    evidence={"line_no": i},
                )
        return None

    return check


@primitive("contract_consistency_check")
def contract_consistency_check():
    """When a contract is resolved, proposal currency must match it.

    Stage 5 scope: currency comparison. Future scope: billing-structure
    match (flat-fee SOW vs. T&M etc.). The contract reference itself is
    optional — if no contract was queried, the rule skips.
    """

    def check(ctx: PolicyContext) -> Violation | None:
        contract = resolve_dotted(ctx, "resolved_entities.contract")
        if contract is MISSING or contract is None:
            return None
        proposal = resolve_dotted(ctx, "proposal")
        if proposal is MISSING:
            return Violation(
                rule_id="",
                message="proposal missing",
                evidence={},
            )
        proposal_currency = proposal.get("currency")
        contract_currency = contract.get("currency")
        if proposal_currency != contract_currency:
            return Violation(
                rule_id="",
                message=(
                    f"proposal currency {proposal_currency!r} does not match "
                    f"contract currency {contract_currency!r}"
                ),
                evidence={
                    "proposal_currency": proposal_currency,
                    "contract_currency": contract_currency,
                    "contract_id": contract.get("id"),
                },
            )
        return None

    return check


@primitive("prohibit_exceed_contract_cap")
def prohibit_exceed_contract_cap():
    """When the contract has a monthly_hour_cap, proposal hours must not exceed it.

    Sums ``quantity_micros / 1e6`` across time_tracking line items only.
    Other source types don't bill against the cap.
    """

    def check(ctx: PolicyContext) -> Violation | None:
        contract = resolve_dotted(ctx, "resolved_entities.contract")
        if contract is MISSING or contract is None:
            return None
        cap = contract.get("monthly_hour_cap")
        if cap is None:
            return None
        lines = resolve_dotted(ctx, "proposal.line_items")
        if lines is MISSING or not isinstance(lines, list):
            return None
        typed_lines = cast(list[dict[str, Any]], lines)
        hours = sum(
            line.get("quantity_micros", 0) / 1_000_000
            for line in typed_lines
            if line.get("source_type") == "time_tracking"
        )
        if hours > cap:
            return Violation(
                rule_id="",
                message=f"proposal hours {hours} exceed contract cap {cap}",
                evidence={
                    "proposal_hours": hours,
                    "contract_cap": cap,
                    "contract_id": contract.get("id"),
                },
            )
        return None

    return check


@primitive("currency_consistency_check")
def currency_consistency_check():
    """All line items must share the proposal's currency.

    Line items don't carry their own currency in InvoiceProposal at
    v0.1 — Pydantic enforces that the proposal has one currency for
    the whole invoice. This rule guards against a future regression
    where line-level currency is added and falls out of sync. Stage 5
    additionally checks: if any cited rate_card has a different
    currency, fire.
    """

    def check(ctx: PolicyContext) -> Violation | None:
        proposal = resolve_dotted(ctx, "proposal")
        if proposal is MISSING:
            return None
        proposal_currency = proposal.get("currency")
        rate_cards = resolve_dotted(ctx, "resolved_entities.rate_card_entries")
        if rate_cards is MISSING or not isinstance(rate_cards, list):
            return None
        typed_cards = cast(list[dict[str, Any]], rate_cards)
        mismatched = [rc.get("id") for rc in typed_cards if rc.get("currency") != proposal_currency]
        if mismatched:
            return Violation(
                rule_id="",
                message=(
                    f"rate cards {mismatched} have currency != proposal "
                    f"currency {proposal_currency!r}"
                ),
                evidence={
                    "proposal_currency": proposal_currency,
                    "mismatched_rate_card_ids": mismatched,
                },
            )
        return None

    return check
