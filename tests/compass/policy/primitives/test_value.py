"""numeric_threshold: above/below/within/missing-path cases."""

from __future__ import annotations

import pytest

from compass.policy.primitives.value import numeric_threshold


async def test_above_max_fires() -> None:
    pred = numeric_threshold(field="proposal.total_cents", max=10_000)
    v = await pred({"proposal": {"total_cents": 15_000}})
    assert v is not None
    assert v.evidence == {"field": "proposal.total_cents", "value": 15_000, "max": 10_000}


async def test_equal_max_skips() -> None:
    pred = numeric_threshold(field="x", max=10)
    assert await pred({"x": 10}) is None


async def test_below_min_fires() -> None:
    pred = numeric_threshold(field="x", min=5)
    v = await pred({"x": 1})
    assert v is not None
    assert v.evidence == {"field": "x", "value": 1, "min": 5}


async def test_within_band_skips() -> None:
    pred = numeric_threshold(field="x", min=0, max=10)
    assert await pred({"x": 5}) is None


async def test_missing_field_fires_with_clear_evidence() -> None:
    """A missing field is a bug, not a skip — surface it loudly."""
    pred = numeric_threshold(field="x", max=10)
    v = await pred({})
    assert v is not None
    assert "missing" in v.message.lower()


async def test_neither_min_nor_max_raises_at_factory_time() -> None:
    with pytest.raises(ValueError, match="at least one of"):
        numeric_threshold(field="x")
