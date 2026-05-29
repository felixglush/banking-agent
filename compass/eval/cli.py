"""compass.eval CLI. Parses args, validates the mode gates, dispatches
to run_eval.

Exit codes:
  0 — full pass
  1 — at least one suite case-level failure
  2 — invalid CLI args (missing justification, unknown suite, etc.)
  3 — holdout cap exceeded for this git_sha (raised by EvalRunStore)
  4 — pre-flight budget exceeded
  5 — infra (Postgres / Langfuse) unavailable
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal, cast

from compass.eval.orchestrator import EvalReport

VALID_SUITES = {"functional", "policy_compliance", "cost_latency"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="compass.eval", description="Stage 7 eval harness")
    p.add_argument("--workflow", required=True, choices=["send_invoice"])
    p.add_argument("--mode", required=True, choices=["train", "holdout"])
    p.add_argument("--suites", required=True,
                   help="comma-separated: functional,policy_compliance,cost_latency")
    p.add_argument("--cases", default="", help="comma-separated case_id subset")
    p.add_argument(
        "--ground-truth-root",
        type=Path,
        default=Path.cwd() / "synthetic_account_1" / "ground_truth",
        help="Path to the directory with train/ and holdout/ JSONL splits.",
    )
    p.add_argument(
        "--task-queue",
        default="send-invoice",
        help="Temporal task queue the workflow worker polls.",
    )
    p.add_argument(
        "--dataset-name",
        default=None,
        help="Langfuse dataset name. Defaults to ground_truth/"
             "dataset_manifest.json's name (written by simulate.py "
             "--dataset-name), else <workflow>_v0_1.",
    )
    p.add_argument("--ablation", action="store_true",
                   help="run twice: policy on then off, link via paired_run_id")
    p.add_argument("--holdout-justification", default=None,
                   help="required when --mode=holdout")
    p.add_argument("--budget-cap", type=float, default=None,
                   help="override holdout budget in USD")
    p.add_argument("--no-confirm", action="store_true",
                   help="skip interactive holdout-mode confirmation")
    p.add_argument("--concurrency", type=int, default=4,
                   help="max cases run in parallel (default 4; cases are "
                        "independent). Raise for speed, lower to ease load on "
                        "the worker / OpenAI rate limits.")
    # ---- agent ablation levers (passed into each SendInvoiceRequest) ----
    p.add_argument("--prompt-variant", choices=["fixed", "legacy"], default="fixed",
                   help="agent prompt: 'fixed' (default) or 'legacy' "
                        "(abstention-prone baseline).")
    p.add_argument("--invoice-tool", action=argparse.BooleanOptionalAction, default=True,
                   help="give the agent the compute_line_total/compute_invoice_total "
                        "tools (default on; --no-invoice-tool to disable).")
    p.add_argument("--self-heal-attempts", type=int, default=0,
                   help="on a pre_action_proposal policy block, feed the "
                        "violation back to the agent and retry up to N times "
                        "(0 = off).")
    return p


def validate_args(ns: argparse.Namespace) -> None:
    """Mode-gate validation. Exits non-zero on failure."""
    suites_raw: str = ns.suites
    suites = [s.strip() for s in suites_raw.split(",") if s.strip()]
    bad = [s for s in suites if s not in VALID_SUITES]
    if bad:
        print(f"ERROR: unknown suite(s): {bad}. Valid: {sorted(VALID_SUITES)}",
              file=sys.stderr)
        sys.exit(2)
    if ns.mode == "holdout":
        j_raw: str | None = ns.holdout_justification
        j = (j_raw or "").strip()
        if not j:
            print("ERROR: --mode=holdout requires --holdout-justification",
                  file=sys.stderr)
            sys.exit(2)


def _resolve_dataset_name(ns: argparse.Namespace, ground_truth_root: Path) -> str:
    """--dataset-name override → manifest label → <workflow>_v0_1 default."""
    if ns.dataset_name:
        return cast(str, ns.dataset_name)
    manifest = ground_truth_root / "dataset_manifest.json"
    if manifest.exists():
        name = json.loads(manifest.read_text()).get("dataset_name")
        if name:
            return cast(str, name)
    return f"{ns.workflow}_v0_1"


def _git_sha() -> str:
    """HEAD of the invoking repo (cwd), not of the compass install."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
        ).decode().strip()
    except subprocess.CalledProcessError:
        return "unknown"


