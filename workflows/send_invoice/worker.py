"""Temporal worker for the SendInvoice workflow.

Run with:

    uv run python -m workflows.send_invoice.worker

Prereqs in separate terminals:

    docker compose up -d        # postgres
    temporal server start-dev   # local Temporal (in-memory backend)

Reads ``.env.local`` at the repo root for ``OPENAI_API_KEY`` (required)
and ``OPENAI_MODEL`` (optional; defaults to ``gpt-5-nano``).
"""

import asyncio
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from agents.mcp import MCPServerStdio, MCPServerStdioParams
from agents.mcp.server import MCPServer
from dotenv import load_dotenv
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from temporalio.client import Client
from temporalio.contrib.openai_agents import (
    ModelActivityParameters,
    OpenAIAgentsPlugin,
    StatefulMCPServerProvider,
)
from temporalio.worker import Worker

from workflows.send_invoice.activities import (
    audit_log,
    evaluate_policy,
    execute_send,
)
from workflows.send_invoice.sandbox import build_sandboxed_runner
from workflows.send_invoice.workflow import SendInvoiceWorkflow

TASK_QUEUE = "send-invoice"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )


def _setup_tracing() -> None:
    """
    Wire Langfuse / OTLP tracing if ``LANGFUSE_OTLP_ENDPOINT`` is set.
    """
    endpoint = os.environ.get("LANGFUSE_OTLP_ENDPOINT")
    if not endpoint:
        return

    headers: dict[str, str] = {}
    auth = os.environ.get("LANGFUSE_OTLP_AUTH")
    if auth:
        headers["Authorization"] = auth
    provider = TracerProvider(resource=Resource.create({"service.name": "compass-send-invoice"}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers))
    )
    trace.set_tracer_provider(provider)
    OpenAIAgentsInstrumentor().instrument()


def _mcp_factory(_arg: object | None) -> MCPServer:
    """Spawn the ``bank`` MCP as a child stdio subprocess.

    The MCP server reads its own ``COMPASS_PG_DSN`` from the environment
    we forward here, so the worker and the MCP point at the same Postgres
    even if the worker has a non-default DSN.
    """
    env: dict[str, str] = {"PATH": os.environ.get("PATH", "")}
    dsn = os.environ.get("COMPASS_PG_DSN")
    if not dsn:
        raise RuntimeError("COMPASS_PG_DSN must be set for the MCP subprocess.")
    env["COMPASS_PG_DSN"] = dsn
    return MCPServerStdio(
        name="bank",
        params=MCPServerStdioParams(
            command="uv",
            args=["run", "python", "-m", "mcp_bank"],
            env=env,
            cwd=str(REPO_ROOT),
        ),
    )


def build_plugin() -> OpenAIAgentsPlugin:
    """Plugin shared between production worker and the test harness.

    The MCP call_tool activity's retry policy is set inside the workflow
    (``stateful_mcp_server(..., config=ActivityConfig(retry_policy=...))``)
    because tool-call errors are almost always "the model called this
    wrong" — non-retryable. The plugin itself only configures the LLM
    activity's timeout; default LLM retries are fine for transient
    upstream failures.
    """
    return OpenAIAgentsPlugin(
        model_params=ModelActivityParameters(
            start_to_close_timeout=timedelta(seconds=120),
        ),
        mcp_server_providers=[
            StatefulMCPServerProvider(name="bank", server_factory=_mcp_factory),
        ],
    )


async def amain() -> None:
    load_dotenv(REPO_ROOT / ".env.local")
    _setup_logging()

    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "ERROR: OPENAI_API_KEY not set. Put it in .env.local at the repo root.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    _setup_tracing()

    target = os.environ.get("TEMPORAL_TARGET", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(target, namespace=namespace, plugins=[build_plugin()])

    logging.info("send-invoice worker connected to %s ns=%s", target, namespace)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[SendInvoiceWorkflow],
        activities=[evaluate_policy, execute_send, audit_log],
        workflow_runner=build_sandboxed_runner(),
    )
    logging.info("send-invoice worker polling task_queue=%s", TASK_QUEUE)
    await worker.run()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
