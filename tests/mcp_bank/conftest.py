"""Reusable fixtures for ``mcp_bank`` functional tests.

The fixtures (all session-scoped, all autouse where needed) wire up a
real Postgres ``compass_test`` database, apply ``db/schema.sql``,
truncate the bank-data tables, load a small hand-rolled corpus, and
hand the resulting async connection pool to ``mcp_bank.db.set_pool``
so the tool handlers can run unchanged.

All nine tools are read-only, so a single session-scoped seed is safe;
tests never mutate. To run against a different Postgres set
``COMPASS_TEST_PG_DSN`` (full DSN incl. database name) and
``COMPASS_TEST_ADMIN_DSN`` (full DSN to a database the user can
connect to in order to ``CREATE DATABASE`` the test db).
"""

import os
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from mcp_bank.db import set_pool

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "db" / "schema.sql"

DEFAULT_TEST_DSN = "postgresql://compass:compass@localhost:5432/compass_test"
DEFAULT_ADMIN_DSN = "postgresql://compass:compass@localhost:5432/postgres"


# ---------------------------------------------------------------------
# Fixture data — small enough to read inline. Designed so every tool
# and every branch (filter combinations, "no active contract", empty
# results, multi-row joins) is exercised.
# ---------------------------------------------------------------------


def _seed_rows() -> dict[str, list[dict[str, Any]]]:
    return {
        "customers": [
            {
                "id": "cust_alpha",
                "name": "Acme Corp",
                "email": "ap@acme.example",
                "address": "1 Acme Way",
                "kyc_status": "verified",
                "default_payment_terms_days": 30,
                "cohort": "mid_market",
                "created_at": datetime(2024, 1, 1, tzinfo=UTC),
            },
            {
                "id": "cust_beta",
                "name": "Bramble Industries",
                "email": "ap@bramble.example",
                "address": "2 Bramble Rd",
                "kyc_status": "pending",
                "default_payment_terms_days": 45,
                "cohort": "enterprise",
                "created_at": datetime(2024, 2, 1, tzinfo=UTC),
            },
            {
                "id": "cust_gamma",
                "name": "Apex Labs",
                "email": "ap@apex.example",
                "address": "3 Apex Ct",
                "kyc_status": "restricted",
                "default_payment_terms_days": 30,
                "cohort": "smb",
                "created_at": datetime(2024, 3, 1, tzinfo=UTC),
            },
        ],
        "accounts": [
            {
                "id": "acct_ops",
                "name": "Operating",
                "type": "operating",
                "currency": "USD",
                "balance_cents": 50_000_000,
                "opened_at": datetime(2024, 1, 1, tzinfo=UTC),
            },
            {
                "id": "acct_payroll",
                "name": "Payroll",
                "type": "payroll",
                "currency": "USD",
                "balance_cents": 20_000_000,
                "opened_at": datetime(2024, 1, 1, tzinfo=UTC),
            },
        ],
        "transactions": [
            {
                "id": "tx_001",
                "account_id": "acct_ops",
                "amount_cents": 1000,
                "direction": "debit",
                "counterparty": "Stripe",
                "memo": "fee",
                "category": "fees",
                "posted_at": datetime(2025, 1, 15, tzinfo=UTC),
                "related_invoice_id": None,
            },
            {
                "id": "tx_002",
                "account_id": "acct_ops",
                "amount_cents": 5000,
                "direction": "credit",
                "counterparty": "Acme Corp",
                "memo": "inv_001 payment",
                "category": "ar",
                "posted_at": datetime(2025, 2, 20, tzinfo=UTC),
                "related_invoice_id": "inv_001",
            },
            {
                "id": "tx_003",
                "account_id": "acct_payroll",
                "amount_cents": 200_000,
                "direction": "debit",
                "counterparty": "Gusto",
                "memo": "payroll feb",
                "category": "payroll",
                "posted_at": datetime(2025, 2, 25, tzinfo=UTC),
                "related_invoice_id": None,
            },
            {
                "id": "tx_004",
                "account_id": "acct_ops",
                "amount_cents": 800,
                "direction": "debit",
                "counterparty": "AWS",
                "memo": "march hosting",
                "category": "infra",
                "posted_at": datetime(2025, 3, 10, tzinfo=UTC),
                "related_invoice_id": None,
            },
        ],
        "rate_cards": [
            {
                "id": "rc_arch",
                "service": "Implementation",
                "role": "Solutions Architect",
                "unit": "hour",
                "list_amount_cents": 35000,
                "currency": "USD",
                "effective_from": date(2024, 6, 1),
                "effective_to": None,
            },
            {
                "id": "rc_eng",
                "service": "Implementation",
                "role": "Solutions Engineer",
                "unit": "hour",
                "list_amount_cents": 25000,
                "currency": "USD",
                "effective_from": date(2024, 6, 1),
                "effective_to": None,
            },
            {
                "id": "rc_trainer",
                "service": "Training",
                "role": "Trainer",
                "unit": "hour",
                "list_amount_cents": 18000,
                "currency": "USD",
                "effective_from": date(2024, 6, 1),
                "effective_to": None,
            },
            {
                "id": "rc_support_flat",
                "service": "Support",
                "role": None,
                "unit": "month",
                "list_amount_cents": 500_000,
                "currency": "USD",
                "effective_from": date(2024, 6, 1),
                "effective_to": None,
            },
        ],
        "projects": [
            {"id": "p_alpha_1", "customer_id": "cust_alpha", "name": "Phase 1", "status": "active"},
            {"id": "p_alpha_2", "customer_id": "cust_alpha", "name": "Phase 2", "status": "active"},
        ],
        # cust_alpha: one expired + one active contract (exercises the
        # "most recently effective wins" tiebreak in get_active_contract).
        # cust_gamma: only a future contract — as_of=today returns None.
        "contracts": [
            {
                "id": "ct_alpha_old",
                "customer_id": "cust_alpha",
                "kind": "msa",
                "effective_from": date(2024, 1, 1),
                "expires_at": date(2024, 12, 31),
                "currency": "USD",
                "billing_structure": {"kind": "t_and_m", "list_rates_apply": True},
                "rate_overrides": [],
                "monthly_hour_cap": None,
                "scope_summary": "2024 MSA",
                "source_doc_ref": None,
            },
            {
                "id": "ct_alpha_current",
                "customer_id": "cust_alpha",
                "kind": "sow",
                "effective_from": date(2025, 1, 1),
                "expires_at": None,
                "currency": "USD",
                "billing_structure": {
                    "kind": "flat_fee",
                    "total_amount_cents": 1_200_000,
                    "milestones": [
                        {"name": "kickoff", "amount_cents": 600_000, "due_date": "2025-02-01"},
                        {"name": "delivery", "amount_cents": 600_000, "due_date": "2025-06-01"},
                    ],
                },
                "rate_overrides": [
                    {"role": "Solutions Architect", "unit": "hour", "amount_cents": 40000},
                ],
                "monthly_hour_cap": 40,
                "scope_summary": "2025 SOW",
                "source_doc_ref": "s3://contracts/ct_alpha_current.pdf",
            },
            {
                "id": "ct_gamma_future",
                "customer_id": "cust_gamma",
                "kind": "retainer",
                "effective_from": date(2099, 1, 1),
                "expires_at": None,
                "currency": "USD",
                "billing_structure": {
                    "kind": "monthly_retainer",
                    "monthly_amount_cents": 100_000,
                    "covers": ["Support"],
                },
                "rate_overrides": [],
                "monthly_hour_cap": None,
                "scope_summary": "future retainer",
                "source_doc_ref": None,
            },
        ],
        "invoices": [
            {
                "id": "inv_001",
                "customer_id": "cust_alpha",
                "issued_at": datetime(2025, 2, 1, tzinfo=UTC),
                "due_at": datetime(2025, 3, 3, tzinfo=UTC),
                "total_cents": 600_000,
                "currency": "USD",
                "status": "paid",
                "payment_received_at": datetime(2025, 2, 20, tzinfo=UTC),
                "source_type": "contract",
                "contract_id": "ct_alpha_current",
                "dispute_flag": False,
            },
            {
                "id": "inv_002",
                "customer_id": "cust_alpha",
                "issued_at": datetime(2025, 3, 1, tzinfo=UTC),
                "due_at": datetime(2025, 3, 31, tzinfo=UTC),
                "total_cents": 75000,
                "currency": "USD",
                "status": "sent",
                "payment_received_at": None,
                "source_type": "time_tracking",
                "contract_id": "ct_alpha_current",
                "dispute_flag": False,
            },
            {
                "id": "inv_003",
                "customer_id": "cust_beta",
                "issued_at": datetime(2025, 3, 1, tzinfo=UTC),
                "due_at": datetime(2025, 4, 15, tzinfo=UTC),
                "total_cents": 100_000,
                "currency": "USD",
                "status": "draft",
                "payment_received_at": None,
                "source_type": "rate_card",
                "contract_id": None,
                "dispute_flag": False,
            },
        ],
        "invoice_line_items": [
            {
                "id": "inv_001_li_01",
                "invoice_id": "inv_001",
                "line_no": 1,
                "description": "Kickoff milestone",
                "quantity_micros": 1_000_000,
                "unit_amount_cents": 600_000,
                "line_total_cents": 600_000,
                "source_type": "contract",
                "source_refs": {"contract_id": "ct_alpha_current", "milestone": "kickoff"},
                "computation": "flat_fee.milestone=kickoff",
            },
            {
                "id": "inv_002_li_01",
                "invoice_id": "inv_002",
                "line_no": 1,
                "description": "Solutions Architect time",
                "quantity_micros": 2_000_000,
                "unit_amount_cents": 40000,
                "line_total_cents": 80000,
                "source_type": "time_tracking",
                "source_refs": {"time_entry_ids": ["te_001"]},
                "computation": "hours=2 * rate=40000",
            },
            {
                "id": "inv_002_li_02",
                "invoice_id": "inv_002",
                "line_no": 2,
                "description": "Trainer time",
                "quantity_micros": 1_000_000,
                "unit_amount_cents": 18000,
                "line_total_cents": 18000,
                "source_type": "time_tracking",
                "source_refs": {"time_entry_ids": ["te_003"]},
                "computation": "hours=1 * rate=18000",
            },
            {
                "id": "inv_003_li_01",
                "invoice_id": "inv_003",
                "line_no": 1,
                "description": "Support — Mar 2025",
                "quantity_micros": 1_000_000,
                "unit_amount_cents": 100_000,
                "line_total_cents": 100_000,
                "source_type": "rate_card",
                "source_refs": {"rate_card_id": "rc_support_flat"},
                "computation": "rate_card.list_amount_cents=100000",
            },
        ],
        "time_entries": [
            {
                "id": "te_001",
                "customer_id": "cust_alpha",
                "project_id": "p_alpha_1",
                "role": "Solutions Architect",
                "hours_micros": 2_000_000,
                "occurred_at": date(2025, 1, 10),
                "description": "design session",
                "invoiced": True,
            },
            {
                "id": "te_002",
                "customer_id": "cust_alpha",
                "project_id": "p_alpha_1",
                "role": "Solutions Engineer",
                "hours_micros": 3_000_000,
                "occurred_at": date(2025, 2, 15),
                "description": "implementation",
                "invoiced": False,
            },
            {
                "id": "te_003",
                "customer_id": "cust_alpha",
                "project_id": "p_alpha_2",
                "role": "Trainer",
                "hours_micros": 1_000_000,
                "occurred_at": date(2025, 2, 20),
                "description": "training session",
                "invoiced": True,
            },
        ],
    }


