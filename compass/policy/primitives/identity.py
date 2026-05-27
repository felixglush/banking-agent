"""entity_status_equals — fires when an entity's status field is not the expected value.

Build-plan §Primitive families — Identity gates. Used for KYC checks,
account-status checks, etc. The convention for "the entity wasn't
queried" is to SKIP rather than fire — separation of concerns from
require_existing_entity, which is the rule that catches absence.

Phase: pre_action_proposal.
"""

from __future__ import annotations

from typing import Any

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("entity_status_equals")
def entity_status_equals(*, field: str, expected_status: str):
    """Returns a predicate that fails if field's value != expected_status.

    Path MISSING is treated as "skip" — different from
    numeric_threshold, where missing == fire. Status checks attach to
    optional resolved-entity sub-paths; "customer wasn't queried" is
    not the same as "customer.kyc_status is bad".
    """

    def check(ctx: dict[str, Any]) -> Violation | None:
        actual = resolve_dotted(ctx, field)
        if actual is MISSING:
            return None
        if actual == expected_status:
            return None
        return Violation(
            rule_id="",
            message=f"{field}={actual!r}, expected {expected_status!r}",
            evidence={"field": field, "expected": expected_status, "actual": actual},
        )

    return check
