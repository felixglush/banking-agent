"""Shared fixtures for compass.policy tests."""

from __future__ import annotations

from typing import Any

import pytest

from compass.policy.types import Predicate, Violation


def make_predicate(
    name: str = "test_primitive",
    params: dict[str, Any] | None = None,
    *,
    fires: bool = False,
    message: str = "boom",
    evidence: dict[str, Any] | None = None,
) -> Predicate:
    """Construct a synthetic predicate for engine/registry tests.

    Returning a non-None Violation when ``fires=True`` lets a single
    factory cover both fire and skip cases.
    """

    def fn(_ctx: Any) -> Violation | None:
        if not fires:
            return None
        return Violation(rule_id="", message=message, evidence=evidence or {})

    return Predicate(primitive_name=name, params=params or {}, fn=fn)


@pytest.fixture
def firing_predicate() -> Predicate:
    return make_predicate("test_primitive", fires=True)


@pytest.fixture
def passing_predicate() -> Predicate:
    return make_predicate("test_primitive", fires=False)
