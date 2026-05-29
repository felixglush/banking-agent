"""Worker tracing wiring.

Agent generation spans (the prompt/response text) only reach Langfuse when the
OpenAI Agents plugin runs with ``use_otel_instrumentation=True`` — that flag
installs the context-propagation glue (the ``on_trace_start`` monkeypatch and the
OTel-aware boundary interceptor) that parents the agent's OpenInference spans into
the workflow's OTel trace. A bare ``OpenAIAgentsInstrumentor().instrument()`` lacks
that glue, so the spans never land inside the workflow trace.

The flag must be gated on tracing being enabled: ``OpenAIAgentsPlugin.__init__``
raises ``ValueError`` unless the global OTel tracer provider is already a
``ReplaySafeTracerProvider``, which only happens when Langfuse tracing is wired up.
"""

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from temporalio.contrib.opentelemetry import create_tracer_provider

from workflows.send_invoice.worker import build_plugin


def test_build_plugin_disables_otel_bridge_when_tracing_off() -> None:
    plugin = build_plugin(tracing_enabled=False)
    assert plugin._use_otel_instrumentation is False  # pyright: ignore[reportPrivateUsage]


def test_build_plugin_enables_otel_bridge_when_tracing_on() -> None:
    # The plugin reads the *global* OTel provider in __init__; set a ReplaySafe
    # one first. set_tracer_provider is set-once per process, so if another test
    # already set a non-ReplaySafe provider we can't assert this path here.
    provider = create_tracer_provider(
        resource=Resource.create({"service.name": "test-worker-tracing"})
    )
    otel_trace.set_tracer_provider(provider)
    if otel_trace.get_tracer_provider() is not provider:
        pytest.skip("global OTel tracer provider already set; cannot assert True-path")

    plugin = build_plugin(tracing_enabled=True)
    assert plugin._use_otel_instrumentation is True  # pyright: ignore[reportPrivateUsage]
