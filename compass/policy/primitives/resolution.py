"""require_existing_entity — fires when a required entity isn't resolved.

Build-plan §Primitive families — Resolution gates. The agent looked up
a customer (or contract, etc.) via MCP; the workflow's context-builder
projected the result into resolved_entities; this rule fires if the
projection failed.

Phase: pre_action_proposal.
"""

from __future__ import annotations

from typing import Any

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("require_existing_entity")
def require_existing_entity(*, field: str, entity_type: str):
    """Returns a predicate that fails when field is missing, None, or empty.

    "Empty dict" counts as absence — a customer record without an id is
    not a customer for our purposes. List entities aren't supported by
    this primitive at v0.1; use a different primitive for those.
    """

    def check(ctx: dict[str, Any]) -> Violation | None:
        value = resolve_dotted(ctx, field)
        if value is MISSING or value is None or value == {}:
            return Violation(
                rule_id="",
                message=f"required {entity_type} not resolved at {field}",
                evidence={"field": field, "entity_type": entity_type},
            )
        return None

    return check
