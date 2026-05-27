"""``bank`` MCP server — read-only, structured tool surface.

Tool bodies raise ``NotImplementedError`` at this stage; this PR lands
the skeleton (server instance, tool signatures, I/O models, idempotency
contract). The parameterized-SQL handlers and the ``psycopg`` pool wire
land in the follow-up PR that closes Stage 3.

No raw-SQL or query-builder tool is exposed — see
docs/build-plan.md §Stage 3 and §Validation Criteria 2.
"""

from __future__ import annotations

from datetime import date

from fastmcp import FastMCP

from mcp_bank.models import (
    Contract,
    Customer,
    Invoice,
    InvoiceStatus,
    RateCardEntry,
    TimeEntry,
    Transaction,
)

mcp: FastMCP[None] = FastMCP("bank")


@mcp.tool
async def list_customers(name_contains: str | None = None) -> list[Customer]:
    """List customers, optionally filtered by case-insensitive substring on ``name``."""
    raise NotImplementedError("mcp_bank.list_customers — SQL handler lands in Stage 3 impl PR")


@mcp.tool
async def get_customer(customer_id: str) -> Customer | None:
    """Fetch one customer by id. Returns ``None`` if not found."""
    raise NotImplementedError("mcp_bank.get_customer — SQL handler lands in Stage 3 impl PR")


@mcp.tool
async def list_invoices(
    customer_id: str | None = None,
    status: InvoiceStatus | None = None,
) -> list[Invoice]:
    """List invoices, optionally filtered by customer and/or status.

    Each row includes its ``line_items``.
    """
    raise NotImplementedError("mcp_bank.list_invoices — SQL handler lands in Stage 3 impl PR")


@mcp.tool
async def get_invoice(invoice_id: str) -> Invoice | None:
    """Fetch one invoice (with line items) by id. Returns ``None`` if not found."""
    raise NotImplementedError("mcp_bank.get_invoice — SQL handler lands in Stage 3 impl PR")


@mcp.tool
async def list_transactions(
    account_id: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[Transaction]:
    """List transactions, optionally bounded by account and posted-at date range (inclusive)."""
    raise NotImplementedError("mcp_bank.list_transactions — SQL handler lands in Stage 3 impl PR")


@mcp.tool
async def get_rate_card(
    service: str | None = None,
    role: str | None = None,
) -> list[RateCardEntry]:
    """Look up rate-card entries by service and/or role.

    At least one of ``service``, ``role`` must be supplied; rate-card lookups
    without either filter are rejected to keep the tool-call history meaningful
    for ``pre_action_proposal`` rules.
    """
    raise NotImplementedError("mcp_bank.get_rate_card — SQL handler lands in Stage 3 impl PR")


@mcp.tool
async def list_time_entries(
    customer_id: str,
    project_id: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[TimeEntry]:
    """List time entries for a customer, optionally scoped to a project and/or date range."""
    raise NotImplementedError("mcp_bank.list_time_entries — SQL handler lands in Stage 3 impl PR")


@mcp.tool
async def get_active_contract(customer_id: str, as_of_date: date) -> Contract | None:
    """Return the customer's contract that is active on ``as_of_date``, or ``None``.

    "Active" means ``effective_from <= as_of_date`` and (``expires_at IS NULL`` or
    ``expires_at > as_of_date``). If multiple contracts match, the most recently
    effective one wins — the handler is responsible for that ordering.
    """
    raise NotImplementedError("mcp_bank.get_active_contract — SQL handler lands in Stage 3 impl PR")


@mcp.tool
async def list_contracts(customer_id: str) -> list[Contract]:
    """List all contracts (active, future, expired) for a customer."""
    raise NotImplementedError("mcp_bank.list_contracts — SQL handler lands in Stage 3 impl PR")
