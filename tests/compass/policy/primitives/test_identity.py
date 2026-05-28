"""entity_status_equals — fires when an entity's status field doesn't match."""

from __future__ import annotations

from compass.policy.primitives.identity import entity_status_equals


async def test_matching_status_skips() -> None:
    pred = entity_status_equals(
        field="resolved_entities.customer.kyc_status",
        expected_status="verified",
    )
    ctx = {"resolved_entities": {"customer": {"kyc_status": "verified"}}}
    assert await pred(ctx) is None


async def test_mismatched_status_fires() -> None:
    pred = entity_status_equals(
        field="resolved_entities.customer.kyc_status",
        expected_status="verified",
    )
    ctx = {"resolved_entities": {"customer": {"kyc_status": "pending"}}}
    v = await pred(ctx)
    assert v is not None
    assert v.evidence["expected"] == "verified"
    assert v.evidence["actual"] == "pending"


async def test_missing_path_skips_silently() -> None:
    """No customer in resolved_entities = the agent didn't query one;
    other rules (require_existing_entity) handle missing entities."""
    pred = entity_status_equals(
        field="resolved_entities.customer.kyc_status",
        expected_status="verified",
    )
    assert await pred({"resolved_entities": {}}) is None
