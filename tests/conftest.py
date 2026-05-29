"""Session-wide test-database bootstrap.

Every Postgres-backed test (the compass.eval stores, mcp_bank, the
workflow suite) targets a single ``compass_test`` database. Creating it
and applying ``db/schema.sql`` used to live only in
``tests/mcp_bank/conftest.py``, whose session fixture fires the first
time a test *under tests/mcp_bank/* runs. ``tests/compass/`` sorts
first, so its Postgres tests ran before the database existed and failed
with ``database "compass_test" does not exist``.

This root fixture creates the database + applies the schema before ANY
test, regardless of collection order. ``db/schema.sql`` is idempotent
(``CREATE ... IF NOT EXISTS`` / ``ADD COLUMN IF NOT EXISTS``), so
mcp_bank's seed fixture re-applying it is harmless.

Gated on ``COMPASS_TEST_PG_DSN`` — the same signal the Postgres tests
themselves use to skip. When it is unset (local runs without Postgres)
this is a no-op, so the non-Postgres suite still runs and the
Postgres-backed tests skip rather than erroring the whole session.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


@pytest.fixture(scope="session", autouse=True)
async def _ensure_test_database() -> None:  # pyright: ignore[reportUnusedFunction]
    test_dsn = os.environ.get("COMPASS_TEST_PG_DSN")
    if not test_dsn:
        return  # no Postgres configured — PG-backed tests skip themselves
    admin_dsn = os.environ.get(
        "COMPASS_TEST_ADMIN_DSN", "postgresql://compass:compass@localhost:5432/postgres"
    )
    db_name = urlparse(test_dsn).path.lstrip("/")

    admin = await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True)
    async with admin, admin.cursor() as cur:
        await cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
        if await cur.fetchone() is None:
            await cur.execute(f'CREATE DATABASE "{db_name}"')  # type: ignore[arg-type]

    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn = await psycopg.AsyncConnection.connect(test_dsn)
    async with conn, conn.cursor() as cur:
        await cur.execute(schema_sql)  # type: ignore[arg-type]
        await conn.commit()
