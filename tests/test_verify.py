"""verify.py individual checks must flag deliberately-broken JSONL.

These tests don't exercise the full verify_all() orchestrator (that's
covered by the determinism+verify integration on the committed
generated data); they target each check function in isolation.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from synthetic_account_1 import verify


def test_invoice_total_mismatch_flagged() -> None:
    invoices = [
        {"id": "inv_1", "total_cents": 12_345, "currency": "USD"},
    ]
    line_items = [
        {
            "id": "li_1",
            "invoice_id": "inv_1",
            "quantity_micros": 1_000_000,
            "unit_amount_cents": 500,
            "line_total_cents": 500,
            "source_type": "user_specified",
        },
    ]
    with pytest.raises(verify.VerifyError, match="total_cents=12345"):
        verify.check_invoice_total_matches_lines(invoices, line_items)


def test_line_item_arithmetic_mismatch_flagged() -> None:
    invoices = [{"id": "inv_1", "total_cents": 1000}]
    line_items = [
        {
            "id": "li_broken",
            "invoice_id": "inv_1",
            "quantity_micros": 2_000_000,
            "unit_amount_cents": 500,
            "line_total_cents": 1_500,  # should be 1_000
            "source_type": "user_specified",
        }
    ]
    with pytest.raises(verify.VerifyError, match="li_broken"):
        verify.check_invoice_total_matches_lines(invoices, line_items)


def test_invoice_customer_fk_violation_flagged() -> None:
    invoices = [{"id": "inv_1", "customer_id": "cust_ghost"}]
    customers = [{"id": "cust_real"}]
    with pytest.raises(verify.VerifyError, match="cust_ghost"):
        verify.check_invoice_customer_fk(invoices, customers)


def test_contract_pydantic_validation_flags_bad_row() -> None:
    bad = [
        {
            "id": "c1",
            "customer_id": "cust_1",
            "kind": "retainer",
            "effective_from": "2026-01-01",
            "expires_at": "2025-01-01",  # before effective_from → reject
            "currency": "USD",
            "billing_structure": {"kind": "monthly_retainer", "monthly_amount_cents": 1000},
            "scope_summary": "bad",
        }
    ]
    with pytest.raises(verify.VerifyError, match="failed Pydantic validation"):
        verify.check_contracts_validate(bad)


def test_kyc_status_invalid_flagged() -> None:
    customers = [{"id": "c1", "kyc_status": "definitely_not_a_real_status"}]
    with pytest.raises(verify.VerifyError, match="bad kyc_status"):
        verify.check_kyc_statuses(customers)


def test_ambiguous_name_subset_absent_flagged() -> None:
    customers = [
        {"id": "c1", "name": "Acme Labs"},
        {"id": "c2", "name": "Globex Systems"},
        {"id": "c3", "name": "Northwind Capital"},
    ]
    with pytest.raises(verify.VerifyError, match="ambiguity-rich"):
        verify.check_ambiguous_name_subset(customers)


def test_ambiguous_name_subset_present_ok() -> None:
    customers = [
        {"id": "c1", "name": "Acme Corp"},
        {"id": "c2", "name": "Acme Corporation"},
        {"id": "c3", "name": "Globex Labs"},
    ]
    # Should not raise.
    verify.check_ambiguous_name_subset(customers)


def test_dispute_transaction_fk_flagged() -> None:
    disputes = [
        {"id": "d1", "transaction_id": "txn_ghost", "opened_at": datetime(2026, 1, 1)},
    ]
    transactions = [{"id": "txn_real"}]
    with pytest.raises(verify.VerifyError, match="txn_ghost"):
        verify.check_dispute_transaction_fk(disputes, transactions)
