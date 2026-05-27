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
    RateOverride,
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


def test_contract_top_level_rate_overrides_accepted() -> None:
    overrides = [RateOverride(role="Engineer", unit="hour", amount_cents=20_000)]
    c = Contract(
        id="contract_test_002",
        customer_id="cust_0001",
        kind="msa",
        effective_from=date(2025, 1, 1),
        expires_at=date(2026, 1, 1),
        currency="USD",
        billing_structure=TimeAndMaterials(
            rate_overrides=overrides,
            monthly_hour_cap=None,
            list_rates_apply=True,
        ),
        rate_overrides=overrides,
        monthly_hour_cap=None,
        scope_summary="T&M with negotiated engineer rate.",
    )
    assert len(c.rate_overrides) == 1
    assert c.rate_overrides[0].role == "Engineer"
    assert c.monthly_hour_cap is None


def test_contract_top_level_monthly_hour_cap_null_accepted() -> None:
    c = Contract(
        id="contract_test_003",
        customer_id="cust_0002",
        kind="msa",
        effective_from=date(2025, 1, 1),
        expires_at=date(2026, 1, 1),
        currency="USD",
        billing_structure=TimeAndMaterials(
            rate_overrides=[],
            monthly_hour_cap=None,
            list_rates_apply=True,
        ),
        rate_overrides=[],
        monthly_hour_cap=None,
        scope_summary="T&M list rates only.",
    )
    assert c.monthly_hour_cap is None


def test_contract_top_level_monthly_hour_cap_set_accepted() -> None:
    c = Contract(
        id="contract_test_004",
        customer_id="cust_0003",
        kind="msa",
        effective_from=date(2025, 1, 1),
        expires_at=date(2026, 1, 1),
        currency="USD",
        billing_structure=TimeAndMaterials(
            rate_overrides=[],
            monthly_hour_cap=80,
            list_rates_apply=True,
        ),
        rate_overrides=[],
        monthly_hour_cap=80,
        scope_summary="T&M with monthly cap.",
    )
    assert c.monthly_hour_cap == 80


def test_contract_model_validate_full_jsonl_row() -> None:
    """Contract.model_validate accepts the full JSONL row shape (as verify.py now does)."""
    row = {
        "id": "contract_test_005",
        "customer_id": "cust_0004",
        "kind": "msa",
        "effective_from": "2025-01-01",
        "expires_at": "2026-01-01",
        "currency": "USD",
        "billing_structure": {
            "kind": "t_and_m",
            "rate_overrides": [{"role": "Analyst", "unit": "hour", "amount_cents": 15_000}],
            "monthly_hour_cap": None,
            "list_rates_apply": True,
        },
        "rate_overrides": [{"role": "Analyst", "unit": "hour", "amount_cents": 15_000}],
        "monthly_hour_cap": None,
        "scope_summary": "Full row validation.",
        "source_doc_ref": None,
    }
    c = Contract.model_validate(row)
    assert c.rate_overrides[0].role == "Analyst"
