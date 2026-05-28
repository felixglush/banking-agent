"""Pure-function tests for workflows/send_invoice/context.py.

No Temporal, no OpenAI, no MCP. Synthetic RunResult-shaped inputs.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from compass.policy import ToolCallRecord
from workflows.send_invoice.context import (
    extract_reasoning_text,
    extract_tool_calls,
    hash_proposal,
    project_resolved_entities,
)

# ---------------------------------------------------------------------
# Synthetic RunResult builders
# ---------------------------------------------------------------------


def _tool_call_item(
    name: str,
    args: dict[str, Any],
    output: Any,
    *,
    call_id: str | None = None,
) -> list[SimpleNamespace]:
    """Build the (tool_call_item, tool_call_output_item) pair the SDK emits.

    extract_tool_calls pairs them by call_id; we return both as one
    list so call sites can spread into _run_result(...).
    """
    cid = call_id or f"call_{name}"
    call_raw = SimpleNamespace(name=name, arguments=json.dumps(args), call_id=cid)
    output_raw = SimpleNamespace(
        call_id=cid,
        output=json.dumps(output) if not isinstance(output, str) else output,
    )
    return [
        SimpleNamespace(type="tool_call_item", raw_item=call_raw),
        SimpleNamespace(type="tool_call_output_item", raw_item=output_raw),
    ]


def _message_item(role: str, text: str) -> SimpleNamespace:
    raw = SimpleNamespace(role=role, content=text)
    return SimpleNamespace(type="message_output_item", raw_item=raw)


def _run_result(items: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(new_items=items)


# ---------------------------------------------------------------------
# extract_tool_calls
# ---------------------------------------------------------------------


def test_extract_tool_calls_returns_one_per_call() -> None:
    rr = _run_result(
        [
            *_tool_call_item("list_customers", {"name": "Acme"}, [{"id": "cust_alpha"}]),
            *_tool_call_item(
                "get_active_contract", {"customer_id": "cust_alpha"}, {"id": "ct_alpha"}
            ),
        ]
    )
    calls = extract_tool_calls(rr)
    assert len(calls) == 2
    assert calls[0]["tool_name"] == "list_customers"
    assert calls[0]["result"] == [{"id": "cust_alpha"}]
    assert calls[1]["tool_name"] == "get_active_contract"
    assert calls[1]["result"] == {"id": "ct_alpha"}


def test_extract_tool_calls_strips_non_tool_items() -> None:
    rr = _run_result(
        [
            _message_item("assistant", "thinking..."),
            *_tool_call_item("list_customers", {}, []),
        ]
    )
    calls = extract_tool_calls(rr)
    assert [c["tool_name"] for c in calls] == ["list_customers"]


def test_extract_tool_calls_handles_empty() -> None:
    assert extract_tool_calls(_run_result([])) == []


def test_extract_tool_calls_unwraps_sdk_content_envelope() -> None:
    """The OpenAI Agents SDK + MCP transport wraps tool results in a
    one-segment content envelope ``[{"type": "input_text", "text": "<json>"}]``.
    extract_tool_calls must peel the envelope so downstream consumers
    see the underlying Pydantic dump (here, a ``BoundedList[Customer]``)."""
    payload = '{"items": [{"id": "cust_alpha", "kyc_status": "verified"}], "truncated": false}'
    envelope = [{"type": "input_text", "text": payload}]
    rr = _run_result(
        [
            *_tool_call_item("list_customers", {"name_contains": "Acme"}, envelope),
        ]
    )
    calls = extract_tool_calls(rr)
    assert calls[0]["result"] == {
        "items": [{"id": "cust_alpha", "kyc_status": "verified"}],
        "truncated": False,
    }


def test_extract_tool_calls_unwraps_envelope_for_single_model_tool() -> None:
    """``get_active_contract`` returns a single Pydantic model, not a
    BoundedList — its envelope payload is a bare object."""
    payload = '{"id": "ct_alpha", "currency": "USD"}'
    envelope = [{"type": "input_text", "text": payload}]
    rr = _run_result(
        [
            *_tool_call_item("get_active_contract", {"customer_id": "x"}, envelope),
        ]
    )
    calls = extract_tool_calls(rr)
    assert calls[0]["result"] == {"id": "ct_alpha", "currency": "USD"}


# ---------------------------------------------------------------------
# project_resolved_entities
# ---------------------------------------------------------------------


def test_project_customer_from_list_customers() -> None:
    calls: list[ToolCallRecord] = [
        {
            "tool_name": "list_customers",
            "args": {"name_q": "Acme"},
            "result": [{"id": "cust_alpha", "name": "Acme", "kyc_status": "verified"}],
        },
    ]
    entities = project_resolved_entities(calls)
    customer = entities["customer"]
    assert customer is not None
    assert customer["id"] == "cust_alpha"
    assert customer["kyc_status"] == "verified"


def test_project_customer_from_get_customer() -> None:
    calls: list[ToolCallRecord] = [
        {
            "tool_name": "get_customer",
            "args": {"customer_id": "cust_alpha"},
            "result": {"id": "cust_alpha", "kyc_status": "verified"},
        },
    ]
    entities = project_resolved_entities(calls)
    customer = entities["customer"]
    assert customer is not None
    assert customer["id"] == "cust_alpha"


def test_project_contract_from_get_active_contract() -> None:
    calls: list[ToolCallRecord] = [
        {
            "tool_name": "get_active_contract",
            "args": {"customer_id": "cust_alpha"},
            "result": {"id": "ct_alpha", "currency": "USD", "monthly_hour_cap": 40},
        },
    ]
    entities = project_resolved_entities(calls)
    contract = entities["contract"]
    assert contract is not None
    assert contract["id"] == "ct_alpha"


def test_project_contract_absent_when_not_queried() -> None:
    entities = project_resolved_entities([])
    assert entities.get("contract") is None
    assert entities.get("customer") is None


def test_project_rate_cards_collected() -> None:
    """``get_rate_card`` returns ``BoundedList[RateCardEntry]`` which MCP
    serializes as ``{items, truncated}``; the projection extends with items."""
    calls: list[ToolCallRecord] = [
        {
            "tool_name": "get_rate_card",
            "args": {"role": "SA"},
            "result": {
                "items": [
                    {"id": "rc_sa", "list_amount_cents": 40000, "currency": "USD"},
                ],
                "truncated": False,
            },
        },
        {
            "tool_name": "get_rate_card",
            "args": {"role": "PM"},
            "result": {
                "items": [
                    {"id": "rc_pm", "list_amount_cents": 25000, "currency": "USD"},
                ],
                "truncated": False,
            },
        },
    ]
    entities = project_resolved_entities(calls)
    assert {rc["id"] for rc in entities["rate_card_entries"]} == {"rc_sa", "rc_pm"}


def test_project_rate_cards_from_bounded_list_with_multiple_items() -> None:
    """A single ``get_rate_card`` call may return multiple entries."""
    calls: list[ToolCallRecord] = [
        {
            "tool_name": "get_rate_card",
            "args": {"service": "Implementation"},
            "result": {
                "items": [
                    {"id": "rc_a", "currency": "USD"},
                    {"id": "rc_b", "currency": "USD"},
                ],
                "truncated": False,
            },
        },
    ]
    entities = project_resolved_entities(calls)
    assert {rc["id"] for rc in entities["rate_card_entries"]} == {"rc_a", "rc_b"}


def test_project_customer_from_bounded_list_customers() -> None:
    """``list_customers`` returns a BoundedList; the first item is the
    resolved customer."""
    calls: list[ToolCallRecord] = [
        {
            "tool_name": "list_customers",
            "args": {"name_contains": "Acme"},
            "result": {
                "items": [
                    {"id": "cust_alpha", "name": "Acme", "kyc_status": "verified"},
                    {"id": "cust_beta", "name": "Acme Inc", "kyc_status": "verified"},
                ],
                "truncated": False,
            },
        },
    ]
    entities = project_resolved_entities(calls)
    customer = entities["customer"]
    assert customer is not None
    assert customer["id"] == "cust_alpha"


def test_project_time_entries_from_bounded_list() -> None:
    """``list_time_entries`` returns a BoundedList; items are appended."""
    calls: list[ToolCallRecord] = [
        {
            "tool_name": "list_time_entries",
            "args": {"customer_id": "cust_alpha"},
            "result": {
                "items": [
                    {"id": "te_1", "hours_micros": 2_000_000},
                    {"id": "te_2", "hours_micros": 4_000_000},
                ],
                "truncated": False,
            },
        },
    ]
    entities = project_resolved_entities(calls)
    assert [te["id"] for te in entities["time_entries"]] == ["te_1", "te_2"]


def test_project_time_entries_collected() -> None:
    calls: list[ToolCallRecord] = [
        {
            "tool_name": "list_time_entries",
            "args": {},
            "result": [
                {"id": "te_1", "hours_micros": 2_000_000},
                {"id": "te_2", "hours_micros": 4_000_000},
            ],
        },
    ]
    entities = project_resolved_entities(calls)
    assert [te["id"] for te in entities["time_entries"]] == ["te_1", "te_2"]


# ---------------------------------------------------------------------
# extract_reasoning_text
# ---------------------------------------------------------------------


def test_extract_reasoning_joins_assistant_messages() -> None:
    rr = _run_result(
        [
            _message_item("assistant", "looking up customer"),
            *_tool_call_item("list_customers", {}, []),
            _message_item("assistant", "found it"),
        ]
    )
    text = extract_reasoning_text(rr)
    assert "looking up customer" in text
    assert "found it" in text


# ---------------------------------------------------------------------
# hash_proposal
# ---------------------------------------------------------------------


def test_hash_proposal_deterministic() -> None:
    p = {"customer_id": "x", "total_cents": 80000}
    assert hash_proposal(p) == hash_proposal(p)


def test_hash_proposal_key_order_invariant() -> None:
    p1 = {"a": 1, "b": 2}
    p2 = {"b": 2, "a": 1}
    assert hash_proposal(p1) == hash_proposal(p2)


def test_hash_proposal_sensitive_to_values() -> None:
    assert hash_proposal({"a": 1}) != hash_proposal({"a": 2})
