import os

import psycopg
import pytest

# Re-export the Temporal workflow harness so adversarial-probe tests in this
# package can drive the real SendInvoiceWorkflow. The fixtures resolve their
# ``model`` dependency from the requesting test module (see test_runner_probe).
from tests.workflows.send_invoice.conftest import temporal_client, worker

__all__ = ["temporal_client", "worker"]


@pytest.fixture
def db_dsn() -> str:
    dsn = os.environ.get("COMPASS_TEST_PG_DSN")
    if not dsn:
        pytest.skip("COMPASS_TEST_PG_DSN not set")
    return dsn


@pytest.fixture
async def invoice_runtime_db(db_dsn: str) -> str:
    """Point the workflow activities at compass_test and clear the runtime
    tables before a probe. ``db_dsn`` skips when COMPASS_TEST_PG_DSN is unset."""
    os.environ["COMPASS_PG_DSN"] = db_dsn
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn, conn.cursor() as cur:
        await cur.execute("TRUNCATE TABLE invoice_line_items, invoices, audit_log RESTART IDENTITY")
        await conn.commit()
    return db_dsn
