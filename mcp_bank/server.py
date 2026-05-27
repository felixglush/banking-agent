"""``bank`` MCP server — read-only, structured tool surface.

All nine tools are read-only ``SELECT`` queries (parameterized) against
the bank-data tables. No raw-SQL or query-builder tool is exposed —
see docs/build-plan.md §Stage 3 and §Validation Criteria 2.

The lifespan opens a single ``psycopg`` async connection pool from
``COMPASS_PG_DSN`` and tears it down on shutdown. Handlers call
``mcp_bank.db.get_pool()`` to acquire a connection.

The composed-SQL ``execute()`` calls carry ``type: ignore[arg-type]``
to match the project convention in ``synthetic_account_1/load_to_postgres.py``:
the WHERE-clause fragments are static literals chosen at the call site
and the only user-supplied values flow through ``%s`` parameters, so
there is no injection surface despite the f-string composition.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastmcp import FastMCP
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from mcp_bank.db import get_pool, set_pool
from mcp_bank.models import (
    BoundedList,
    Contract,
    Customer,
    Invoice,
    InvoiceLineItem,
    InvoiceStatus,
    RateCardEntry,
    TimeEntry,
    Transaction,
)

# Server-side hard cap on every ``list_*`` / ``get_rate_card`` tool. We
# do not paginate; if a query matches more rows than this, the response
# carries ``truncated=True`` and the agent is expected to narrow its
# filters. See mcp_bank/README.md §Result cap for the rationale.
MAX_ROWS = 500


@asynccontextmanager
async def lifespan(server: FastMCP[None]) -> AsyncGenerator[None]:
    dsn = os.environ.get("COMPASS_PG_DSN")
    if not dsn:
        raise RuntimeError("mcp_bank: COMPASS_PG_DSN must be set to run the bank MCP server.")
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo=dsn, min_size=1, max_size=4, open=False
    )
    await pool.open()
    set_pool(pool)
    try:
        yield
    finally:
        set_pool(None)
        await pool.close()


mcp: FastMCP[None] = FastMCP("bank", lifespan=lifespan)


# ---------------------------------------------------------------------
# Column lists — kept verbatim from db/schema.sql in the same order as
# the Pydantic model fields so ``Model(**row)`` works without remapping.
# ---------------------------------------------------------------------

_CUSTOMER_COLS = (
    "id, name, email, address, kyc_status, default_payment_terms_days, cohort, created_at"
)
_TRANSACTION_COLS = (
    "id, account_id, amount_cents, direction, counterparty, "
    "memo, category, posted_at, related_invoice_id"
)
_RATE_CARD_COLS = (
    "id, service, role, unit, list_amount_cents, currency, effective_from, effective_to"
)
_TIME_ENTRY_COLS = (
    "id, customer_id, project_id, role, hours_micros, occurred_at, description, invoiced"
)
_INVOICE_COLS = (
    "id, customer_id, issued_at, due_at, total_cents, currency, status, "
    "payment_received_at, source_type, contract_id, dispute_flag"
)
_LINE_ITEM_COLS = (
    "id, invoice_id, line_no, description, quantity_micros, "
    "unit_amount_cents, line_total_cents, source_type, source_refs, computation"
)
_CONTRACT_COLS = (
    "id, customer_id, kind, effective_from, expires_at, currency, "
    "billing_structure, rate_overrides, monthly_hour_cap, scope_summary, source_doc_ref"
)


async def _fetch_invoices(
    where_sql: str, params: tuple[Any, ...], *, limit: int | None
) -> tuple[list[Invoice], bool]:
    """Run an invoices SELECT and attach line items.

    When ``limit`` is given, fetches ``limit + 1`` invoices so the caller
    can detect overflow; the second element of the returned tuple is
    ``True`` iff the underlying query matched more than ``limit`` rows.
    Line items are loaded only for the kept invoices.
    """
    pool = get_pool()
    extra_sql = " LIMIT %s" if limit is not None else ""
    extra_params: tuple[Any, ...] = (limit + 1,) if limit is not None else ()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_INVOICE_COLS} FROM invoices {where_sql} "  # type: ignore[arg-type]  # noqa: S608
            f"ORDER BY issued_at, id{extra_sql}",
            params + extra_params,
        )
        invoice_rows = await cur.fetchall()
        truncated = limit is not None and len(invoice_rows) > limit
        if truncated:
            assert limit is not None
            invoice_rows = invoice_rows[:limit]
        if not invoice_rows:
            return [], truncated
        invoice_ids = [r["id"] for r in invoice_rows]
        await cur.execute(
            f"SELECT {_LINE_ITEM_COLS} FROM invoice_line_items "  # type: ignore[arg-type]  # noqa: S608
            "WHERE invoice_id = ANY(%s) ORDER BY invoice_id, line_no",
            (invoice_ids,),
        )
        line_rows = await cur.fetchall()

    lines_by_invoice: dict[str, list[InvoiceLineItem]] = {iid: [] for iid in invoice_ids}
    for row in line_rows:
        lines_by_invoice[row["invoice_id"]].append(InvoiceLineItem(**row))
    invoices = [Invoice(**r, line_items=lines_by_invoice[r["id"]]) for r in invoice_rows]
    return invoices, truncated


# ---------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------


@mcp.tool
async def list_customers(name_contains: str | None = None) -> BoundedList[Customer]:
    """List customers, optionally filtered by case-insensitive substring on ``name``."""
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        if name_contains is None:
            await cur.execute(
                f"SELECT {_CUSTOMER_COLS} FROM customers ORDER BY id LIMIT %s",  # type: ignore[arg-type]  # noqa: S608
                (MAX_ROWS + 1,),
            )
        else:
            await cur.execute(
                f"SELECT {_CUSTOMER_COLS} FROM customers "  # type: ignore[arg-type]  # noqa: S608
                "WHERE name ILIKE %s ORDER BY id LIMIT %s",
                (f"%{name_contains}%", MAX_ROWS + 1),
            )
        rows = await cur.fetchall()
    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]
    return BoundedList[Customer](items=[Customer(**r) for r in rows], truncated=truncated)


@mcp.tool
async def get_customer(customer_id: str) -> Customer | None:
    """Fetch one customer by id. Returns ``None`` if not found."""
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_CUSTOMER_COLS} FROM customers WHERE id = %s",  # type: ignore[arg-type]  # noqa: S608
            (customer_id,),
        )
        row = await cur.fetchone()
    return Customer(**row) if row is not None else None


@mcp.tool
async def list_invoices(
    customer_id: str | None = None,
    status: InvoiceStatus | None = None,
) -> BoundedList[Invoice]:
    """List invoices, optionally filtered by customer and/or status.

    Each row includes its ``line_items``.
    """
    clauses: list[str] = []
    params: list[Any] = []
    if customer_id is not None:
        clauses.append("customer_id = %s")
        params.append(customer_id)
    if status is not None:
        clauses.append("status = %s")
        params.append(status)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    invoices, truncated = await _fetch_invoices(where_sql, tuple(params), limit=MAX_ROWS)
    return BoundedList[Invoice](items=invoices, truncated=truncated)


@mcp.tool
async def get_invoice(invoice_id: str) -> Invoice | None:
    """Fetch one invoice (with line items) by id. Returns ``None`` if not found."""
    invoices, _ = await _fetch_invoices("WHERE id = %s", (invoice_id,), limit=None)
    return invoices[0] if invoices else None


@mcp.tool
async def list_transactions(
    account_id: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> BoundedList[Transaction]:
    """List transactions, optionally bounded by account and posted-at date range (inclusive)."""
    clauses: list[str] = []
    params: list[Any] = []
    if account_id is not None:
        clauses.append("account_id = %s")
        params.append(account_id)
    if from_date is not None:
        clauses.append("posted_at >= %s")
        params.append(from_date)
    if to_date is not None:
        # Inclusive upper bound on a DATE against a TIMESTAMPTZ column means
        # "any timestamp on that date" → use < (to_date + 1 day) at the SQL level.
        clauses.append("posted_at < (%s::date + INTERVAL '1 day')")
        params.append(to_date)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_TRANSACTION_COLS} FROM transactions {where_sql} "  # type: ignore[arg-type]  # noqa: S608
            "ORDER BY posted_at, id LIMIT %s",
            (*params, MAX_ROWS + 1),
        )
        rows = await cur.fetchall()
    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]
    return BoundedList[Transaction](items=[Transaction(**r) for r in rows], truncated=truncated)


@mcp.tool
async def get_rate_card(
    service: str | None = None,
    role: str | None = None,
) -> BoundedList[RateCardEntry]:
    """Look up rate-card entries by service and/or role.

    At least one of ``service``, ``role`` must be supplied; rate-card lookups
    without either filter are rejected to keep the tool-call history meaningful
    for ``pre_action_proposal`` rules.
    """
    if service is None and role is None:
        raise ValueError("get_rate_card requires at least one of `service`, `role`.")
    clauses: list[str] = []
    params: list[Any] = []
    if service is not None:
        clauses.append("service = %s")
        params.append(service)
    if role is not None:
        clauses.append("role = %s")
        params.append(role)
    where_sql = f"WHERE {' AND '.join(clauses)}"
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_RATE_CARD_COLS} FROM rate_cards {where_sql} "  # type: ignore[arg-type]  # noqa: S608
            "ORDER BY id LIMIT %s",
            (*params, MAX_ROWS + 1),
        )
        rows = await cur.fetchall()
    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]
    return BoundedList[RateCardEntry](items=[RateCardEntry(**r) for r in rows], truncated=truncated)


@mcp.tool
async def list_time_entries(
    customer_id: str,
    project_id: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> BoundedList[TimeEntry]:
    """List time entries for a customer, optionally scoped to a project and/or date range."""
    clauses: list[str] = ["customer_id = %s"]
    params: list[Any] = [customer_id]
    if project_id is not None:
        clauses.append("project_id = %s")
        params.append(project_id)
    if from_date is not None:
        clauses.append("occurred_at >= %s")
        params.append(from_date)
    if to_date is not None:
        clauses.append("occurred_at <= %s")
        params.append(to_date)
    where_sql = f"WHERE {' AND '.join(clauses)}"
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_TIME_ENTRY_COLS} FROM time_entries {where_sql} "  # type: ignore[arg-type]  # noqa: S608
            "ORDER BY occurred_at, id LIMIT %s",
            (*params, MAX_ROWS + 1),
        )
        rows = await cur.fetchall()
    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]
    return BoundedList[TimeEntry](items=[TimeEntry(**r) for r in rows], truncated=truncated)


@mcp.tool
async def get_active_contract(customer_id: str, as_of_date: date) -> Contract | None:
    """Return the customer's contract that is active on ``as_of_date``, or ``None``.

    "Active" means ``effective_from <= as_of_date`` and (``expires_at IS NULL`` or
    ``expires_at > as_of_date``). If multiple contracts match, the most recently
    effective one wins.
    """
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_CONTRACT_COLS} FROM contracts "  # type: ignore[arg-type]  # noqa: S608
            "WHERE customer_id = %s AND effective_from <= %s "
            "AND (expires_at IS NULL OR expires_at > %s) "
            "ORDER BY effective_from DESC, id DESC LIMIT 1",
            (customer_id, as_of_date, as_of_date),
        )
        row = await cur.fetchone()
    return Contract(**row) if row is not None else None


@mcp.tool
async def list_contracts(customer_id: str) -> BoundedList[Contract]:
    """List all contracts (active, future, expired) for a customer."""
    pool = get_pool()
    async with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"SELECT {_CONTRACT_COLS} FROM contracts WHERE customer_id = %s "  # type: ignore[arg-type]  # noqa: S608
            "ORDER BY effective_from, id LIMIT %s",
            (customer_id, MAX_ROWS + 1),
        )
        rows = await cur.fetchall()
    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]
    return BoundedList[Contract](items=[Contract(**r) for r in rows], truncated=truncated)
