"""Smoke tests for SQL generation in load_to_postgres.py.

A full integration test requires a live Postgres; that's gated behind
``COMPASS_PG_DSN`` (see test_load_to_postgres_integration when added).
Here we just lock the shape of the SQL the loader generates.
"""

from __future__ import annotations

from synthetic_account_1.load_to_postgres import (
    BANK_DATA_TABLES,
    JSONB_COLUMNS,
    build_insert_sql,
    build_truncate_sql,
)


def test_truncate_includes_all_bank_data_tables() -> None:
    sql = build_truncate_sql()
    for table in BANK_DATA_TABLES:
        assert table in sql, f"TRUNCATE missing table {table}"
    assert "RESTART IDENTITY CASCADE" in sql


def test_truncate_does_not_include_runtime_owned_tables() -> None:
    sql = build_truncate_sql()
    for forbidden in ("audit_log", "policy_snapshots", "eval_runs"):
        # `forbidden` could only appear if we erroneously added it; check
        # for the SQL identifier exactly.
        assert f" {forbidden}" not in sql, f"loader must not truncate {forbidden}"
        assert not sql.endswith(forbidden), f"loader must not truncate {forbidden}"


def test_insert_sql_shape() -> None:
    sql = build_insert_sql("customers", ["id", "name", "kyc_status"])
    assert sql == "INSERT INTO customers (id, name, kyc_status) VALUES (%s, %s, %s)"


def test_jsonb_columns_declared_for_contracts_and_line_items() -> None:
    assert "billing_structure" in JSONB_COLUMNS["contracts"]
    assert "rate_overrides" in JSONB_COLUMNS["contracts"]
    assert "source_refs" in JSONB_COLUMNS["invoice_line_items"]
