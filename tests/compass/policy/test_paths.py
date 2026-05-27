"""resolve_dotted: dotted-path lookup with [*] wildcard."""

from __future__ import annotations

from compass.policy.paths import MISSING, resolve_dotted


def test_single_key() -> None:
    assert resolve_dotted({"a": 1}, "a") == 1


def test_nested_keys() -> None:
    assert resolve_dotted({"a": {"b": {"c": 7}}}, "a.b.c") == 7


def test_missing_root_returns_sentinel() -> None:
    assert resolve_dotted({}, "a") is MISSING


def test_missing_nested_returns_sentinel() -> None:
    assert resolve_dotted({"a": {"b": 1}}, "a.c") is MISSING


def test_traverse_through_none_returns_sentinel() -> None:
    assert resolve_dotted({"a": None}, "a.b") is MISSING


def test_wildcard_collects_list_elements() -> None:
    ctx = {"items": [{"x": 1}, {"x": 2}, {"x": 3}]}
    assert resolve_dotted(ctx, "items[*].x") == [1, 2, 3]


def test_wildcard_terminal() -> None:
    ctx = {"items": [{"x": 1}, {"x": 2}]}
    assert resolve_dotted(ctx, "items[*]") == [{"x": 1}, {"x": 2}]


def test_wildcard_on_missing_list_returns_sentinel() -> None:
    assert resolve_dotted({}, "items[*].x") is MISSING


def test_wildcard_on_non_list_returns_sentinel() -> None:
    assert resolve_dotted({"items": "not a list"}, "items[*].x") is MISSING


def test_present_falsy_value_not_sentinel() -> None:
    assert resolve_dotted({"a": 0}, "a") == 0
    assert resolve_dotted({"a": ""}, "a") == ""
    assert resolve_dotted({"a": []}, "a") == []
