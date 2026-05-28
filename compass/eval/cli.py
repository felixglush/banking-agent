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
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from compass.eval.orchestrator import EvalReport

REPO_ROOT = Path(__file__).resolve().parents[2]
VALID_SUITES = {"functional", "policy_compliance", "cost_latency"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="compass.eval", description="Stage 7 eval harness")
    p.add_argument("--workflow", required=True, choices=["send_invoice"])
    p.add_argument("--mode", required=True, choices=["train", "holdout"])
    p.add_argument("--suites", required=True,
                   help="comma-separated: functional,policy_compliance,cost_latency")
    p.add_argument("--cases", default="", help="comma-separated case_id subset")
    p.add_argument("--ablation", action="store_true",
                   help="run twice: policy on then off, link via paired_run_id")
    p.add_argument("--holdout-justification", default=None,
                   help="required when --mode=holdout")
    p.add_argument("--budget-cap", type=float, default=None,
                   help="override holdout budget in USD")
    p.add_argument("--no-confirm", action="store_true",
                   help="skip interactive holdout-mode confirmation")
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


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
        ).decode().strip()
    except subprocess.CalledProcessError:
        return "unknown"


def _git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=REPO_ROOT,
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
    ground_truth_root = REPO_ROOT / "synthetic_account_1" / "ground_truth"
    mode = Mode(ns.mode)
    suites = [s.strip() for s in ns.suites.split(",") if s.strip()]

    cases = load_corpus(workflow=ns.workflow, mode=mode,
                        ground_truth_root=ground_truth_root)
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
    temporal_client = await Client.connect(temporal_target)
    runner = TemporalWorkflowRunner(client=temporal_client, task_queue="send-invoice")

    rule_src = PostgresAuditLogSource(dsn=dsn)
    eval_store = PostgresEvalRunStore(dsn=dsn)
    score_sink = LangfuseDatasetScoreSink(
        client=langfuse_client, dataset_name=f"{ns.workflow}_v0_1",
    )

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
            )
    except HoldoutCapExceeded as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3

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
