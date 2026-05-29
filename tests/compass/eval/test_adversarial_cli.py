import json
from pathlib import Path
from typing import Any

import pytest

from compass.eval.adversarial import run_adversarial


class _FakeStore:
    def __init__(self) -> None:
        self.finalized: list[str] = []
        self.kwargs: dict[str, Any] = {}

    async def allocate_run(self, **kwargs: Any) -> str:
        self.kwargs = kwargs
        return "ev_test123"

    async def finalize(self, run_id: str) -> None:
        self.finalized.append(run_id)


class _FakeSink:
    def __init__(self) -> None:
        self.scores: list[dict[str, Any]] = []
        self.run_scores: list[dict[str, Any]] = []

    async def write_score(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    async def write_run_score(self, **kwargs: Any) -> None:
        self.run_scores.append(kwargs)


async def test_run_adversarial_writes_two_scores_per_case(tmp_path: Path) -> None:
    fixture = Path("tests/compass/eval/fixtures/promptfoo_results_sample.json")
    results_json = json.loads(fixture.read_text())

    store, sink = _FakeStore(), _FakeSink()

    def _run_promptfoo(config_path: str, out_path: Path) -> dict[str, Any]:
        out_path.write_text(json.dumps(results_json))
        return results_json

    rc, table = await run_adversarial(
        mode="train",
        git_sha="deadbeef",
        holdout_justification=None,
        host_git_dirty=False,
        contexts_path=Path("evals/adversarial/contexts.yaml"),
        provider_path="evals/adversarial/provider.py",
        assertion_path="evals/adversarial/assertion.py",
        frozen_dir=tmp_path / "frozen",
        work_dir=tmp_path,
        store=store,
        sink=sink,
        resolve_config=lambda: tmp_path / "cfg.yaml",
        run_promptfoo=_run_promptfoo,
    )

    names = sorted(s["name"] for s in sink.scores)
    assert names == [
        "adversarial_policy_fire",
        "adversarial_policy_fire",
        "adversarial_response",
        "adversarial_response",
    ]
    assert any(s["name"] == "adversarial" for s in sink.run_scores)
    run_score = next(s for s in sink.run_scores if s["name"] == "adversarial")
    assert run_score["value"] == 0.5
    assert store.finalized == ["ev_test123"]
    assert rc == 1
    assert table["amount_manipulation"]["leaked_rule_fired"] == 1


def test_holdout_requires_justification() -> None:
    from compass.eval.adversarial import (  # noqa: PLC0415
        _build_parser,  # pyright: ignore[reportPrivateUsage]
        _validate,  # pyright: ignore[reportPrivateUsage]
    )

    ns = _build_parser().parse_args(["--workflow", "send_invoice", "--mode", "holdout"])
    with pytest.raises(SystemExit) as exc:
        _validate(ns)
    assert exc.value.code == 2
