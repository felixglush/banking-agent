"""Functional tests for the nine ``bank`` MCP tool handlers.

Tests call the handler functions directly (FastMCP's ``@mcp.tool``
decorator returns the original async function unchanged), so the
real SQL runs against the seeded ``compass_test`` Postgres database
configured by ``conftest.py``. The MCP transport layer adds no
business logic of its own; testing the handlers is sufficient for
the read-only v0.1 surface.

The seeded corpus is tiny (a handful of rows per table), so every
``BoundedList`` return carries ``truncated=False``. The cap-firing
behaviour is exercised separately in ``test_bounded_list_cap``.
"""

from datetime import date

import pytest

from mcp_bank.db import get_pool
from mcp_bank.server import (
    MAX_ROWS,
    get_active_contract,
    get_customer,
    get_invoice,
    get_rate_card,
    list_contracts,
    list_customers,
    list_invoices,
    list_time_entries,
    list_transactions,
)

# ---------------------------------------------------------------------
# list_customers
# ---------------------------------------------------------------------


async def test_list_customers_returns_all_when_no_filter() -> None:
    result = await list_customers()
    assert result.truncated is False
    assert [c.id for c in result.items] == ["cust_alpha", "cust_beta", "cust_gamma"]


async def test_list_customers_filters_case_insensitive_substring() -> None:
    result = await list_customers(name_contains="acme")
    assert result.truncated is False
    assert [c.id for c in result.items] == ["cust_alpha"]
    assert result.items[0].name == "Acme Corp"
    assert result.items[0].kyc_status == "verified"


async def test_list_customers_returns_empty_on_no_match() -> None:
    result = await list_customers(name_contains="no-such-customer")
    assert result.items == []
    assert result.truncated is False


# ---------------------------------------------------------------------
# get_customer
# ---------------------------------------------------------------------


async def test_get_customer_returns_row() -> None:
    customer = await get_customer("cust_beta")
    assert customer is not None
    assert customer.name == "Bramble Industries"
    assert customer.kyc_status == "pending"
    assert customer.default_payment_terms_days == 45


async def test_get_customer_returns_none_when_missing() -> None:
    assert await get_customer("cust_does_not_exist") is None


# ---------------------------------------------------------------------
# list_invoices
# ---------------------------------------------------------------------


async def test_list_invoices_returns_all_when_no_filter() -> None:
    result = await list_invoices()
    assert result.truncated is False
    assert {inv.id for inv in result.items} == {"inv_001", "inv_002", "inv_003"}
    # Every invoice carries its line items (per-tool contract).
    by_id = {inv.id: inv for inv in result.items}
    assert len(by_id["inv_001"].line_items) == 1
    assert len(by_id["inv_002"].line_items) == 2
    assert len(by_id["inv_003"].line_items) == 1
    # Line items come back ordered by ``line_no``.
    assert [li.line_no for li in by_id["inv_002"].line_items] == [1, 2]


async def test_list_invoices_filters_by_customer() -> None:
    result = await list_invoices(customer_id="cust_alpha")
    assert {inv.id for inv in result.items} == {"inv_001", "inv_002"}


async def test_list_invoices_filters_by_status() -> None:
    result = await list_invoices(status="paid")
    assert [inv.id for inv in result.items] == ["inv_001"]
    assert result.items[0].payment_received_at is not None


async def test_list_invoices_filters_by_customer_and_status() -> None:
    result = await list_invoices(customer_id="cust_alpha", status="sent")
    assert [inv.id for inv in result.items] == ["inv_002"]


async def test_list_invoices_returns_empty_when_customer_has_none() -> None:
    result = await list_invoices(customer_id="cust_gamma")
    assert result.items == []
    assert result.truncated is False


# ---------------------------------------------------------------------
# get_invoice
# ---------------------------------------------------------------------


async def test_get_invoice_returns_invoice_with_line_items() -> None:
    invoice = await get_invoice("inv_002")
    assert invoice is not None
    assert invoice.customer_id == "cust_alpha"
    assert invoice.status == "sent"
    assert [li.id for li in invoice.line_items] == ["inv_002_li_01", "inv_002_li_02"]
    # JSONB source_refs round-trips as a dict.
    assert invoice.line_items[0].source_refs == {"time_entry_ids": ["te_001"]}


async def test_get_invoice_returns_none_when_missing() -> None:
    assert await get_invoice("inv_does_not_exist") is None


# ---------------------------------------------------------------------
# list_transactions
# ---------------------------------------------------------------------


async def test_list_transactions_returns_all_when_no_filter() -> None:
    result = await list_transactions()
    assert result.truncated is False
    assert [t.id for t in result.items] == ["tx_001", "tx_002", "tx_003", "tx_004"]


async def test_list_transactions_filters_by_account() -> None:
    result = await list_transactions(account_id="acct_payroll")
    assert [t.id for t in result.items] == ["tx_003"]


async def test_list_transactions_inclusive_date_range() -> None:
    # 2025-02-20 (tx_002) and 2025-02-25 (tx_003) both fall inside the
    # range; the upper bound is end-of-day inclusive.
    result = await list_transactions(from_date=date(2025, 2, 1), to_date=date(2025, 2, 28))
    assert [t.id for t in result.items] == ["tx_002", "tx_003"]


async def test_list_transactions_from_date_only() -> None:
    result = await list_transactions(from_date=date(2025, 3, 1))
    assert [t.id for t in result.items] == ["tx_004"]


async def test_list_transactions_account_and_date_compose() -> None:
    result = await list_transactions(
        account_id="acct_ops",
        from_date=date(2025, 2, 1),
        to_date=date(2025, 3, 31),
    )
    assert [t.id for t in result.items] == ["tx_002", "tx_004"]


