"""require_existing_entity — fires when the resolved entity is missing/None."""

from __future__ import annotations

from typing import Any

import pytest

from compass.policy.primitives.resolution import require_existing_entity

_PRED = require_existing_entity(
    field="resolved_entities.customer", entity_type="customer",
)


async def test_entity_present_skips() -> None:
    assert await _PRED(
        {"resolved_entities": {"customer": {"id": "cust_alpha", "name": "Acme"}}}
    ) is None


@pytest.mark.parametrize(
    "ctx",
    [
        pytest.param({"resolved_entities": {"customer": None}}, id="none"),
        pytest.param({"resolved_entities": {}}, id="missing"),
        pytest.param({"resolved_entities": {"customer": {}}}, id="empty_dict"),
    ],
)
async def test_absent_entity_fires(ctx: dict[str, Any]) -> None:
    v = await _PRED(ctx)
    assert v is not None
    assert v.evidence["entity_type"] == "customer"
