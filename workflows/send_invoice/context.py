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
from typing import Any


def extract_tool_calls(run_result: Any) -> list[dict[str, Any]]:
    """Return [{tool_name, args, result}, ...] for each tool call."""
    out: list[dict[str, Any]] = []
    items = getattr(run_result, "new_items", None) or []
    for item in items:
        # The SDK uses ``type="tool_call_output_item"`` for tool results.
        if getattr(item, "type", None) != "tool_call_output_item":
            continue
        raw = getattr(item, "raw_item", item)
        name = getattr(raw, "name", None)
        if not name:
            continue
        args_raw = getattr(raw, "arguments", None)
        output_raw = getattr(raw, "output", None)
        out.append({
            "tool_name": name,
            "args": _maybe_json(args_raw),
            "result": _maybe_json(output_raw),
        })
    return out


def project_resolved_entities(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce tool calls into the resolved-entities snapshot the rules use.

    The mapping from tool name → entity slot is closed-set. Adding a
    new tool that should populate resolved_entities requires updating
    this function — that's intentional; the projection contract is
    workflow-specific.
    """
    entities: dict[str, Any] = {
        "customer": None,
        "contract": None,
        "rate_card_entries": [],
        "time_entries": [],
    }
    for call in tool_calls:
        name = call.get("tool_name")
        result = call.get("result")
        if name == "list_customers" and isinstance(result, list) and result:
            entities["customer"] = result[0]
        elif name == "get_customer" and isinstance(result, dict):
            entities["customer"] = result
        elif name == "get_active_contract" and isinstance(result, dict):
            entities["contract"] = result
        elif name == "get_rate_card" and isinstance(result, dict):
            entities["rate_card_entries"].append(result)
        elif name == "list_time_entries" and isinstance(result, list):
            entities["time_entries"].extend(result)
    return entities


def extract_reasoning_text(run_result: Any) -> str:
    """Concatenate every assistant message in the run's new_items."""
    parts: list[str] = []
    items = getattr(run_result, "new_items", None) or []
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
