"""Unit tests for `_enrich_langfuse_trace` — trace-level input/output.

The function reads the active OTel span via `otel_trace.get_current_span()`
and sets Langfuse trace attributes on it. We patch the span and capture
`set_attribute` calls keyed by attribute name.
"""

import json
from unittest.mock import MagicMock, patch

from langfuse import LangfuseOtelSpanAttributes

# Unit test of the module-private trace-enrichment helper — importing it
# directly is the documented intent.
from workflows.send_invoice.activities import (
    AuditEvent,
    _enrich_langfuse_trace,  # pyright: ignore[reportPrivateUsage]
)


def _capture(event: AuditEvent) -> dict[str, object]:
    """Run _enrich against a recording span; return {attr_name: value}."""
    span = MagicMock()
    span.is_recording.return_value = True
    attrs: dict[str, object] = {}

    def _set(key: str, value: object) -> None:
        attrs[key] = value

    span.set_attribute.side_effect = _set
    with patch(
        "workflows.send_invoice.activities.otel_trace.get_current_span",
        return_value=span,
    ):
        # Exercising the module-private enrichment helper directly is the
        # documented intent of this unit test.
        _enrich_langfuse_trace(event)  # pyright: ignore[reportPrivateUsage]
    return attrs


def _event(**kw: object) -> AuditEvent:
    base: dict[str, object] = dict(
        workflow_run_id="eval-ir_0001-abcd",
        sequence_no=1,
        phase="input_validation",
        event_kind="intent_classified",
        payload={},
    )
    base.update(kw)
    return AuditEvent(**base)  # type: ignore[arg-type]


def test_no_op_when_span_not_recording() -> None:
    span = MagicMock()
    span.is_recording.return_value = False
    with patch(
        "workflows.send_invoice.activities.otel_trace.get_current_span",
        return_value=span,
    ):
        _enrich_langfuse_trace(_event())
    span.set_attribute.assert_not_called()


def test_input_set_from_user_message() -> None:
    attrs = _capture(
        _event(
            event_kind="intent_classified",
            payload={
                "user_message": "Send invoice for Acme Corp",
                "classification": {"intent": "send_invoice"},
            },
        )
    )
    assert attrs[LangfuseOtelSpanAttributes.TRACE_INPUT] == "Send invoice for Acme Corp"


def test_no_input_attr_when_user_message_absent() -> None:
    attrs = _capture(
        _event(
            event_kind="executed",
            phase="audit_validation",
            payload={"invoice_id": "inv_1", "total_cents": 5000},
        )
    )
    assert LangfuseOtelSpanAttributes.TRACE_INPUT not in attrs


def test_output_sent_carries_outcome_and_invoice() -> None:
    attrs = _capture(
        _event(
            phase="audit_validation",
            event_kind="executed",
            payload={"invoice_id": "inv_42", "total_cents": 5000},
            is_terminal_event=True,
        )
    )
    out = json.loads(attrs[LangfuseOtelSpanAttributes.TRACE_OUTPUT])  # type: ignore[arg-type]
    assert out["outcome"] == "sent"
    assert out["invoice_id"] == "inv_42"
    assert out["total_cents"] == 5000


def test_output_declined_carries_detail() -> None:
    attrs = _capture(
        _event(phase="pre_execute", event_kind="declined", payload={"notes": "amount too high"})
    )
    out = json.loads(attrs[LangfuseOtelSpanAttributes.TRACE_OUTPUT])  # type: ignore[arg-type]
    assert out["outcome"] == "declined"
    assert out["detail"] == "amount too high"


def test_output_timeout_distinguished_from_decline() -> None:
    attrs = _capture(
        _event(phase="pre_execute", event_kind="declined", payload={"reason": "approval_timeout"})
    )
    out = json.loads(attrs[LangfuseOtelSpanAttributes.TRACE_OUTPUT])  # type: ignore[arg-type]
    assert out["outcome"] == "timeout"


def test_output_policy_rejected() -> None:
    attrs = _capture(
        _event(
            phase="pre_action_proposal",
            event_kind="policy_rejected",
            payload={"message": "prohibit_exceed_contract_cap tripped"},
        )
    )
    out = json.loads(attrs[LangfuseOtelSpanAttributes.TRACE_OUTPUT])  # type: ignore[arg-type]
    assert out["outcome"] == "policy_rejected"
    assert "prohibit_exceed_contract_cap" in out["detail"]


def test_output_agent_no_output_maps_by_phase() -> None:
    early = _capture(
        _event(
            phase="input_validation", event_kind="agent_no_output", payload={"user_message": "hi"}
        )
    )
    late = _capture(
        _event(
            phase="pre_action_proposal",
            event_kind="agent_no_output",
            payload={"user_message": "hi"},
        )
    )
    assert json.loads(early[LangfuseOtelSpanAttributes.TRACE_OUTPUT])["outcome"] == "unsupported"  # type: ignore[arg-type]
    assert json.loads(late[LangfuseOtelSpanAttributes.TRACE_OUTPUT])["outcome"] == "policy_rejected"  # type: ignore[arg-type]


def test_no_output_attr_for_non_terminal_event() -> None:
    attrs = _capture(
        _event(
            phase="pre_execute",
            event_kind="approval_signal",
            payload={"approval": {"approved": True}},
        )
    )
    assert LangfuseOtelSpanAttributes.TRACE_OUTPUT not in attrs


def test_tags_always_include_workflow_run_id() -> None:
    attrs = _capture(_event())
    tags = json.loads(attrs[LangfuseOtelSpanAttributes.TRACE_TAGS])  # type: ignore[arg-type]
    assert "wf:eval-ir_0001-abcd" in tags
