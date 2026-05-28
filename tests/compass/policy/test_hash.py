"""hash_rules: canonical, deterministic, param-sensitive."""

from __future__ import annotations

from compass.policy import Phase, Rule, Severity
from compass.policy.hashing import canonicalize_rule, hash_rules, serialize_rules
from compass.policy.types import Predicate


def _pred(name: str = "p", **params) -> Predicate:
    def check(_ctx):
        return None

    return Predicate(primitive_name=name, params=dict(params), fn=check)


def test_same_rules_same_hash() -> None:
    r = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred("p", max=10))
    assert hash_rules([r]) == hash_rules([r])


def test_param_change_changes_hash() -> None:
    r1 = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred("p", max=10))
    r2 = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred("p", max=11))
    assert hash_rules([r1]) != hash_rules([r2])


def test_param_key_order_does_not_change_hash() -> None:
    # Params dicts ordered differently must hash identically.
    r1 = Rule(
        id="r1",
        phase=Phase.pre_action_proposal,
        predicate=Predicate(primitive_name="p", params={"a": 1, "b": 2}, fn=lambda _c: None),
    )
    r2 = Rule(
        id="r1",
        phase=Phase.pre_action_proposal,
        predicate=Predicate(primitive_name="p", params={"b": 2, "a": 1}, fn=lambda _c: None),
    )
    assert hash_rules([r1]) == hash_rules([r2])


def test_rule_reorder_changes_hash() -> None:
    # Declaration order is part of the policy identity.
    r1 = Rule(id="a", phase=Phase.pre_action_proposal, predicate=_pred())
    r2 = Rule(id="b", phase=Phase.pre_action_proposal, predicate=_pred())
    assert hash_rules([r1, r2]) != hash_rules([r2, r1])


def test_severity_change_changes_hash() -> None:
    r1 = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred(), severity=Severity.BLOCK)
    r2 = Rule(
        id="r1", phase=Phase.pre_action_proposal, predicate=_pred(), severity=Severity.ESCALATE
    )
    assert hash_rules([r1]) != hash_rules([r2])


def test_regulatory_basis_change_changes_hash() -> None:
    r1 = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred(), regulatory_basis=("a",))
    r2 = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred(), regulatory_basis=("b",))
    assert hash_rules([r1]) != hash_rules([r2])


def test_canonicalize_rule_keys() -> None:
    r = Rule(
        id="r1",
        phase=Phase.pre_action_proposal,
        predicate=_pred("p", max=10),
        severity=Severity.BLOCK,
        regulatory_basis=("SOP-1",),
        tags=("tag1", "tag2"),
        must_be_covered=True,
    )
    canon = canonicalize_rule(r)
    assert canon["id"] == "r1"
    assert canon["phase"] == "pre_action_proposal"
    assert canon["primitive"] == "p"
    assert canon["params"] == {"max": 10}
    assert canon["severity"] == "block"
    assert canon["regulatory_basis"] == ["SOP-1"]
    assert canon["tags"] == ["tag1", "tag2"]
    assert canon["must_be_covered"] is True
    assert canon["surface_to_user"] is True


def test_serialize_rules_is_a_list() -> None:
    r = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred())
    assert isinstance(serialize_rules([r]), list)
    assert serialize_rules([r])[0]["id"] == "r1"


def test_hash_is_hex_sha256() -> None:
    r = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred())
    h = hash_rules([r])
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
