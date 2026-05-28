"""@primitive registry semantics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from compass.policy import registry as registry_module
from compass.policy.registry import list_primitives, primitive
from compass.policy.types import Predicate, PredicateFn, Violation


def _always_none() -> PredicateFn:
    def check(_ctx: Mapping[str, Any]) -> Violation | None:
        return None

    return check


@pytest.fixture(autouse=True)
def _clear_registry():  # pyright: ignore[reportUnusedFunction]
    """Tests in this module install primitives — reset between runs.

    The registry is module-private but test isolation legitimately
    needs to touch it; the alternative would be a public
    test-only-hook on the module, which has more surface area than
    a scoped pyright suppression.
    """
    snapshot = dict(registry_module._REGISTRY)  # pyright: ignore[reportPrivateUsage]
    registry_module._REGISTRY.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    registry_module._REGISTRY.clear()  # pyright: ignore[reportPrivateUsage]
    registry_module._REGISTRY.update(snapshot)  # pyright: ignore[reportPrivateUsage]


def test_decorator_returns_predicate_with_name_and_params() -> None:
    @primitive("my_threshold")
    def my_threshold(*, max: int) -> PredicateFn:
        def check(_ctx: Mapping[str, Any]) -> Violation | None:
            return None

        return check

    pred = my_threshold(max=10)
    assert isinstance(pred, Predicate)
    assert pred.primitive_name == "my_threshold"
    assert pred.params == {"max": 10}


def test_params_are_frozen() -> None:
    @primitive("my_threshold")
    def my_threshold(*, max: int) -> PredicateFn:
        def check(_ctx: Mapping[str, Any]) -> Violation | None:
            return None

        return check

    pred = my_threshold(max=10)
    with pytest.raises(TypeError):
        pred.params["max"] = 99  # type: ignore[index]


def test_list_primitives_returns_registered() -> None:
    @primitive("first")
    def first() -> PredicateFn:  # pyright: ignore[reportUnusedFunction]
        return _always_none()

    @primitive("second")
    def second() -> PredicateFn:  # pyright: ignore[reportUnusedFunction]
        return _always_none()

    catalogue = list_primitives()
    assert set(catalogue.keys()) == {"first", "second"}


def test_duplicate_registration_raises() -> None:
    @primitive("dup")
    def dup_a() -> PredicateFn:  # pyright: ignore[reportUnusedFunction]
        return _always_none()

    with pytest.raises(RuntimeError, match="duplicate primitive"):

        @primitive("dup")
        def dup_b() -> PredicateFn:  # pyright: ignore[reportUnusedFunction]
            return _always_none()


async def test_predicate_passes_through_violation() -> None:
    @primitive("returns_violation")
    def factory() -> PredicateFn:
        def check(_ctx: Mapping[str, Any]) -> Violation | None:
            return Violation(rule_id="", message="x", evidence={})

        return check

    pred = factory()
    result = await pred({})
    assert result is not None
    assert result.message == "x"
