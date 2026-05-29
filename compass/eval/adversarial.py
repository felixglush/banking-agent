"""compass.eval.adversarial — Stage 8 adversarial-robustness eval entry point.

Separate from the Stage-7 `compass.eval` CLI (which is untouched). Orchestrates
Promptfoo's red-team as a subprocess, drives each attack to the gate via the
provider (in evals/), and writes two Langfuse scores per attack into the shared
harness.

Exit codes:
  0 — every attack repelled
  1 — at least one attack leaked (a bad proposal passed the gate)
  2 — invalid CLI args
  3 — holdout cap exceeded (raised by EvalRunStore)
  4 — pre-flight budget exceeded
  5 — infra (Postgres / Langfuse / Promptfoo) unavailable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from compass.eval.adversarial_corpus import (
    default_generate_fn,
    load_contexts,
    resolve_corpus_config,
)
from compass.eval.adversarial_report import build_bucket_table
from compass.eval.adversarial_results import parse_results
from compass.eval.gitmeta import git_dirty, git_sha
from compass.eval.types import AdversarialBucket

# Injected callable: (config_path, out_path) -> parsed results dict.
RunPromptfoo = Callable[[str, Path], dict[str, Any]]


class _Store(Protocol):
    async def allocate_run(self, **kwargs: Any) -> str: ...
    async def finalize(self, run_id: str) -> None: ...


class _Sink(Protocol):
    async def write_score(self, **kwargs: Any) -> None: ...
    async def write_run_score(self, **kwargs: Any) -> None: ...


async def run_adversarial(
    *,
    mode: str,
    git_sha: str,
    holdout_justification: str | None,
    host_git_dirty: bool,
    contexts_path: Path,
    provider_path: str,
    assertion_path: str,
    frozen_dir: Path,
    work_dir: Path,
    store: _Store,
    sink: _Sink,
    resolve_config: Callable[[], Path],
    run_promptfoo: RunPromptfoo,
    num_tests: int = 5,
) -> tuple[int, dict[str, dict[AdversarialBucket, int]]]:
    """Core orchestration (no arg parsing / no client construction — injectable)."""
    run_id = await store.allocate_run(
        git_sha=git_sha,
        mode=mode,
        holdout_justification=holdout_justification,
        policy_enabled=True,
        suite_names=["adversarial"],
        host_git_dirty=host_git_dirty,
    )

    config_path = resolve_config()
    out_path = work_dir / "results.json"
    data = run_promptfoo(str(config_path), out_path)
    results = parse_results(data)

    for r in results:
        await sink.write_score(
            run_id=run_id,
            item_id=r.case_id,
            name="adversarial_response",
            value=1.0 if r.repelled else 0.0,
            comment=f"{r.category}: {'repelled' if r.repelled else 'LEAKED'}",
            trace_id=r.trace_id,
        )
        await sink.write_score(
            run_id=run_id,
            item_id=r.case_id,
            name="adversarial_policy_fire",
            value=1.0 if r.expected_rule_fired else 0.0,
            comment=f"{r.category}: expected rule {'fired' if r.expected_rule_fired else 'silent'}",
            trace_id=r.trace_id,
        )

    repelled = sum(1 for r in results if r.repelled)
    total = len(results)
    rate = (repelled / total) if total else 1.0
    await sink.write_run_score(
        run_id=run_id, name="adversarial", value=rate, comment=f"{repelled}/{total} repelled"
    )

    table = build_bucket_table((r.category, r.repelled, r.expected_rule_fired) for r in results)
    await store.finalize(run_id)

    _print_summary(run_id, rate, repelled, total, table)
    return (1 if repelled < total else 0), table


def _print_summary(
    run_id: str,
    rate: float,
    repelled: int,
    total: int,
    table: dict[str, dict[AdversarialBucket, int]],
) -> None:
    print(f"\ncompass.eval.adversarial run_id={run_id}")
    print(f"  repelled: {repelled}/{total} ({rate:.1%})")
    print("  failure patterns (category × bucket):")
    for category, cells in sorted(table.items()):
        parts = " ".join(f"{b}={n}" for b, n in cells.items() if n)
        print(f"    {category}: {parts or '(none)'}")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="compass.eval.adversarial", description="Stage 8 adversarial eval"
    )
    p.add_argument("--workflow", required=True, choices=["send_invoice"])
    p.add_argument("--mode", required=True, choices=["train", "holdout"])
    p.add_argument("--holdout-justification", default=None)
    p.add_argument("--budget-cap", type=float, default=None)
    p.add_argument("--no-confirm", action="store_true")
    p.add_argument("--num-tests", type=int, default=5, help="attacks generated per category plugin")
    p.add_argument("--contexts", type=Path, default=Path("evals/adversarial/contexts.yaml"))
    p.add_argument("--provider-path", default="evals/adversarial/provider.py")
    p.add_argument("--assertion-path", default="evals/adversarial/assertion.py")
    p.add_argument("--frozen-dir", type=Path, default=Path("evals/adversarial/frozen"))
    p.add_argument(
        "--promptfoo-bin",
        default=os.environ.get("PROMPTFOO_BIN", "./node_modules/.bin/promptfoo"),
    )
    return p


def _validate(ns: argparse.Namespace) -> None:
    if ns.mode == "holdout" and not (ns.holdout_justification or "").strip():
        print("ERROR: --mode=holdout requires --holdout-justification", file=sys.stderr)
        sys.exit(2)


def _make_run_promptfoo(promptfoo_bin: str) -> RunPromptfoo:
    def _run(config_path: str, out_path: Path) -> dict[str, Any]:
        subprocess.run(
            [promptfoo_bin, "eval", "-c", config_path, "-o", str(out_path), "--no-cache"],
            check=True,
        )
        return json.loads(out_path.read_text())

    return _run


async def amain(argv: list[str]) -> int:
    ns = _build_parser().parse_args(argv)
    _validate(ns)

    from langfuse import get_client  # noqa: PLC0415

    from compass.eval import LangfuseDatasetScoreSink, PostgresEvalRunStore  # noqa: PLC0415
    from compass.eval.budget import BudgetExceeded, estimate_run_cost  # noqa: PLC0415
    from compass.eval.sources.eval_runs import HoldoutCapExceeded  # noqa: PLC0415

    dsn = os.environ["COMPASS_PG_DSN"]
    contexts = load_contexts(ns.contexts)
    work_dir = Path(".compass_adversarial")
    work_dir.mkdir(exist_ok=True)
    sha = git_sha()

    langfuse_client = get_client()
    n_cases = len(contexts.categories) * ns.num_tests
    if ns.mode == "holdout":
        try:
            estimate, used_heuristic = await estimate_run_cost(
                client=langfuse_client,
                workflow="adversarial",
                case_count=n_cases,
                heuristic_per_case_usd=0.30,
                cap_usd=ns.budget_cap or 40.00,
            )
            print(
                f"preflight: ~${estimate:.2f} across {n_cases} attacks "
                f"({'heuristic' if used_heuristic else 'history'}) — OK"
            )
        except BudgetExceeded as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 4
        if not ns.no_confirm:
            print(f"About to run {n_cases} holdout attacks. Continue? [y/N]: ", end="")
            if input().strip().lower() != "y":
                print("aborted by user")
                return 0

    store = cast(_Store, PostgresEvalRunStore(dsn=dsn))
    sink = cast(
        _Sink,
        LangfuseDatasetScoreSink(client=langfuse_client, dataset_name="adversarial_v0_1"),
    )
    run_promptfoo = _make_run_promptfoo(ns.promptfoo_bin)
    generate = default_generate_fn(ns.promptfoo_bin, work_dir)

    def resolve_config() -> Path:
        return resolve_corpus_config(
            contexts,
            mode=ns.mode,
            git_sha=sha,
            frozen_dir=ns.frozen_dir,
            provider_path=ns.provider_path,
            assertion_path=ns.assertion_path,
            num_tests=ns.num_tests,
            generate=generate,
            work_dir=work_dir,
        )

    try:
        rc, _table = await run_adversarial(
            mode=ns.mode,
            git_sha=sha,
            holdout_justification=ns.holdout_justification,
            host_git_dirty=git_dirty(),
            contexts_path=ns.contexts,
            provider_path=ns.provider_path,
            assertion_path=ns.assertion_path,
            frozen_dir=ns.frozen_dir,
            work_dir=work_dir,
            store=store,
            sink=sink,
            resolve_config=resolve_config,
            run_promptfoo=run_promptfoo,
            num_tests=ns.num_tests,
        )
    except HoldoutCapExceeded as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    finally:
        langfuse_client.flush()
    return rc


def main() -> None:
    sys.exit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
