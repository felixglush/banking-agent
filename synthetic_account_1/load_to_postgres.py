"""Idempotent bulk loader: TRUNCATE + reload bank-data tables from JSONL.

Bank-data tables only — ``audit_log``, ``policy_snapshots``,
``eval_runs``, ``eval_results`` are runtime-owned and never touched by
this loader. Per build-plan §Validation Criteria 1 the criterion
"identical DB state on re-run" applies to bank-data tables only.

DSN is read from ``COMPASS_PG_DSN``. The schema DDL at ``db/schema.sql``
is always executed before TRUNCATE so the loader is usable against a
fresh Postgres (the DDL uses ``CREATE TABLE IF NOT EXISTS`` so the call
is idempotent).

The whole load runs inside a single transaction — TRUNCATE + reload
either both succeed or both roll back. No partial-state windows.

Run as a module::

    COMPASS_PG_DSN=postgres://... uv run python -m synthetic_account_1.load_to_postgres
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import psycopg
from psycopg.types.json import Jsonb

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
SCHEMA_PATH = REPO_ROOT / "db" / "schema.sql"
GENERATED = PACKAGE_DIR / "generated"
BANK = GENERATED / "bank"
INTERNAL = GENERATED / "account_internal"

# Bank-data tables only. Order matters: truncated in reverse-FK order
# (CASCADE on TRUNCATE clears FKs but explicit ordering protects against
# someone adding a non-CASCADE constraint later); inserted in FK order.
BANK_DATA_TABLES: tuple[str, ...] = (
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

# JSONB columns that need explicit psycopg Jsonb() wrapping.
JSONB_COLUMNS: dict[str, frozenset[str]] = {
    "contracts": frozenset({"billing_structure", "rate_overrides"}),
    "invoice_line_items": frozenset({"source_refs"}),
}


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(cast(dict[str, Any], json.loads(line)))
    return rows


def _read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return cast(list[dict[str, Any]], json.load(f))


def _rows_for(table: str) -> list[dict[str, Any]]:
    """Map table → JSONL/JSON source file."""
    if table == "customers":
        return _iter_jsonl(BANK / "customers.jsonl")
    if table == "accounts":
        return _read_json(BANK / "accounts.json")
    if table == "transactions":
        return _iter_jsonl(BANK / "transactions.jsonl")
    if table == "invoices":
        return _iter_jsonl(BANK / "invoices.jsonl")
    if table == "invoice_line_items":
        return _iter_jsonl(BANK / "invoice_line_items.jsonl")
    if table == "disputes":
        return _iter_jsonl(BANK / "disputes.jsonl")
    if table == "rate_cards":
        return _iter_jsonl(INTERNAL / "rate_card_lookup.jsonl")
    if table == "projects":
        return _iter_jsonl(INTERNAL / "projects.jsonl")
    if table == "contracts":
        return _iter_jsonl(INTERNAL / "contracts.jsonl")
    if table == "time_entries":
        return _iter_jsonl(INTERNAL / "time_tracking.jsonl")
    raise RuntimeError(f"no source mapping for table {table!r}")


def _column_order(table: str, sample: dict[str, Any]) -> list[str]:
    """Stable column order derived from sorted keys of the first row.

    The COPY/INSERT layer needs a fixed column order across all rows;
    sorting deterministically is the simplest way to guarantee that
    every JSONL row of the same shape produces the same SQL.
    """
    return sorted(sample.keys())


def _wrap_jsonb(table: str, column: str, value: Any) -> Any:
    if column in JSONB_COLUMNS.get(table, frozenset()):
        return Jsonb(value)
    return value


def build_truncate_sql() -> str:
    """Compose the TRUNCATE statement for bank-data tables."""
    return "TRUNCATE TABLE " + ", ".join(BANK_DATA_TABLES) + " RESTART IDENTITY CASCADE"


def build_insert_sql(table: str, columns: Sequence[str]) -> str:
    cols_sql = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    return f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders})"


def load(dsn: str) -> dict[str, int]:
    """Run the full reload. Returns per-table row counts."""
    ddl = SCHEMA_PATH.read_text(encoding="utf-8")
    counts: dict[str, int] = {}

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # 1. Idempotent DDL (CREATE TABLE IF NOT EXISTS …).
            cur.execute(ddl)  # type: ignore[arg-type]
            # 2. Single transaction for the truncate + reload.
            cur.execute(build_truncate_sql())  # type: ignore[arg-type]
            for table in reversed(BANK_DATA_TABLES):
                rows = _rows_for(table)
                if not rows:
                    counts[table] = 0
                    continue
                columns = _column_order(table, rows[0])
                sql = build_insert_sql(table, columns)
                tuples: list[tuple[Any, ...]] = [
                    tuple(_wrap_jsonb(table, c, r.get(c)) for c in columns) for r in rows
                ]
                cur.executemany(sql, tuples)  # type: ignore[arg-type]
                counts[table] = len(tuples)
        conn.commit()
    return counts


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synthetic_account_1.load_to_postgres")
    parser.add_argument(
        "--dsn",
        default=None,
        help="Postgres DSN. Defaults to env var COMPASS_PG_DSN.",
    )
    args = parser.parse_args(argv)
    dsn = args.dsn or os.environ.get("COMPASS_PG_DSN")
    if not dsn:
        print(
            "load_to_postgres: no DSN — pass --dsn or set COMPASS_PG_DSN.",
            file=sys.stderr,
        )
        return 2
    counts = load(dsn)
    for table in BANK_DATA_TABLES:
        print(f"  {table}: {counts.get(table, 0)}")
    print("load: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
