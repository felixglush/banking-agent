"""Dotted-path resolver with ``[*]`` wildcard.

Predicates use string paths to navigate the context dict. The MISSING
sentinel lets predicates distinguish "key absent" from "key present
with falsy value" — important for ``require_existing_entity`` (where
absence is the firing condition) versus ``entity_status_equals``
(where absence may mean "skip this rule, the entity wasn't queried").

Grammar:
    path     := segment ('.' segment)*
    segment  := identifier | identifier '[*]'

``items[*].x`` returns a list of every item's ``x``. ``items[*]`` (no
suffix) returns the list itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, cast

# Sentinel for "the path did not resolve to anything". A unique object
# so callers compare with ``is``.
MISSING: Final[object] = object()


def resolve_dotted(ctx: Mapping[str, Any], path: str) -> Any:
    """Navigate ``ctx`` along ``path``; return MISSING on any miss."""
    segments = path.split(".")
    return _resolve(ctx, segments)


def _resolve(node: Any, segments: list[str]) -> Any:
    if not segments:
        return node
    head, *rest = segments
    wildcard = head.endswith("[*]")
    key = head[:-3] if wildcard else head

    if node is None or not isinstance(node, Mapping) or key not in node:
        return MISSING
    value = cast(Any, node[key])

    if wildcard:
        if not isinstance(value, list):
            return MISSING
        items = cast(list[Any], value)
        resolved = [_resolve(item, rest) for item in items]
        if any(r is MISSING for r in resolved):
            return MISSING
        return resolved
    return _resolve(value, rest)
