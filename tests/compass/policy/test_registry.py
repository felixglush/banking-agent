"""@primitive registry semantics."""

from __future__ import annotations

import pytest

from compass.policy.registry import (
    _REGISTRY,
    list_primitives,
    primitive,
)
from compass.policy.types import Predicate, Violation


@pytest.fixture(autouse=True)
def _clear_registry():
    """Tests in this module install primitives — reset between runs."""
    snapshot = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


def test_decorator_returns_predicate_with_name_and_params() -> None:
    @primitive("my_threshold")
    def my_threshold(*, max: int):
        def check(_ctx):
            return None
        return check

    pred = my_threshold(max=10)
    assert isinstance(pred, Predicate)
    assert pred.primitive_name == "my_threshold"
    assert pred.params == {"max": 10}


def test_params_are_frozen() -> None:
    @primitive("my_threshold")
    def my_threshold(*, max: int):
        def check(_ctx):
            return None
        return check

    pred = my_threshold(max=10)
    with pytest.raises(TypeError):
        pred.params["max"] = 99  # type: ignore[index]


def test_list_primitives_returns_registered() -> None:
    @primitive("first")
    def first(): return lambda _ctx: None
    @primitive("second")
    def second(): return lambda _ctx: None

    catalogue = list_primitives()
    assert set(catalogue.keys()) == {"first", "second"}


def test_duplicate_registration_raises() -> None:
    @primitive("dup")
    def dup_a(): return lambda _ctx: None

    with pytest.raises(RuntimeError, match="duplicate primitive"):
        @primitive("dup")
        def dup_b(): return lambda _ctx: None


async def test_predicate_passes_through_violation() -> None:
    @primitive("returns_violation")
    def factory():
        def check(_ctx):
            return Violation(rule_id="", message="x", evidence={})
        return check

    pred = factory()
    result = await pred({})
    assert result is not None
    assert result.message == "x"
