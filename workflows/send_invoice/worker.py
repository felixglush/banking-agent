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
from typing import cast

from agents.mcp import MCPServerStdio, MCPServerStdioParams, create_static_tool_filter
from agents.mcp.server import MCPServer
from dotenv import load_dotenv
from langfuse import Langfuse
from openinference.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
from temporalio.client import Client
from temporalio.contrib.openai_agents import (
    ModelActivityParameters,
    OpenAIAgentsPlugin,
    StatefulMCPServerProvider,
)
from temporalio.contrib.opentelemetry import OpenTelemetryPlugin, create_tracer_provider
from temporalio.worker import Worker

from workflows.send_invoice.activities import (
    audit_log,
    evaluate_policy,
    execute_send,
    resolve_customer_contract,
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


def _setup_tracing() -> Langfuse | None:
    """Wire Langfuse tracing if ``LANGFUSE_PUBLIC_KEY`` is set.

    Returns the Langfuse client so the caller can ``flush()`` on shutdown,
    or None if Langfuse env vars are unset (tracing disabled).

    Three layers cooperate:

    * ``create_tracer_provider()`` builds a replay-safe TracerProvider so
      Temporal's ``OpenTelemetryPlugin`` can emit deterministic span IDs
      during workflow replay.
    * ``OpenAIAgentsInstrumentor`` records LLM/tool spans from the
      OpenAI Agents SDK onto that provider.
    * ``Langfuse(tracer_provider=...)`` attaches its OTLP exporter to the
      same provider, so all three layers ship spans to one place.
    """
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None

    provider = create_tracer_provider(
        resource=Resource.create({"service.name": "compass-send-invoice"}),
    )
    otel_trace.set_tracer_provider(provider)
    OpenAIAgentsInstrumentor().instrument()
    # ReplaySafeTracerProvider implements the abstract OTel TracerProvider
    # interface but is not a subclass of the SDK's concrete TracerProvider.
    # Langfuse only calls add_span_processor(), which the replay-safe one
    # supports; the cast acknowledges this structural compatibility.
    langfuse = Langfuse(tracer_provider=cast(SdkTracerProvider, provider))
    if not langfuse.auth_check():
        logging.warning("Langfuse auth_check failed; traces may not export")
    return langfuse


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
        # Expose only the tools the send_invoice agent needs to draft an
        # invoice. Drops list_transactions (payments — irrelevant) and
        # list_contracts (get_active_contract suffices), cutting tool-schema
        # tokens re-sent every turn and removing distractor tools that can lead
        # to wrong tool/source picks. get_invoice/list_invoices stay for the
        # user_specified (ad-hoc) path.
        tool_filter=create_static_tool_filter(
            allowed_tool_names=[
                "list_customers",
                "get_customer",
                "get_active_contract",
                "get_rate_card",
                "list_time_entries",
                "list_invoices",
                "get_invoice",
            ],
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

    langfuse = _setup_tracing()

    plugins: list[OpenAIAgentsPlugin | OpenTelemetryPlugin] = [build_plugin()]
    if langfuse is not None:
        # OpenTelemetryPlugin is experimental in temporalio==1.27 — adds
        # OTel spans for workflow tasks + activities so the Langfuse trace
        # tree shows the full DAG, not just LLM calls.
        plugins.append(OpenTelemetryPlugin(add_temporal_spans=True))

    target = os.environ.get("TEMPORAL_TARGET", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(target, namespace=namespace, plugins=plugins)

    logging.info("send-invoice worker connected to %s ns=%s", target, namespace)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[SendInvoiceWorkflow],
        activities=[evaluate_policy, execute_send, audit_log, resolve_customer_contract],
        workflow_runner=build_sandboxed_runner(),
    )
    logging.info("send-invoice worker polling task_queue=%s", TASK_QUEUE)
    try:
        await worker.run()
    finally:
        if langfuse is not None:
            langfuse.flush()
            langfuse.shutdown()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
