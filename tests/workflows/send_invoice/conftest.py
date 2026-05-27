"""Test fixtures for the SendInvoice workflow.

Reuses ``compass_test`` (created and seeded by ``tests/mcp_bank/conftest``)
so the audit_log / invoices writes have something to write to. The DSN
is passed to the workflow's activities via the ``COMPASS_PG_DSN`` env
var so production code and tests use the same lookup path.

The workflow tests run against the in-memory ``WorkflowEnvironment``
(``start_time_skipping``) with a mocked OpenAI model — no network, no
MCP subprocess, no agent reasoning. Stage 4's correctness for the
orchestration code is independent of model behaviour; live-model smoke
is documented separately in ``workflows/send_invoice/README.md``.
"""

import os
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from agents.mcp.server import MCPServer
from mcp import GetPromptResult, ListPromptsResult
from mcp import Tool as MCPTool
from mcp.types import CallToolResult
from temporalio.client import Client
from temporalio.contrib.openai_agents import (
    ModelActivityParameters,
    StatefulMCPServerProvider,
)
from temporalio.contrib.openai_agents.testing import AgentEnvironment, TestModel
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from workflows.send_invoice.activities import (
    audit_log,
    evaluate_policy,
    execute_send,
)
from workflows.send_invoice.sandbox import build_sandboxed_runner
from workflows.send_invoice.workflow import SendInvoiceWorkflow

TASK_QUEUE = "send-invoice-test"


class _NoOpMCPServer(MCPServer):
    """No-op MCP server for the workflow tests.

    The workflow opens ``stateful_mcp_server("bank")`` unconditionally;
    the plugin spawns the registered factory once per workflow to back
    that reference. The ``TestModel`` never calls a tool, so the only
    surface that must respond is ``list_tools`` — and an empty list is
    correct.
    """

    @property
    def name(self) -> str:
        return "bank"

    async def connect(self) -> None: ...

    async def cleanup(self) -> None: ...

    async def list_tools(
        self,
        run_context: Any = None,  # noqa: ARG002
        agent: Any = None,  # noqa: ARG002
    ) -> list[MCPTool]:
        return []

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,  # noqa: ARG002
        meta: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> CallToolResult:
        raise RuntimeError(f"unexpected call_tool({tool_name!r}) in workflow test")

    async def list_prompts(self) -> ListPromptsResult:
        return ListPromptsResult(prompts=[])

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> GetPromptResult:
        raise RuntimeError(f"unexpected get_prompt({name!r}) in workflow test")


@pytest.fixture(autouse=True)
async def _truncate_runtime_tables() -> None:  # pyright: ignore[reportUnusedFunction]
    """Wipe ``audit_log`` / ``invoices`` / ``invoice_line_items`` before each test.

    The bank-data seed from ``tests/mcp_bank/conftest`` populates a few
    invoice rows that we don't want polluting the workflow's runtime
    writes. We can't TRUNCATE invoices alone (FK from
    ``invoice_line_items``), so we go in FK order.
    """
    dsn = os.environ.get(
        "COMPASS_TEST_PG_DSN", "postgresql://compass:compass@localhost:5432/compass_test"
    )
    os.environ["COMPASS_PG_DSN"] = dsn
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.cursor() as cur:
        await cur.execute("TRUNCATE TABLE invoice_line_items, invoices, audit_log RESTART IDENTITY")
        await conn.commit()


@pytest.fixture
async def temporal_client(model: TestModel) -> AsyncIterator[Client]:
    """Spin up an ephemeral Temporal server with the OpenAI Agents plugin.

    ``time_skipping`` lets us advance through the long approval-timeout
    in the timeout test without sleeping for real time.
    """
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        AgentEnvironment(
            model=model,
            model_params=ModelActivityParameters(
                start_to_close_timeout=timedelta(seconds=30),
            ),
            mcp_server_providers=[
                StatefulMCPServerProvider(name="bank", server_factory=lambda _: _NoOpMCPServer()),
            ],
        ) as agent_env,
    ):
        yield agent_env.applied_on_client(env.client)


@pytest.fixture
async def worker(temporal_client: Client) -> AsyncIterator[Worker]:
    worker = Worker(
        temporal_client,
        task_queue=TASK_QUEUE,
        workflows=[SendInvoiceWorkflow],
        activities=[evaluate_policy, execute_send, audit_log],
        workflow_runner=build_sandboxed_runner(),
    )
    async with worker:
        yield worker


def proposal_dict(**overrides: Any) -> dict[str, Any]:
    """Sensible default ``InvoiceProposal.model_dump()`` for tests."""
    base: dict[str, Any] = {
        "customer_id": "cust_alpha",
        "currency": "USD",
        "total_cents": 80000,
        "payment_terms_days": 30,
        "source_type": "time_tracking",
        "contract_id": "ct_alpha_current",
        "line_items": [
            {
                "description": "Solutions Architect time",
                "quantity_micros": 2_000_000,
                "unit_amount_cents": 40000,
                "line_total_cents": 80000,
                "source_type": "time_tracking",
                "source_refs": ["te_001"],
                "computation": "2h * $400/hr per contract ct_alpha_current",
            }
        ],
        "notes": None,
    }
    base.update(overrides)
    return base
