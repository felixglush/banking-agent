"""Stage-8 adversarial attack contexts: load the category definitions
(`evals/adversarial/contexts.yaml`) that drive red-team generation.

A category bundles a ``tag`` (grouping label) with the Promptfoo ``plugins`` and
``strategies`` that generate its attacks. Building the combined red-team config
and mapping generated attacks back to categories live in
``compass.eval.adversarial_run``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml


@dataclass(frozen=True)
class AttackCategory:
    tag: str
    plugins: list[dict[str, Any]]
    strategies: list[str] = field(default_factory=lambda: cast(list[str], []))


@dataclass(frozen=True)
class AttackContexts:
    purpose: str
    categories: list[AttackCategory]
    num_tests_default: int


def load_contexts(path: Path) -> AttackContexts:
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    cats = [
        AttackCategory(
            tag=str(c["tag"]),
            plugins=[dict(p) for p in c["plugins"]],
            strategies=[str(s) for s in c.get("strategies", [])],
        )
        for c in raw["categories"]
    ]
    return AttackContexts(
        purpose=str(raw["purpose"]).strip(),
        categories=cats,
        num_tests_default=int(raw.get("defaults", {}).get("num_tests_per_plugin", 5)),
    )
