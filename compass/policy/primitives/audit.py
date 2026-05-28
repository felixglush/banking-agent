"""audit_validation primitives — completeness checks on the terminal row.

Both primitives fire only on workflow bugs (a terminal row without
policy_hash or without consulted tool calls). Production behavior is
that they never fire; they exist as defect detectors.

Phase: audit_validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("log_policy_version")
def log_policy_version():
    """Returns a predicate that fails if context has no non-empty policy_hash."""

    def check(ctx: Mapping[str, Any]) -> Violation | None:
        h = ctx.get("policy_hash")
        if not h:
            return Violation(
                rule_id="",
                message="audit candidate has no policy_hash",
                evidence={"policy_hash_present": False},
            )
        return None

    return check


@primitive("log_data_sources_consulted")
def log_data_sources_consulted():
    """Returns a predicate that fails when tool_calls is empty/missing.

    Skips when the terminal row's phase is ``input_validation`` — a
    workflow that short-circuited at the scope gate never reached the
    main agent and has nothing to consult; flagging it as a defect
    would be a false positive. The main-agent terminal phases
    (``audit_validation`` for executed, ``pre_action_proposal`` /
    ``pre_execute`` for rejected) still apply.
    """

    def check(ctx: Mapping[str, Any]) -> Violation | None:
        candidate: dict[str, Any] = ctx.get("audit_entry_candidate") or {}
        if candidate.get("phase") == "input_validation":
            return None
        calls: list[Any] = ctx.get("tool_calls") or []
        if not calls:
            return Violation(
                rule_id="",
                message="audit candidate has no tool_calls (agent queried nothing)",
                evidence={"tool_call_count": len(calls)},
            )
        return None

    return check