# Tables that need ``Jsonb()`` wrapping on insert. Mirrors the loader in
# synthetic_account_1/load_to_postgres.py.
_JSONB_COLS: dict[str, frozenset[str]] = {
    "contracts": frozenset({"billing_structure", "rate_overrides"}),
    "invoice_line_items": frozenset({"source_refs"}),
}

# Truncated in reverse-FK order so even non-CASCADE installs stay happy.
_TRUNCATE_ORDER: tuple[str, ...] = (
    "invoice_line_items",
    "disputes",
    "time_entries",
    "transactions",
    "invoices",
    "contracts",
    "projects",
    "rate_cards",
    "accounts",
    "customers",
)


def _test_dsn() -> str:
    return os.environ.get("COMPASS_TEST_PG_DSN", DEFAULT_TEST_DSN)


def _admin_dsn() -> str:
    return os.environ.get("COMPASS_TEST_ADMIN_DSN", DEFAULT_ADMIN_DSN)


def _db_name(dsn: str) -> str:
    name = urlparse(dsn).path.lstrip("/")
    if not name or not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise RuntimeError(f"refusing to manage db with unusual name: {name!r}")
    return name


async def _ensure_database(test_dsn: str) -> None:
    db = _db_name(test_dsn)
    admin = await psycopg.AsyncConnection.connect(_admin_dsn(), autocommit=True)
    async with admin, admin.cursor() as cur:
        await cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,))
        if await cur.fetchone() is None:
            await cur.execute(f'CREATE DATABASE "{db}"')  # type: ignore[arg-type]


