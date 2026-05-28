"""Canonical hashing of a rule set.

The hash is the audit log's reconstructability anchor: every
``audit_log`` row carries ``policy_hash``, every distinct hash has a
matching ``policy_snapshots.rules_json``, and ``rules_json`` is
byte-identical to what produced the hash. See spec §Hashing.

Canonicalization rules:

* Rules in declaration order (matches iteration order in ``evaluate``).
* Per rule: ``{id, phase, primitive, params, severity, regulatory_basis,
  tags, must_be_covered, surface_to_user}``.
* Tuples → JSON-native lists; param dicts → sorted keys (recursively).
* ``json.dumps(..., sort_keys=False, separators=(',', ':'))`` over the
  sorted-key dicts; sha256 hex.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, cast

from compass.policy.types import Rule


def canonicalize_rule(rule: Rule) -> dict[str, Any]:
    """Serialize one rule to a JSON-safe, deterministic dict."""
    return {
        "id": rule.id,
        "phase": rule.phase.value,
        "primitive": rule.predicate.primitive_name,
        "params": _sort_recursive(dict(rule.predicate.params)),
        "severity": rule.severity.value,
        "regulatory_basis": list(rule.regulatory_basis),
        "tags": list(rule.tags),
        "must_be_covered": rule.must_be_covered,
        "surface_to_user": rule.surface_to_user,
    }


def serialize_rules(rules: Sequence[Rule]) -> list[dict[str, Any]]:
    """Canonical list-of-dicts for the full rule set."""
    return [canonicalize_rule(r) for r in rules]


def hash_rules(rules: Sequence[Rule]) -> str:
    """sha256 hex over the canonical serialization."""
    blob = json.dumps(serialize_rules(rules), sort_keys=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _sort_recursive(value: Any) -> Any:
    """Sort dict keys at every level so param order doesn't affect the hash.

    frozenset/set become sorted lists so set-typed primitive params
    (e.g. ``intent_in_allowlist(allowed=frozenset({...}))``) serialize
    deterministically across runs.
    """
    if isinstance(value, Mapping):
        mapping = cast(Mapping[Any, Any], value)
        return {k: _sort_recursive(mapping[k]) for k in sorted(mapping)}
    if isinstance(value, frozenset | set):
        return sorted(_sort_recursive(v) for v in cast(set[Any], value))
    if isinstance(value, list | tuple):
        return [_sort_recursive(v) for v in cast(list[Any], value)]
    return value
