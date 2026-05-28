"""Primitive registry — ``@primitive`` decorator and ``list_primitives``.

Why the registry exists: ``hash_rules`` needs primitive name + frozen
params to canonicalize rule sets, and the Stage-10 coverage report
counts rule fires per primitive. Without registration, neither has a
stable handle. See spec §Registry — @primitive.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from compass.policy.types import Predicate, PredicateFn

PrimitiveFactory = Callable[..., PredicateFn]
RegisteredFactory = Callable[..., Predicate]

# Module-level catalogue. Public surface is ``list_primitives()``;
# direct access is allowed inside tests (see conftest auto-reset).
_REGISTRY: dict[str, RegisteredFactory] = {}


def primitive(name: str) -> Callable[[PrimitiveFactory], RegisteredFactory]:
    """Register a primitive factory under ``name``.

    The decorated factory MUST take keyword-only arguments (so the
    serialization in ``hash_rules`` is deterministic). It returns a
    plain callable ``(ctx) -> Violation | None``; the wrapper packages
    that callable + name + frozen params into a Predicate.
    """

    def decorator(factory: PrimitiveFactory) -> RegisteredFactory:
        @functools.wraps(factory)
        def wrapped(**params: Any) -> Predicate:
            fn = factory(**params)
            return Predicate(primitive_name=name, params=_freeze(params), fn=fn)

        if name in _REGISTRY:
            raise RuntimeError(f"duplicate primitive registration: {name!r}")
        _REGISTRY[name] = wrapped
        return wrapped

    return decorator


def list_primitives() -> dict[str, RegisteredFactory]:
    """Snapshot of the registry, keyed by primitive name."""
    return dict(_REGISTRY)


def _freeze(params: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a read-only view of ``params``.

    A primitive's params are the rule's identity for hashing; mutation
    after construction would corrupt the hash. MappingProxyType gives
    read-only access without copying.
    """
    return MappingProxyType(dict(params))
