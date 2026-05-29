"""Stage-8 adversarial corpus: load attack contexts and build Promptfoo
red-team configs from them. Pure data transforms (no IO beyond reading the
contexts file); generation/freeze orchestration is added in a later task.

This module is framework-side (compass): it reads contexts as data and emits
Promptfoo config dicts. It never imports evals/ — the provider/assertion paths
arrive as strings."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
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


# A Promptfoo test dict (vars + metadata). Open shape Promptfoo owns.
PromptfooTest = dict[str, Any]
# category_tag -> raw generated tests for that category
GeneratedByCategory = dict[str, list[PromptfooTest]]
# (config_path) -> list of generated test dicts. Injected so tests don't shell out.
GenerateFn = Callable[[str], list[PromptfooTest]]


def stamp_and_merge(
    contexts: AttackContexts,
    generated: GeneratedByCategory,
    *,
    provider_path: str,
    assertion_path: str,
) -> dict[str, Any]:
    """Stamp category + expected_rule_ids into every test's metadata and merge
    all categories into one runnable Promptfoo eval config."""
    by_tag = {c.tag: c for c in contexts.categories}
    tests: list[PromptfooTest] = []
    for tag, raw_tests in generated.items():
        cat = by_tag[tag]
        for t in raw_tests:
            stamped = dict(t)
            md = dict(stamped.get("metadata") or {})
            md["category"] = tag
            md["expected_rule_ids"] = list(cat.expected_rule_ids)
            stamped["metadata"] = md
            tests.append(stamped)
    return {
        "description": "Stage 8 adversarial — merged corpus",
        "providers": [f"file://{provider_path}"],
        "defaultTest": {
            "assert": [
                {
                    "type": "python",
                    "value": f"file://{assertion_path}",
                    "metric": "adversarial_policy_fire",
                    "weight": 0,  # non-gating: grader is the sole pass/fail gate
                }
            ]
        },
        "tests": tests,
    }


def default_generate_fn(promptfoo_bin: str, work_dir: Path) -> GenerateFn:
    """Real generator: writes a per-category redteam config, runs
    `promptfoo redteam generate`, and returns the generated tests."""

    def _generate(config_path: str) -> list[PromptfooTest]:
        out_path = work_dir / (Path(config_path).stem + ".generated.yaml")
        subprocess.run(
            [promptfoo_bin, "redteam", "generate", "-c", config_path, "-o", str(out_path)],
            check=True,
        )
        generated: dict[str, Any] = yaml.safe_load(out_path.read_text())
        raw_tests = cast(list[PromptfooTest], generated.get("tests") or [])
        return [dict(t) for t in raw_tests]

    return _generate


def build_corpus(
    contexts: AttackContexts,
    *,
    provider_path: str,
    assertion_path: str,
    num_tests: int,
    generate: GenerateFn,
    work_dir: Path,
) -> dict[str, Any]:
    """Generate one config per category, run generation, stamp + merge."""
    generated: GeneratedByCategory = {}
    for cat in contexts.categories:
        cfg = build_redteam_config(contexts, cat, provider_path=provider_path, num_tests=num_tests)
        cfg_path = work_dir / f"redteam_{cat.tag}.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg))
        generated[cat.tag] = generate(str(cfg_path))
    return stamp_and_merge(
        contexts, generated, provider_path=provider_path, assertion_path=assertion_path
    )


def write_manifest(merged: dict[str, Any], manifest_path: Path) -> None:
    """Human-readable JSONL audit trail of the frozen corpus."""
    lines: list[str] = []
    for t in cast(list[PromptfooTest], merged.get("tests", [])):
        md = cast(dict[str, Any], t.get("metadata") or {})
        vars_ = cast(dict[str, Any], t.get("vars") or {})
        lines.append(
            json.dumps(
                {
                    "attack_prompt": vars_.get("prompt", ""),
                    "category_tag": md.get("category"),
                    "expected_rule_ids": md.get("expected_rule_ids", []),
                }
            )
        )
    manifest_path.write_text("\n".join(lines) + ("\n" if lines else ""))


def resolve_corpus_config(
    contexts: AttackContexts,
    *,
    mode: str,
    git_sha: str,
    frozen_dir: Path,
    provider_path: str,
    assertion_path: str,
    num_tests: int,
    generate: GenerateFn,
    work_dir: Path,
) -> Path:
    """Return the Promptfoo eval config path to run.

    holdout: reuse frozen redteam_<sha>.yaml if present; else generate, freeze,
    and write the JSONL manifest. train: always generate fresh into work_dir
    (not frozen)."""
    if mode == "holdout":
        frozen_dir.mkdir(parents=True, exist_ok=True)
        frozen = frozen_dir / f"redteam_{git_sha}.yaml"
        if frozen.exists():
            return frozen
        merged = build_corpus(
            contexts,
            provider_path=provider_path,
            assertion_path=assertion_path,
            num_tests=num_tests,
            generate=generate,
            work_dir=work_dir,
        )
        frozen.write_text(yaml.safe_dump(merged))
        write_manifest(merged, frozen_dir / f"holdout_cases_{git_sha}.jsonl")
        return frozen
    # train
    merged = build_corpus(
        contexts,
        provider_path=provider_path,
        assertion_path=assertion_path,
        num_tests=num_tests,
        generate=generate,
        work_dir=work_dir,
    )
    fresh = work_dir / "redteam_train.yaml"
    fresh.write_text(yaml.safe_dump(merged))
    return fresh
