"""Pure functions that project Runner.run's RunResult into a policy context.

The OpenAI Agents SDK's RunResult exposes ``new_items`` — a list of
typed items including tool-call outputs and assistant messages. We
extract the bits the policy engine needs:

* tool_calls — for evidence-citation rules and audit_validation
* resolved_entities — derived from specific tool names (the
  workflow's MCP is closed-set; we know which tool returns which type)
* reasoning_text — concatenated assistant messages, for future
  reasoning-trace audit checks
* hash_proposal — sha256 of the canonical proposal JSON, captured by
  the workflow for drift detection at pre_execute

No I/O. Workflow code calls these directly between Runner.run and the
evaluate_policy activity invocation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypedDict, cast

from compass.policy import ToolCallRecord


class ResolvedEntities(TypedDict):
    """The send-invoice projection of the agent's bank-MCP tool calls.

    Workflow-specific: send-invoice cares about exactly these four
    slots. Dispute-investigation (v0.2) will define its own analogous
    TypedDict over a different MCP surface. None values denote
    "agent did not query this entity"; ``rule_skipped`` is the
    expected predicate behavior for those.
    """

    customer: dict[str, Any] | None
    contract: dict[str, Any] | None
    rate_card_entries: list[dict[str, Any]]
    time_entries: list[dict[str, Any]]


def extract_tool_calls(run_result: Any) -> list[ToolCallRecord]:
    """Return [{tool_name, args, result}, ...] for each tool call.

    The SDK emits a ``tool_call_item`` (with name+arguments on raw_item)
    followed by a ``tool_call_output_item`` (with output on raw_item)
    for each completed call. We pair them by ``call_id`` so each entry
    carries name, args, and result. Calls without a paired output (e.g.
    cancelled) are dropped.
    """
    items: list[Any] = getattr(run_result, "new_items", None) or []
    calls: dict[str, ToolCallRecord] = {}
    order: list[str] = []
    for item in items:
        kind = getattr(item, "type", None)
        if kind == "tool_call_item":
            raw = getattr(item, "raw_item", item)
            name = _attr_or_key(raw, "name")
            call_id = _attr_or_key(raw, "call_id") or _attr_or_key(raw, "id")
            if not name or not call_id:
                continue
            args_raw = _attr_or_key(raw, "arguments")
            calls[call_id] = {
                "tool_name": name,
                "args": _maybe_json(args_raw),
                "result": None,
            }
            order.append(call_id)
        elif kind == "tool_call_output_item":
            raw = getattr(item, "raw_item", item)
            call_id = _attr_or_key(raw, "call_id") or _attr_or_key(raw, "id")
            output_raw = _attr_or_key(raw, "output") or getattr(item, "output", None)
            if call_id and call_id in calls:
                calls[call_id]["result"] = _decode_tool_output(output_raw)
    return [calls[cid] for cid in order]


def _attr_or_key(obj: Any, key: str) -> Any:
    """Read either obj.key (dataclass / model) or obj[key] (dict)."""
    if isinstance(obj, dict):
        return cast(dict[str, Any], obj).get(key)
    return getattr(obj, key, None)


def project_resolved_entities(tool_calls: list[ToolCallRecord]) -> ResolvedEntities:
    """Reduce tool calls into the resolved-entities snapshot the rules use.

    The mapping from tool name → entity slot is closed-set. Adding a
    new tool that should populate resolved_entities requires updating
    this function — that's intentional; the projection contract is
    workflow-specific.
    """
    entities: ResolvedEntities = {
        "customer": None,
        "contract": None,
        "rate_card_entries": [],
        "time_entries": [],
    }
    for call in tool_calls:
        name = call["tool_name"]
        result = call["result"]
        if name == "list_customers":
            items = _bounded_items(result)
            if items:
                entities["customer"] = items[0]
        elif name == "get_customer" and isinstance(result, dict):
            entities["customer"] = cast(dict[str, Any], result)
        elif name == "get_active_contract" and isinstance(result, dict):
            entities["contract"] = cast(dict[str, Any], result)
        elif name == "get_rate_card":
            entities["rate_card_entries"].extend(_bounded_items(result))
        elif name == "list_time_entries":
            entities["time_entries"].extend(_bounded_items(result))
    return entities


def _bounded_items(result: Any) -> list[dict[str, Any]]:
    """Extract the items list from an mcp_bank ``BoundedList[T]`` payload.

    The wire shape after ``_decode_tool_output`` is ``{"items": [...],
    "truncated": bool}``; raw lists are accepted for legacy test fixtures.
    """
    if isinstance(result, list):
        return cast(list[dict[str, Any]], result)
    if isinstance(result, dict):
        items = cast(dict[str, Any], result).get("items")
        if isinstance(items, list):
            return cast(list[dict[str, Any]], items)
    return []


def extract_reasoning_text(run_result: Any) -> str:
    """Concatenate every assistant message in the run's new_items."""
    parts: list[str] = []
    items: list[Any] = getattr(run_result, "new_items", None) or []
    for item in items:
        if getattr(item, "type", None) != "message_output_item":
            continue
        raw = getattr(item, "raw_item", item)
        if getattr(raw, "role", None) != "assistant":
            continue
        content = getattr(raw, "content", None)
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def hash_proposal(proposal: dict[str, Any]) -> str:
    """Stable sha256 hex of the proposal dict.

    Used as proposal_hash_at_proposal by the workflow, compared at
    pre_execute by prohibit_silent_modification_after_confirmation.
    """
    canon = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _maybe_json(value: Any) -> Any:
    """Decode strings that look like JSON; pass through other types."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def _decode_tool_output(value: Any) -> Any:
    """Decode an SDK tool-output payload to its underlying Pydantic-dump shape.

    Two transport wrappers are unwrapped:
    * a bare JSON string — emitted when the SDK hands us the model's raw
      serialization;
    * a single-segment content envelope of the form
      ``[{"type": "input_text" | "text", "text": "<json>"}]`` — emitted when
      MCP routes a Pydantic response through the Agents SDK's content
      protocol.

    Multi-segment envelopes and unknown shapes pass through unchanged; the
    projection then either matches them with an ``isinstance`` guard or
    ignores them.
    """
    decoded: Any = _maybe_json(value)
    if not isinstance(decoded, list):
        return decoded
    decoded_list = cast(list[Any], decoded)
    if len(decoded_list) != 1 or not isinstance(decoded_list[0], dict):
        return decoded_list
    first = cast(dict[str, Any], decoded_list[0])
    if first.get("type") not in ("input_text", "text"):
        return decoded_list
    text = first.get("text")
    if not isinstance(text, str):
        return decoded_list
    return _maybe_json(text)
