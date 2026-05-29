"""Stage-8 adversarial corpus: load attack contexts and build Promptfoo
red-team configs from them. Pure data transforms (no IO beyond reading the
contexts file); generation/freeze orchestration is added in a later task.

This module is framework-side (compass): it reads contexts as data and emits
Promptfoo config dicts. It never imports evals/ — the provider/assertion paths
arrive as strings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml


@dataclass(frozen=True)
class AttackCategory:
    tag: str
    expected_rule_ids: list[str]
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
            expected_rule_ids=[str(r) for r in c["expected_rule_ids"]],
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


def build_redteam_config(
    contexts: AttackContexts,
    category: AttackCategory,
    *,
    provider_path: str,
    num_tests: int,
) -> dict[str, Any]:
    """One category → a Promptfoo red-team config dict (ready to YAML-dump)."""
    plugins: list[dict[str, Any]] = []
    for p in category.plugins:
        entry: dict[str, Any] = {"id": p["id"], "numTests": num_tests}
        if "config" in p:
            entry["config"] = p["config"]
        plugins.append(entry)
    return {
        "description": f"Stage 8 adversarial — {category.tag}",
        "providers": [f"file://{provider_path}"],
        "redteam": {
            "purpose": contexts.purpose,
            "plugins": plugins,
            "strategies": [{"id": s} for s in category.strategies],
        },
    }
