"""require_evidence_citation — every list element at field must be truthy.

Build-plan §Primitive families — Evidence / citation gates. Specifically
shaped for ``proposal.line_items[*].source_refs`` and similar paths
that resolve to a list of lists. Each inner list must be non-empty.

Phase: pre_action_proposal.
"""

from __future__ import annotations

from typing import Any

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("require_evidence_citation")
def require_evidence_citation(*, field: str):
    """Returns a predicate that fails if any inner list at field is empty."""

    def check(ctx: dict[str, Any]) -> Violation | None:
        values = resolve_dotted(ctx, field)
        if values is MISSING:
            return Violation(
                rule_id="",
                message=f"{field} missing from context",
                evidence={"field": field, "reason": "missing"},
            )
        if not isinstance(values, list):
            return Violation(
                rule_id="",
                message=f"{field} resolved to non-list",
                evidence={"field": field, "reason": "non-list"},
            )
        empty_indices = [i for i, v in enumerate(values) if not v]
        if empty_indices:
            return Violation(
                rule_id="",
                message=f"{field}: lines {empty_indices} have no citations",
                evidence={"field": field, "empty_line_indices": empty_indices},
            )
        return None

    return check