async def _apply_schema_and_seed(test_dsn: str) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    rows_by_table = _seed_rows()
    conn = await psycopg.AsyncConnection.connect(test_dsn)
    async with conn, conn.cursor() as cur:
        await cur.execute(schema_sql)  # type: ignore[arg-type]
        await cur.execute(
            "TRUNCATE TABLE " + ", ".join(_TRUNCATE_ORDER) + " RESTART IDENTITY CASCADE"  # type: ignore[arg-type]
        )
        for table, rows in rows_by_table.items():
            if not rows:
                continue
            cols = sorted(rows[0].keys())
            placeholders = ", ".join(["%s"] * len(cols))
            cols_sql = ", ".join(cols)
            jsonb_cols = _JSONB_COLS.get(table, frozenset())
            wrapped = [tuple(Jsonb(r[c]) if c in jsonb_cols else r[c] for c in cols) for r in rows]
            await cur.executemany(
                f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders})",  # type: ignore[arg-type]  # noqa: S608
                wrapped,
            )
        await conn.commit()


@pytest.fixture(scope="session", autouse=True)
async def db_pool() -> AsyncIterator[AsyncConnectionPool[Any]]:
    """Session-scoped async pool wired into ``mcp_bank.db``.

    Creates ``compass_test`` if missing, applies ``db/schema.sql``,
    seeds the bank-data tables with the corpus from ``_seed_rows()``,
    then opens a pool and registers it via ``set_pool``. All nine
    ``mcp_bank`` tools are read-only, so session scope is safe.
    """
    dsn = _test_dsn()
    await _ensure_database(dsn)
    await _apply_schema_and_seed(dsn)
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo=dsn, min_size=1, max_size=4, open=False
    )
    await pool.open()
    set_pool(pool)
    try:
        yield pool
    finally:
        set_pool(None)
        await pool.close()
