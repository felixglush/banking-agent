"""compass.eval.adversarial — Stage 8 adversarial eval, decomposed so the
operator runs Promptfoo themselves.

Subcommands (compass):
  gen-config   contexts.yaml                    → a combined Promptfoo red-team config
  run          attacks.yaml (operator-generated) → grade config + probes.json  (Temporal)
  score        probes.json + grade_results.json  → category × bucket table + exit code

Promptfoo steps the operator runs in between:
  promptfoo redteam generate -c <gen-config>   -o attacks.yaml
  promptfoo eval             -c <grade-config> -o grade_results.json

`run` needs Postgres (`COMPASS_PG_DSN`) and a SendInvoice worker polling
`--task-queue` (default `$ADVERSARIAL_TASK_QUEUE` or `send-invoice`). No Langfuse
/ Postgres writes — scoring is local.

Exit codes: 0 — every attack repelled (score) / command ok · 1 — at least one
attack leaked (score) · 2 — invalid args.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

import yaml

from compass.eval.adversarial_corpus import load_contexts
from compass.eval.adversarial_results import parse_grade_results
from compass.eval.adversarial_run import (
    build_combined_redteam_config,
    build_grade_config,
    map_attacks,
    probes_from_json,
    probes_to_json,
    run_probes,
    score_probes,
)
from compass.eval.types import AdversarialBucket


def _print_summary(
    repelled: int, total: int, table: dict[str, dict[AdversarialBucket, int]]
) -> None:
    rate = (repelled / total) if total else 1.0
    print(f"\nadversarial: repelled {repelled}/{total} ({rate:.1%})")
    print("  failure patterns (category × bucket):")
    for category, cells in sorted(table.items()):
        parts = " ".join(f"{b}={n}" for b, n in cells.items() if n)
        print(f"    {category}: {parts or '(none)'}")


def _cmd_gen_config(ns: argparse.Namespace) -> int:
    """Stage 1: contexts → one combined red-team config for the operator to run
    `promptfoo redteam generate` on."""
    contexts = load_contexts(ns.contexts)
    cfg = build_combined_redteam_config(contexts, num_tests=ns.num_tests)
    ns.out.write_text(yaml.safe_dump(cfg, sort_keys=False))
    print(f"wrote red-team config → {ns.out}  ({len(contexts.categories)} categories)")
    print(f"next: promptfoo redteam generate -c {ns.out} -o attacks.yaml")
    return 0


async def _cmd_run(ns: argparse.Namespace) -> int:
    """Stage 2: map the operator's generated attacks back to categories, drive
    each to the gate (Temporal, in-process), and emit the grade config +
    probes.json."""
    contexts = load_contexts(ns.contexts)
    generated = cast("dict[str, Any]", yaml.safe_load(ns.attacks.read_text()))
    attacks = map_attacks(generated, contexts)
    if not attacks:
        print(f"ERROR: no attacks (tests) found in {ns.attacks}", file=sys.stderr)
        return 2

    from temporalio.client import Client  # noqa: PLC0415
    from temporalio.contrib.opentelemetry import OpenTelemetryPlugin  # noqa: PLC0415

    from compass.eval.runner import TemporalWorkflowRunner  # noqa: PLC0415
    from compass.eval.sources.audit_log import PostgresAuditLogSource  # noqa: PLC0415

    dsn = os.environ["COMPASS_PG_DSN"]
    target = ns.temporal_target or os.environ.get("TEMPORAL_TARGET", "localhost:7233")
    client = await Client.connect(target, plugins=[OpenTelemetryPlugin()])
    runner = TemporalWorkflowRunner(client=client, task_queue=ns.task_queue, langfuse_client=None)
    audit = PostgresAuditLogSource(dsn=dsn)

    probes = await run_probes(attacks, run_probe=runner.run_probe, fired_rules=audit.rule_ids_fired)

    ns.grade_config.write_text(yaml.safe_dump(build_grade_config(probes), sort_keys=False))
    ns.probes.write_text(json.dumps(probes_to_json(probes), indent=2))
    print(f"ran {len(probes)} probes → grade config {ns.grade_config}, probes {ns.probes}")
    print(f"next: promptfoo eval -c {ns.grade_config} -o grade_results.json")
    return 0


def _cmd_score(ns: argparse.Namespace) -> int:
    """Stage 3: combine probes.json with the operator's grade results → table +
    exit code (1 if any attack leaked)."""
    probes = probes_from_json(json.loads(ns.probes.read_text()))
    repelled_by_case = parse_grade_results(json.loads(ns.results.read_text()))
    rc, table, repelled, total = score_probes(probes, repelled_by_case)
    _print_summary(repelled, total, table)
    return rc


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="compass.eval.adversarial", description="Stage 8 adversarial eval (operator-driven)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    contexts_default = Path("evals/adversarial/contexts.yaml")

    g = sub.add_parser("gen-config", help="emit a combined Promptfoo red-team config")
    g.add_argument("--contexts", type=Path, default=contexts_default)
    g.add_argument("--num-tests", type=int, default=5, help="attacks generated per plugin")
    g.add_argument("-o", "--out", type=Path, required=True)

    r = sub.add_parser("run", help="drive generated attacks to the gate (Temporal)")
    r.add_argument("--attacks", type=Path, required=True, help="promptfoo redteam generate output")
    r.add_argument("--contexts", type=Path, default=contexts_default)
    r.add_argument("--grade-config", type=Path, required=True, help="echo grade config to write")
    r.add_argument("--probes", type=Path, required=True, help="probes JSON to write")
    r.add_argument("--task-queue", default=os.environ.get("ADVERSARIAL_TASK_QUEUE", "send-invoice"))
    r.add_argument("--temporal-target", default=None)

    s = sub.add_parser("score", help="bucket verdicts + exit code (local, no DB/Langfuse)")
    s.add_argument("--probes", type=Path, required=True)
    s.add_argument("--results", type=Path, required=True, help="promptfoo eval output JSON")
    return p


async def amain(argv: list[str]) -> int:
    ns = _build_parser().parse_args(argv)
    if ns.cmd == "gen-config":
        return _cmd_gen_config(ns)
    if ns.cmd == "run":
        return await _cmd_run(ns)
    if ns.cmd == "score":
        return _cmd_score(ns)
    return 2  # unreachable: subparser is required


def main() -> None:
    sys.exit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
