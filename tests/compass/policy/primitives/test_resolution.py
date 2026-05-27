"""require_existing_entity — fires when the resolved entity is missing/None."""

from __future__ import annotations

from compass.policy.primitives.resolution import require_existing_entity


async def test_entity_present_skips() -> None:
    pred = require_existing_entity(
        field="resolved_entities.customer", entity_type="customer",
    )
    ctx = {"resolved_entities": {"customer": {"id": "cust_alpha", "name": "Acme"}}}
    assert await pred(ctx) is None


async def test_entity_none_fires() -> None:
    pred = require_existing_entity(
        field="resolved_entities.customer", entity_type="customer",
    )
    ctx = {"resolved_entities": {"customer": None}}
    v = await pred(ctx)
    assert v is not None
    assert v.evidence["entity_type"] == "customer"


async def test_entity_missing_fires() -> None:
    pred = require_existing_entity(
        field="resolved_entities.customer", entity_type="customer",
    )
    v = await pred({"resolved_entities": {}})
    assert v is not None
    assert "customer" in v.message.lower()


async def test_entity_empty_dict_fires() -> None:
    pred = require_existing_entity(
        field="resolved_entities.customer", entity_type="customer",
    )
    v = await pred({"resolved_entities": {"customer": {}}})
    assert v is not None
