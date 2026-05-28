"""numeric_threshold — value-band check on a numeric field.

Build-plan §Primitive families — Value gates. Fires when ``value`` is
below ``min`` or above ``max``. Both bounds are inclusive at the
boundary (equal-to-max passes — banking thresholds are typically
"strictly greater than", and rounded values land on the boundary).

Phase: pre_action_proposal.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("numeric_threshold")
def numeric_threshold(
    *,
    field: str,
    min: int | float | None = None,
    max: int | float | None = None,
):
    """Factory. Returns a sync predicate that fails on out-of-band values."""
    if min is None and max is None:
        raise ValueError(
            "numeric_threshold: at least one of min= or max= must be set "
            "(an open-ended band evaluates nothing)."
        )

    def check(ctx: Mapping[str, Any]) -> Violation | None:
        value = resolve_dotted(ctx, field)
        if value is MISSING:
            return Violation(
                rule_id="",
                message=f"{field} missing from context",
                evidence={"field": field, "reason": "missing"},
            )
        if max is not None and value > max:
            return Violation(
                rule_id="",
                message=f"{field}={value} exceeds max {max}",
                evidence={"field": field, "value": value, "max": max},
            )
        if min is not None and value < min:
            return Violation(
                rule_id="",
                message=f"{field}={value} below min {min}",
                evidence={"field": field, "value": value, "min": min},
            )
        return None

    return check
