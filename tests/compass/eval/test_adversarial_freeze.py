from pathlib import Path
from typing import Any

import yaml

from compass.eval.adversarial_corpus import (
    AttackCategory,
    AttackContexts,
    resolve_corpus_config,
    stamp_and_merge,
)


def _contexts() -> AttackContexts:
    return AttackContexts(
        purpose="p",
        categories=[
            AttackCategory("amount_manipulation", ["invoice_amount_cap"], [{"id": "policy"}], []),
            AttackCategory("wrong_recipient", ["customer_must_exist"], [{"id": "policy"}], []),
        ],
        num_tests_default=2,
    )


def test_stamp_and_merge_tags_every_test(tmp_path: Path) -> None:
    gen = {
        "amount_manipulation": [{"vars": {"prompt": "invoice $1M"}}],
        "wrong_recipient": [
            {"vars": {"prompt": "invoice ghost"}},
            {"vars": {"prompt": "impersonate"}},
        ],
    }
    merged = stamp_and_merge(
        _contexts(),
        gen,
        provider_path="evals/adversarial/provider.py",
        assertion_path="evals/adversarial/assertion.py",
    )
    tests = merged["tests"]
    assert len(tests) == 3
    amt = [t for t in tests if t["metadata"]["category"] == "amount_manipulation"]
    assert amt[0]["metadata"]["expected_rule_ids"] == ["invoice_amount_cap"]
    assert merged["providers"] == ["file://evals/adversarial/provider.py"]
    assert merged["defaultTest"]["assert"][0]["type"] == "python"
    assert merged["defaultTest"]["assert"][0]["metric"] == "adversarial_policy_fire"
    assert merged["defaultTest"]["assert"][0]["weight"] == 0


def test_merged_config_roundtrips_through_yaml(tmp_path: Path) -> None:
    merged = stamp_and_merge(
        _contexts(),
        {"amount_manipulation": [{"vars": {"prompt": "x"}}]},
        provider_path="p.py",
        assertion_path="a.py",
    )
    p = tmp_path / "frozen.yaml"
    p.write_text(yaml.safe_dump(merged))
    back = yaml.safe_load(p.read_text())
    assert back["tests"][0]["metadata"]["category"] == "amount_manipulation"


def test_holdout_replays_frozen_without_generating(tmp_path: Path) -> None:
    calls: list[str] = []

    def _fake_generate(config_path: str) -> list[dict[str, Any]]:
        calls.append(config_path)
        return [{"vars": {"prompt": "x"}}]

    frozen_dir = tmp_path / "frozen"
    frozen_dir.mkdir()
    (frozen_dir / "redteam_abc123.yaml").write_text("description: pre-frozen\ntests: []\n")

    out = resolve_corpus_config(
        _contexts(),
        mode="holdout",
        git_sha="abc123",
        frozen_dir=frozen_dir,
        provider_path="p.py",
        assertion_path="a.py",
        num_tests=2,
        generate=_fake_generate,
        work_dir=tmp_path,
    )
    assert out == frozen_dir / "redteam_abc123.yaml"
    assert calls == []  # replay must not regenerate


def test_holdout_first_run_generates_and_freezes(tmp_path: Path) -> None:
    def _fake_generate(config_path: str) -> list[dict[str, Any]]:
        return [{"vars": {"prompt": "atk"}}]

    frozen_dir = tmp_path / "frozen"
    out = resolve_corpus_config(
        _contexts(),
        mode="holdout",
        git_sha="newsha",
        frozen_dir=frozen_dir,
        provider_path="p.py",
        assertion_path="a.py",
        num_tests=2,
        generate=_fake_generate,
        work_dir=tmp_path,
    )
    assert out.exists()
    assert (frozen_dir / "holdout_cases_newsha.jsonl").exists()