def _git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"],
        ).decode().strip()
        return bool(out)
    except subprocess.CalledProcessError:
        return False


async def amain(argv: list[str]) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    validate_args(ns)

    # Lazy imports so CLI parsing tests don't pay for them
    from langfuse import get_client  # noqa: PLC0415
    from temporalio.client import Client  # noqa: PLC0415
    from temporalio.contrib.opentelemetry import OpenTelemetryPlugin  # noqa: PLC0415

    from compass.eval import (  # noqa: PLC0415
        LangfuseDatasetScoreSink,
        Mode,
        PostgresAuditLogSource,
        PostgresEvalRunStore,
        TemporalWorkflowRunner,
        run_eval,
    )
    from compass.eval.budget import BudgetExceeded, estimate_run_cost  # noqa: PLC0415
    from compass.eval.corpus import load_corpus  # noqa: PLC0415
    from compass.eval.sources.eval_runs import HoldoutCapExceeded  # noqa: PLC0415

    dsn = os.environ["COMPASS_PG_DSN"]
    ground_truth_root: Path = ns.ground_truth_root
    mode = Mode(ns.mode)
    suites = [s.strip() for s in ns.suites.split(",") if s.strip()]

    # Full corpus backs the Langfuse Dataset; --cases only subsets what runs.
    corpus = load_corpus(workflow=ns.workflow, mode=mode,
                         ground_truth_root=ground_truth_root)
    cases = corpus
    if ns.cases:
        wanted = set(ns.cases.split(","))
        cases = [c for c in cases if c.case_id in wanted]

    langfuse_client = get_client()
    if ns.mode == "holdout":
        try:
            estimate, used_heuristic = await estimate_run_cost(
                client=langfuse_client, workflow=ns.workflow,
                case_count=len(cases),
                heuristic_per_case_usd=0.30,
                cap_usd=ns.budget_cap or 40.00,
            )
            print(f"preflight: estimated ${estimate:.2f} across {len(cases)} cases "
                  f"({'heuristic' if used_heuristic else 'Langfuse history'}) — OK")
        except BudgetExceeded as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 4
        if not ns.no_confirm:
            print(f"About to run {len(cases)} holdout cases. Continue? [y/N]: ", end="")
            if input().strip().lower() != "y":
                print("aborted by user")
                return 0

    temporal_target = os.environ.get("TEMPORAL_TARGET", "localhost:7233")
    # The OpenTelemetry plugin installs the client-side interceptor that
    # injects the active span context into workflow headers — that is what
    # propagates the runner's deterministic trace id into the worker's trace.
    temporal_client = await Client.connect(
        temporal_target, plugins=[OpenTelemetryPlugin()],
    )
    runner = TemporalWorkflowRunner(
        client=temporal_client, task_queue=ns.task_queue,
        langfuse_client=langfuse_client,
        prompt_variant=cast(Literal["fixed", "legacy"], ns.prompt_variant),
        use_invoice_tool=ns.invoice_tool,
        self_heal_max_attempts=ns.self_heal_attempts,
    )

    rule_src = PostgresAuditLogSource(dsn=dsn)
    eval_store = PostgresEvalRunStore(dsn=dsn)
    score_sink = LangfuseDatasetScoreSink(
        client=langfuse_client,
        dataset_name=_resolve_dataset_name(ns, ground_truth_root),
    )
    # Upload the full corpus as a Langfuse Dataset once (items upsert on
    # case_id); --cases narrows only what runs, not what the dataset holds.
    await score_sink.ensure_dataset(corpus)

    async def invoice_lookup(invoice_id: str) -> dict[str, Any] | None:
        import psycopg  # noqa: PLC0415
        async with (
            await psycopg.AsyncConnection.connect(dsn) as conn,
            conn.cursor() as cur,
        ):
            await cur.execute(
                """
                SELECT customer_id, contract_id, currency, source_type, total_cents
                  FROM invoices WHERE id = %s
                """,
                (invoice_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "customer_id": row[0], "contract_id": row[1], "currency": row[2],
            "source_type": row[3], "total_cents": row[4],
        }

    try:
        if ns.ablation:
            report_on = await run_eval(
                runner=runner, cases=cases, suites=suites, mode=mode,
                git_sha=_git_sha(),
                rule_fire_source=rule_src, score_sink=score_sink,
                eval_run_store=eval_store, langfuse_client=langfuse_client,
                invoice_lookup=invoice_lookup,
                holdout_justification=ns.holdout_justification,
                host_git_dirty=_git_dirty(),
                policy_enabled=True,
                concurrency=ns.concurrency,
            )
            os.environ["COMPASS_POLICY_DISABLE"] = "1"
            try:
                report_off = await run_eval(
                    runner=runner, cases=cases, suites=suites, mode=mode,
                    git_sha=_git_sha(),
                    rule_fire_source=rule_src, score_sink=score_sink,
                    eval_run_store=eval_store, langfuse_client=langfuse_client,
                    invoice_lookup=invoice_lookup,
                    holdout_justification=ns.holdout_justification,
                    host_git_dirty=_git_dirty(),
                    policy_enabled=False,
                    concurrency=ns.concurrency,
                )
            finally:
                os.environ.pop("COMPASS_POLICY_DISABLE", None)
            await eval_store.link_pair(report_on.run_id, report_off.run_id)
            report = report_on
            _print_lift_summary(report_on, report_off)
        else:
            report = await run_eval(
                runner=runner, cases=cases, suites=suites, mode=mode,
                git_sha=_git_sha(),
                rule_fire_source=rule_src, score_sink=score_sink,
                eval_run_store=eval_store, langfuse_client=langfuse_client,
                invoice_lookup=invoice_lookup,
                holdout_justification=ns.holdout_justification,
                host_git_dirty=_git_dirty(),
                concurrency=ns.concurrency,
            )
    except HoldoutCapExceeded as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    finally:
        # Export the root observations, dataset run items, and scores
        # buffered during the run before the process exits.
        langfuse_client.flush()

    print(f"\ncompass.eval run_id={report.run_id} mode={mode.value}")
    any_fail = False
    for suite_name, summary in report.suite_summaries.items():
        total = summary.passes + summary.fails
        pct = (summary.passes / total * 100) if total else 0.0
        print(f"  {suite_name}: {summary.passes}/{total} ({pct:.1f}%)")
        if summary.fails:
            any_fail = True
            for case_id, reason in summary.failure_details[:5]:
                print(f"    {case_id}: {reason}")
            if len(summary.failure_details) > 5:
                print(f"    ... and {len(summary.failure_details) - 5} more")

    return 1 if any_fail else 0


def _print_lift_summary(on: EvalReport, off: EvalReport) -> None:
    """Ablation lift = pass_rate(policy_on) − pass_rate(policy_off)."""
    print(f"\nAblation lift (paired runs {on.run_id} on, {off.run_id} off):")
    for suite in on.suite_summaries:
        on_total = on.suite_summaries[suite].passes + on.suite_summaries[suite].fails
        off_total = off.suite_summaries[suite].passes + off.suite_summaries[suite].fails
        on_rate = on.suite_summaries[suite].passes / on_total if on_total else 0.0
        off_rate = off.suite_summaries[suite].passes / off_total if off_total else 0.0
        print(f"  {suite}: on={on_rate:.1%}  off={off_rate:.1%}  lift={on_rate - off_rate:+.1%}")


def main() -> None:
    sys.exit(asyncio.run(amain(sys.argv[1:])))


if __name__ == "__main__":
    main()
