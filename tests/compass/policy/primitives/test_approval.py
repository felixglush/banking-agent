"""Approval-phase primitives: silent-modification and policy-drift detection."""

from __future__ import annotations

from compass.policy.primitives.approval import (
    prohibit_policy_drift_after_confirmation,
    prohibit_silent_modification_after_confirmation,
)

# ---- prohibit_silent_modification_after_confirmation ----


async def test_no_modification_skips() -> None:
    """Stage-5 happy path: proposal unchanged across approval wait."""
    pred = prohibit_silent_modification_after_confirmation()
    ctx = {
        "proposal": {"customer_id": "cust_alpha", "total_cents": 80000},
        "proposal_hash_at_proposal": "abc123",
    }
    ctx["__test_current_proposal_hash__"] = "abc123"
    assert await pred(ctx) is None


async def test_modification_detected_fires() -> None:
    pred = prohibit_silent_modification_after_confirmation()
    ctx = {
        "proposal": {"customer_id": "cust_alpha", "total_cents": 99999},
        "proposal_hash_at_proposal": "abc123",
        "__test_current_proposal_hash__": "def456",
    }
    v = await pred(ctx)
    assert v is not None
    assert v.evidence["hash_at_proposal"] == "abc123"
    assert v.evidence["hash_at_execute"] == "def456"


# ---- prohibit_policy_drift_after_confirmation ----


async def test_no_drift_skips() -> None:
    pred = prohibit_policy_drift_after_confirmation()
    ctx = {
        "policy_hash_at_proposal": "abc123",
        "__test_current_policy_hash__": "abc123",
    }
    assert await pred(ctx) is None


async def test_drift_detected_fires() -> None:
    pred = prohibit_policy_drift_after_confirmation()
    ctx = {
        "policy_hash_at_proposal": "abc123",
        "__test_current_policy_hash__": "def456",
    }
    v = await pred(ctx)
    assert v is not None
    assert v.evidence["hash_at_proposal"] == "abc123"
    assert v.evidence["hash_now"] == "def456"