# ---------------------------------------------------------------------
# get_rate_card
# ---------------------------------------------------------------------


async def test_get_rate_card_by_service_returns_all_matching_roles() -> None:
    result = await get_rate_card(service="Implementation")
    assert {r.id for r in result.items} == {"rc_arch", "rc_eng"}


async def test_get_rate_card_by_role_alone() -> None:
    result = await get_rate_card(role="Trainer")
    assert [r.id for r in result.items] == ["rc_trainer"]


async def test_get_rate_card_by_service_and_role_intersects() -> None:
    result = await get_rate_card(service="Implementation", role="Solutions Architect")
    assert [r.id for r in result.items] == ["rc_arch"]
    assert result.items[0].list_amount_cents == 35000


async def test_get_rate_card_with_neither_filter_raises() -> None:
    with pytest.raises(ValueError, match="at least one of"):
        await get_rate_card()


async def test_get_rate_card_returns_empty_on_no_match() -> None:
    result = await get_rate_card(service="Nonexistent")
    assert result.items == []
    assert result.truncated is False


# ---------------------------------------------------------------------
# list_time_entries
# ---------------------------------------------------------------------


async def test_list_time_entries_returns_all_for_customer() -> None:
    result = await list_time_entries(customer_id="cust_alpha")
    assert [t.id for t in result.items] == ["te_001", "te_002", "te_003"]


async def test_list_time_entries_filters_by_project() -> None:
    result = await list_time_entries(customer_id="cust_alpha", project_id="p_alpha_2")
    assert [t.id for t in result.items] == ["te_003"]


async def test_list_time_entries_filters_by_date_range() -> None:
    result = await list_time_entries(
        customer_id="cust_alpha",
        from_date=date(2025, 2, 1),
        to_date=date(2025, 2, 28),
    )
    assert [t.id for t in result.items] == ["te_002", "te_003"]


async def test_list_time_entries_returns_empty_for_customer_with_none() -> None:
    result = await list_time_entries(customer_id="cust_beta")
    assert result.items == []
    assert result.truncated is False


# ---------------------------------------------------------------------
# get_active_contract
# ---------------------------------------------------------------------


async def test_get_active_contract_picks_currently_effective_one() -> None:
    contract = await get_active_contract(customer_id="cust_alpha", as_of_date=date(2025, 6, 15))
    assert contract is not None
    assert contract.id == "ct_alpha_current"


async def test_get_active_contract_picks_old_when_as_of_in_its_window() -> None:
    contract = await get_active_contract(customer_id="cust_alpha", as_of_date=date(2024, 5, 1))
    assert contract is not None
    assert contract.id == "ct_alpha_old"


async def test_get_active_contract_returns_none_when_only_future_exists() -> None:
    assert await get_active_contract(customer_id="cust_gamma", as_of_date=date(2025, 6, 15)) is None


async def test_get_active_contract_returns_none_when_customer_has_no_contracts() -> None:
    assert await get_active_contract(customer_id="cust_beta", as_of_date=date(2025, 6, 15)) is None


async def test_get_active_contract_strict_expiry_boundary() -> None:
    # ct_alpha_old.expires_at = 2024-12-31 and the active rule is
    # expires_at > as_of_date (strict), so on 2024-12-31 the old contract
    # is no longer active. ct_alpha_current starts on 2025-01-01, so it
    # isn't active that day either. Result: None.
    assert (
        await get_active_contract(customer_id="cust_alpha", as_of_date=date(2024, 12, 31)) is None
    )
    # One day earlier, the old contract is still active.
    earlier = await get_active_contract(customer_id="cust_alpha", as_of_date=date(2024, 12, 30))
    assert earlier is not None
    assert earlier.id == "ct_alpha_old"


# ---------------------------------------------------------------------
# list_contracts
# ---------------------------------------------------------------------


async def test_list_contracts_returns_all_for_customer_in_effective_order() -> None:
    result = await list_contracts(customer_id="cust_alpha")
    assert [c.id for c in result.items] == ["ct_alpha_old", "ct_alpha_current"]
    # JSONB columns round-trip with full structure.
    assert result.items[1].billing_structure["kind"] == "flat_fee"
    assert result.items[1].rate_overrides == [
        {"role": "Solutions Architect", "unit": "hour", "amount_cents": 40000},
    ]
    assert result.items[1].monthly_hour_cap == 40


async def test_list_contracts_returns_empty_for_customer_with_none() -> None:
    result = await list_contracts(customer_id="cust_beta")
    assert result.items == []
    assert result.truncated is False


# ---------------------------------------------------------------------
# BoundedList cap behaviour — exercised against a temporarily oversized
# rate_cards table so the seed corpus stays small for the per-tool tests.
# ---------------------------------------------------------------------


async def test_bounded_list_cap_fires_and_flags_truncated() -> None:
    """When a query matches more than MAX_ROWS rows the response must
    contain exactly MAX_ROWS items and ``truncated=True``."""
    pool = get_pool()
    inserted_ids = [f"rc_overflow_{i:04d}" for i in range(MAX_ROWS + 5)]
    async with pool.connection() as conn, conn.cursor() as cur:
        try:
            await cur.executemany(
                "INSERT INTO rate_cards "
                "(id, service, role, unit, list_amount_cents, currency, effective_from) "
                "VALUES (%s, 'Overflow', NULL, 'hour', 1, 'USD', '2024-01-01')",
                [(rid,) for rid in inserted_ids],
            )
            await conn.commit()

            result = await get_rate_card(service="Overflow")
            assert result.truncated is True
            assert len(result.items) == MAX_ROWS
        finally:
            await cur.execute("DELETE FROM rate_cards WHERE id = ANY(%s)", (inserted_ids,))
            await conn.commit()
