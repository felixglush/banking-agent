"""Approval-phase primitives — drift detection.

Build-plan §Primitive families — Approval gates. Both rules fire at
pre_execute. Both read pre-computed hashes from the context (the
workflow puts hash_at_proposal in there during the pre_action_proposal
phase) and compare to a "current" hash also placed in context.

The test stubs accept __test_current_*_hash__ keys so unit tests don't
need a live workflow; production callers populate the production keys
``current_proposal_hash`` / ``current_policy_hash`` instead.

Phase: pre_execute.
"""

from __future__ import annotations

from compass.policy.registry import primitive
from compass.policy.types import PolicyContext, Violation


@primitive("prohibit_silent_modification_after_confirmation")
def prohibit_silent_modification_after_confirmation():
    """Returns a predicate that fails when the proposal changed after approval.

    Compares ``proposal_hash_at_proposal`` (captured by the workflow at
    pre_action_proposal time) to ``current_proposal_hash``
    (recomputed at pre_execute time). At Stage 5 these always match
    (no UI yet); Stage 12 makes this load-bearing when the approval UI
    can edit the proposal.
    """

    def check(ctx: PolicyContext) -> Violation | None:
        at_proposal = ctx.get("proposal_hash_at_proposal")
        current = ctx.get("__test_current_proposal_hash__") or ctx.get("current_proposal_hash")
        if at_proposal is None or current is None:
            return None
        if at_proposal == current:
            return None
        return Violation(
            rule_id="",
            message="proposal hash differs between approval and execute",
            evidence={
                "hash_at_proposal": at_proposal,
                "hash_at_execute": current,
            },
        )

    return check


@primitive("prohibit_policy_drift_after_confirmation")
def prohibit_policy_drift_after_confirmation():
    """Returns a predicate that fires when the policy changed during approval wait.

    Compares ``policy_hash_at_proposal`` to ``current_policy_hash``.
    A worker restart that loaded new RULES between the agent's draft
    and the human's approval drives this. ESCALATE semantics (not
    BLOCK) — the human should re-approve, not silently get rejected.
    """

    def check(ctx: PolicyContext) -> Violation | None:
        at_proposal = ctx.get("policy_hash_at_proposal")
        current = ctx.get("__test_current_policy_hash__") or ctx.get("current_policy_hash")
        if at_proposal is None or current is None:
            return None
        if at_proposal == current:
            return None
        return Violation(
            rule_id="",
            message="policy hash drifted between proposal and execute",
            evidence={
                "hash_at_proposal": at_proposal,
                "hash_now": current,
            },
        )

    return check
