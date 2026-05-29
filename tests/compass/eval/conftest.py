import os

import pytest


@pytest.fixture
def db_dsn() -> str:
    dsn = os.environ.get("COMPASS_TEST_PG_DSN")
    if not dsn:
        pytest.skip("COMPASS_TEST_PG_DSN not set")
    return dsn
