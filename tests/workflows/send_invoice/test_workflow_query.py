"""The agent_activity query exposes the workflow's tool calls + reasoning so the
eval runner can fold them into the trace (the OpenInference LLM/tool spans orphan
into separate traces under temporalio's experimental use_otel integration)."""

from workflows.send_invoice.workflow import SendInvoiceWorkflow


def test_agent_activity_query_returns_tool_calls_and_reasoning() -> None:
    wf = SendInvoiceWorkflow()
    wf._tool_calls = [  # pyright: ignore[reportPrivateUsage]
        {"tool_name": "get_customer", "args": {"id": "c1"}, "result": {"name": "Acme"}}
    ]
    wf._reasoning_text = "resolved the customer"  # pyright: ignore[reportPrivateUsage]

    out = wf.agent_activity()

    assert out["tool_calls"][0]["tool_name"] == "get_customer"
    assert out["reasoning"] == "resolved the customer"


def test_agent_activity_query_empty_by_default() -> None:
    out = SendInvoiceWorkflow().agent_activity()
    assert out == {"tool_calls": [], "reasoning": ""}
