"""The Contract schema is the build-plan's load-time validation point.

These tests pin the rejection semantics for the cases we care about:
  - milestones must sum to total
  - expires_at must be strictly after effective_from
  - discriminated billing_structure rejects unknown kinds
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from synthetic_account_1.pydantic_models import (
    Contract,
    FlatFeeSOW,
    Milestone,
    MonthlyRetainer,
    TimeAndMaterials,
)


def _valid_retainer_contract() -> Contract:
    return Contract(
        id="contract_test_001",
        customer_id="cust_0001",
        kind="retainer",
        effective_from=date(2025, 1, 1),
        expires_at=date(2026, 1, 1),
        currency="USD",
        billing_structure=MonthlyRetainer(monthly_amount_cents=500_000),
        scope_summary="Retainer-based premium support.",
        source_doc_ref=None,
    )


def test_valid_retainer_contract_passes() -> None:
    c = _valid_retainer_contract()
    assert c.id == "contract_test_001"
    assert c.billing_structure.kind == "monthly_retainer"


def test_expires_at_must_be_after_effective_from() -> None:
    with pytest.raises(ValidationError, match="expires_at must be strictly after"):
        Contract(
            id="x",
            customer_id="c",
            kind="retainer",
            effective_from=date(2026, 1, 1),
            expires_at=date(2025, 1, 1),
            currency="USD",
            billing_structure=MonthlyRetainer(monthly_amount_cents=100_000),
            scope_summary="bad dates",
        )


def test_flat_fee_milestone_sum_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="milestones sum to"):
        FlatFeeSOW(
            total_amount_cents=10_000,
            milestones=[
                Milestone(name="A", amount_cents=4_000, due_date=date(2025, 6, 1)),
                Milestone(name="B", amount_cents=4_000, due_date=date(2025, 9, 1)),
                # 4_000 + 4_000 = 8_000 ≠ 10_000 → reject
            ],
        )


def test_flat_fee_milestone_sum_match_ok() -> None:
    sow = FlatFeeSOW(
        total_amount_cents=10_000,
        milestones=[
            Milestone(name="A", amount_cents=4_000, due_date=date(2025, 6, 1)),
            Milestone(name="B", amount_cents=6_000, due_date=date(2025, 9, 1)),
        ],
    )
    assert sow.total_amount_cents == 10_000


def test_billing_structure_discriminator_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        Contract.model_validate(
            {
                "id": "x",
                "customer_id": "c",
                "kind": "retainer",
                "effective_from": "2025-01-01",
                "expires_at": "2026-01-01",
                "currency": "USD",
                "billing_structure": {"kind": "made_up_kind", "amount": 1},
                "scope_summary": "nope",
            }
        )


def test_extra_fields_forbidden_on_contract() -> None:
    with pytest.raises(ValidationError):
        Contract.model_validate(
            {
                "id": "x",
                "customer_id": "c",
                "kind": "retainer",
                "effective_from": "2025-01-01",
                "expires_at": "2026-01-01",
                "currency": "USD",
                "billing_structure": {
                    "kind": "monthly_retainer",
                    "monthly_amount_cents": 1000,
                },
                "scope_summary": "ok",
                "unknown_extra_field": "boom",
            }
        )


def test_t_and_m_with_cap_accepted() -> None:
    tm = TimeAndMaterials(rate_overrides=[], monthly_hour_cap=40, list_rates_apply=True)
    assert tm.monthly_hour_cap == 40
