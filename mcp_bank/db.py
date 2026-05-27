"""Process-level async psycopg connection pool for the ``bank`` MCP.

The pool is configured at server startup by ``server.lifespan`` from
``COMPASS_PG_DSN`` and torn down on shutdown. Tool handlers call
``get_pool()`` to acquire connections; tests bypass the lifespan and
inject their own pool via ``set_pool()``.

The pool type is intentionally ``AsyncConnectionPool[Any]`` — handlers
configure ``row_factory`` per-cursor rather than per-connection, so
the pool only needs to hand out generic ``AsyncConnection`` instances.
"""

from typing import Any

from psycopg_pool import AsyncConnectionPool

_pool: AsyncConnectionPool[Any] | None = None


def get_pool() -> AsyncConnectionPool[Any]:
    if _pool is None:
        raise RuntimeError(
            "mcp_bank.db: connection pool not initialized — "
            "the server lifespan or a test fixture must call set_pool() first."
        )
    return _pool


def set_pool(pool: AsyncConnectionPool[Any] | None) -> None:
    global _pool  # noqa: PLW0603 — single owner of the process-level pool
    _pool = pool
