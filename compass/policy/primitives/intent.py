"""intent_in_allowlist — fires when an intent field is not in an allowlist.

Stage 6 ships this primitive for the binary scope gate
(allowed=frozenset({"send_invoice"})). Stage 16's multi-class router
extends the allowlist set without touching the primitive.

Phase: input_validation. Missing field is treated as a fire (unlike
entity_status_equals) — if the classifier did not produce an intent
the workflow has no way to route the request, so block.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("intent_in_allowlist")
def intent_in_allowlist(*, field: str, allowed: frozenset[str]):
    """Returns a predicate that fails if field's value is not in allowed.

    `allowed` must be a frozenset so the registry's param-freezing
    treats it as a hashable value and rules with identical membership
    canonicalize identically.
    """

    def check(ctx: Mapping[str, Any]) -> Violation | None:
        value = resolve_dotted(ctx, field)
        if value is MISSING:
            return Violation(
                rule_id="",
                message=f"field {field!r} missing from context",
                evidence={"field": field, "reason": "missing"},
            )
        if value not in allowed:
            return Violation(
                rule_id="",
                message=(f"intent {value!r} is not in allowlist {sorted(allowed)}"),
                evidence={
                    "field": field,
                    "value": value,
                    "allowed": sorted(allowed),
                },
            )
        return None

    return check
