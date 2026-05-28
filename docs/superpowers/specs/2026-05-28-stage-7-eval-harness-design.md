# Stage 7 — Eval Harness Core (`compass.eval`)

Design spec for the Stage 7 build item from `docs/build-plan.md`. Settles
the architecture, data model, suite implementations, CLI surface, and
ground-truth corpus expansion needed before writing an implementation plan.

Decisions in this spec were converged through a structured brainstorm; the
six questions answered along the way are noted in §Decision log at the end.

## Architecture

`compass.eval` is a new submodule of the existing `compass/` package. The
public API exports the following:

**Protocols** (the reusability surface — adopters can swap any of these):

* `WorkflowRunner` — `run_case(case) -> CaseResult`. Drives the workflow under test.
* `RuleFireSource` — `rule_ids_fired(workflow_run_id) -> set[str]`. Read side of policy compliance assertions.
* `ScoreSink` — `write_score(run_id, item_id, name, value, comment)`. Per-case score storage.
* `EvalRunStore` — `allocate_run / link_pair / finalize`. Harness-control state for an eval run.

**Default implementations** (ship with `compass.eval`, used by send-invoice at v0.1):

* `TemporalWorkflowRunner` — `client.execute_workflow(...)` + approval signal based on `case.expected_outcome`.
* `PostgresAuditLogSource` — SQL query against the `audit_log` table.
* `LangfuseDatasetScoreSink` — Langfuse Dataset Run score writes.
* `PostgresEvalRunStore` — writes to the `eval_runs` table.

**Top-level entry:**

* `run_eval(*, runner, corpus, mode, suites, rule_fire_source=PostgresAuditLogSource(), score_sink=LangfuseDatasetScoreSink(), eval_run_store=PostgresEvalRunStore()) -> EvalReport`
* `EvalReport` — dataclass holding per-suite aggregates and a deep-link to the Langfuse Dataset Run.

Adopters with different runtime stores (different audit schema, non-Langfuse
score destination, non-Postgres control state) implement the relevant
protocol and pass it to `run_eval`. The protocols are thin — they name
behavior the default impls already exhibit — so the v0.1 surface is one
impl per protocol; v0.2 and external adopters validate the abstraction by
substituting alternative impls.

### Layout

```
compass/eval/
  __init__.py             # public exports (protocols, default impls, run_eval)
  protocols.py            # WorkflowRunner, RuleFireSource, ScoreSink, EvalRunStore
  runner.py               # TemporalWorkflowRunner (default WorkflowRunner impl)
  sources/
    __init__.py
    audit_log.py          # PostgresAuditLogSource (default RuleFireSource impl)
    langfuse_scores.py    # LangfuseDatasetScoreSink (default ScoreSink impl)
    eval_runs.py          # PostgresEvalRunStore (default EvalRunStore impl)
  suites/
    __init__.py
    functional.py         # field-by-field scoring of result vs expected
    policy_compliance.py  # consumes RuleFireSource; asserts rule_ids fired
    cost_latency.py       # Langfuse passthrough (token/latency aggregates)
  corpus.py               # JSONL loader; mode gate; holdout chroot enforcement
  budget.py               # pre-flight cost estimate from Langfuse history
  cli.py                  # `uv run python -m compass.eval ...`

evals/
  run_config.yaml         # pinned per-workflow knobs
  judge_config.yaml       # empty placeholder for Stage 19
  runs/<run_id>/          # local artifacts: log, intermediate aggregates
```

The `evals/` directory is created with only the v0.1 contents. Stages 8 and 9
will add `evals/adversarial/` and `evals/counterfactual/` when those stages
ship — Stage 7 does **not** pre-decide those shapes.

### Data flow per case

1. Harness loads case from the corpus, respecting `--mode`.
2. Creates or reuses a Langfuse Dataset Run; adds the case as a Dataset Item
   if not already present.
3. `runner.run_case(case)` calls `client.execute_workflow(SendInvoiceWorkflow,
   ..., id=<workflow_run_id>)`. If `case.expected_outcome in {sent, declined}`,
   the runner sends an `approve` signal after `start_workflow` returns (signals
   buffer against the workflow id until the workflow consumes them, so order
   is safe by construction). Awaits `handle.result()`.
4. Each suite scores the case independently:
   * `functional` compares the returned `WorkflowResult` (and, for `sent`
     cases, the persisted invoice row) against `case.expected`.
   * `policy_compliance` queries `audit_log` for `rule_fired` rows keyed on
     `workflow_run_id`.
   * `cost_latency` reads token / latency aggregates from the Langfuse trace.
5. Each suite's score is written as a Langfuse Dataset Run score on the
   per-item trace (`name=<suite>`, `value=0.0|1.0`, `comment=` failure detail).

`workflow_run_id` is the join key everywhere: Postgres stores it on
`audit_log` rows; Langfuse uses it as the trace id by construction (the
OpenAI Agents plugin propagates `workflow.info().workflow_id` as the OTel
trace id — already wired and in production via the Tier 1/2 Langfuse work
landed alongside this spec).

### Why this shape

`compass.eval` is fully runtime-store-agnostic via four protocols
(`WorkflowRunner`, `RuleFireSource`, `ScoreSink`, `EvalRunStore`). Suites and
`run_eval` consume the protocols; default implementations live in
`compass.eval.sources` and `compass.eval.runner` and represent the v0.1
choices (Temporal, Postgres `audit_log`, Langfuse Datasets, Postgres
`eval_runs`).

The default `PostgresAuditLogSource` is the v0.1 implementation of
`RuleFireSource`. It queries `audit_log` for rows where `event_kind =
'rule_fired'`, synchronous and deterministic — no waiting for async trace
ingestion. Alternative implementations (e.g. `LangfuseTraceSource` that
reads OTel events, or a custom impl over a non-Postgres store) are
straightforward to add: implement the one-method protocol and pass it to
`run_eval`. The build plan explicitly endorses the audit-log read path
("queries the per-test trace in Langfuse, or equivalently the audit_log
rows"); the protocol just makes the choice swappable.

The other three protocols (`WorkflowRunner`, `ScoreSink`, `EvalRunStore`)
follow the same pattern: a thin Protocol naming the behavior the default
impl already exhibits. The result at v0.1 is one impl per protocol; v0.2
(dispute workflow) is the first reuse test, and external adopters validate
the abstraction by substituting alternative impls. Stage 21 reports the
list of public-API gaps surfaced and how each was closed.

## Data model

### `eval_runs` schema additions

```sql
ALTER TABLE eval_runs
  ADD COLUMN paired_run_id  TEXT NULL REFERENCES eval_runs(run_id),
  ADD COLUMN policy_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN suite_names    TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN host_git_dirty BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE eval_runs
  ADD CONSTRAINT eval_runs_holdout_counter_unique
    UNIQUE (git_sha, commit_holdout_run_no),
  ADD CONSTRAINT eval_runs_justification_required
    CHECK (mode = 'train' OR length(trim(holdout_justification)) > 0),
  ADD CONSTRAINT eval_runs_suite_names_valid
    CHECK (suite_names <@ ARRAY['functional','policy_compliance','cost_latency']::text[]);

COMMENT ON COLUMN eval_runs.run_id IS
  'Stable identifier (uuid4 hex). Used as the Langfuse Dataset Run name; the join key from Postgres to Langfuse for the run.';
COMMENT ON COLUMN eval_runs.git_sha IS
  'HEAD commit at run start. Required for the per-commit holdout-run counter (build-plan §0: cap of 3 holdout runs per SHA).';
COMMENT ON COLUMN eval_runs.mode IS
  'train | holdout. Holdout mode requires holdout_justification and increments commit_holdout_run_no.';
COMMENT ON COLUMN eval_runs.holdout_justification IS
  'Free-text reason a holdout run was invoked (e.g. "release v0.1"). Required when mode=holdout; refused otherwise. Surfaces in reports so accidental holdout runs are visible after the fact.';
COMMENT ON COLUMN eval_runs.commit_holdout_run_no IS
  '1..3 — ordinal of this holdout run for this git_sha. UNIQUE(git_sha, commit_holdout_run_no) enforces the cap mechanically. NULL when mode=train.';
COMMENT ON COLUMN eval_runs.paired_run_id IS
  'Self-FK to the paired ablation run (policy-on ↔ policy-off). Lift computation joins through this. NULL when the run is standalone.';
COMMENT ON COLUMN eval_runs.policy_enabled IS
  'FALSE when the run executed with COMPASS_POLICY_DISABLE=1. Determines which side of an ablation pair this row represents; redundant with audit_log.policy_hash=''disabled-for-eval'' but cheap to query directly.';
COMMENT ON COLUMN eval_runs.suite_names IS
  'Suite list executed in this run. A paired-run report asserts both sides ran the same suite set before computing lift.';
COMMENT ON COLUMN eval_runs.host_git_dirty IS
  'TRUE if the working tree had uncommitted changes when the run started. Soft warning surfaced in the report; not a block (engineers iterate dirty in train mode).';
COMMENT ON COLUMN eval_runs.started_at IS
  'Wall-clock start of the harness invocation. Used for cost/latency rollups.';
COMMENT ON COLUMN eval_runs.finished_at IS
  'NULL while the run is in flight or if it crashed; set on clean completion. run_id rows are never deleted — a crashed run stays with finished_at=NULL for forensics.';
```

Holdout-counter atomicity uses a `SERIALIZABLE` transaction wrapping
`SELECT COALESCE(MAX(commit_holdout_run_no), 0) + 1 ... FOR UPDATE` followed
by `INSERT`. The `UNIQUE` constraint catches any race the SERIALIZABLE
escape misses.

### Ground-truth schema extension

`synthetic_account_1/ground_truth/{train,holdout}/invoice_resolution_labels.jsonl`
gains two fields per case:

```json
{
  "case_id": "ir_0001",
  "request": "Send invoice for Acme Corp — source: rate_card",
  "expected_outcome": "sent",
  "expected": { ... existing ... },
  "expected_decline_reason": null
}
```

* `expected_outcome` is one of `sent | declined | policy_rejected | timeout | unsupported`. v0.1 ships cases for the first three; `timeout` is theoretical (would require driving the approval timeout, which adds test latency for little signal) and `unsupported` is covered by the existing `scope_gate_labels.jsonl`.
* `expected_decline_reason` is non-null only when `expected_outcome = "declined"`. Free-text from a small enum: `amount_too_high_for_approver`, `customer_on_hold`, `requested_clarification`.

For `sent` cases, `expected` keeps its existing shape and is the source for
the functional suite's field-by-field comparison. For other outcomes, the
content of `expected` is ignored (kept for forward compatibility).

### Corpus expansion — sizing

The current corpus is all-`sent` by construction. New outcome classes need
case bodies that exercise the decline and policy-rejected code paths. Target
sizing balances statistical signal against generation cost; values below are
the smallest that produce a usable Wilson 95% lower bound on a hypothetical
100% holdout pass rate (LB shown below).

| outcome class    | train | holdout | LB on 100% holdout pass | What it proves |
| ---------------- | ----- | ------- | ----------------------- | -------------- |
| `sent`           | 84    | 36      | 90%                     | Headline four-amount-source claim (existing; untouched). |
| `declined`       | 14    | 10      | 69%                     | Decline branch works as a regression detector — not a headline percentage. |
| `policy_rejected`| 10    | 6       | 54%                     | Policy gate blocks. Granularity comes from `policy_compliance`'s set-match assertion (much stricter than binary). |
| `unsupported`    | —     | —       | —                       | Already covered by `scope_gate_labels.jsonl` (84/36 cases). |
| **total**        | **108** | **52** |                         | |

`policy_compliance` is what gives `policy_rejected` cases their teeth: a
case must trip the *exact expected rule set*, not just "some block". Six
holdout cases is enough because the assertion is set-equality rather than
boolean.

### Corpus expansion — generator

`synthetic_account_1/simulate.py` is the deterministic generator and the
right place to extend. New responsibilities:

* For `declined`: clone selected `sent` cases at deterministic indices (every
  Nth, with N chosen so the four-amount-source headline slice is untouched);
  flip `expected_outcome="declined"`, attach an `expected_decline_reason`
  drawn from the enum via a seeded RNG.
* For `policy_rejected`: synthesize cases that intentionally trip specific
  Billing-integrity primitives. One sub-case per primitive
  (`require_amount_source`, `contract_consistency_check`,
  `prohibit_exceed_contract_cap`, `currency_consistency_check`), each
  populating `expected_fired_rules` for the `policy_compliance` suite.
* `synthetic_account_1/verify.py` extends to assert per-outcome-class counts
  match the table above. A regression that flips a `sent` case to `declined`
  fails verify.

The generator remains seed-deterministic — re-running `simulate.py` yields
identical files.

### Langfuse Datasets shape

One Langfuse dataset per workflow, `send_invoice_v0_1`. One Dataset Item per
case, `item_id = case_id`. One Dataset Run per `compass.eval` invocation,
`run_name = eval_runs.run_id` so the Postgres↔Langfuse join is by name.

Per-case scores are written via the Dataset Run scoring API, one score
record per suite:

* `name = "functional"`, `value = 0.0 | 1.0`, `comment = "field_mismatch:[total_cents]"` (or empty on pass).
* `name = "policy_compliance"`, `value = 0.0 | 1.0`, `comment = "missing:[require_amount_source]; extra:[]"`.
* `name = "cost_latency"`, `value = 1.0` (always — passthrough), `comment = "tokens=2456 cost_usd=0.041 p95_ms=1820"`.

## Suites

### `suite_names` enum

Three v0.1 values: `functional`, `policy_compliance`, `cost_latency`.
Stages 8 and 9 extend the enum by `ALTER ... DROP CONSTRAINT ... ADD
CONSTRAINT` (append-only). **Ablation is not a suite** — it is a harness
mode (`--ablation`) that runs the same suite set twice with
`COMPASS_POLICY_DISABLE=1` toggled and links the two resulting `eval_runs`
rows via `paired_run_id`.

### Suite 1 — `functional`

**Inputs.** `case.expected_outcome`, `case.expected`; the `WorkflowResult`
returned by `runner.run_case(case)`; for `sent` cases, the persisted
`invoices` row read by `result.invoice_id`.

**Scoring.**

```
if result.outcome != case.expected_outcome:
    FAIL  reason=outcome_class_mismatch
elif case.expected_outcome == "sent":
    fields = ["customer_id", "contract_id", "currency", "source_type", "total_cents"]
    diffs = [f for f in fields if persisted[f] != case.expected[f]]
    PASS if not diffs else FAIL reason=f"field_mismatch:{diffs}"
else:
    PASS  # outcome-class match is sufficient
```

All fields exact-match at v0.1. `total_cents` is integer so no tolerance is
needed; the structure for tolerances exists in `run_config.yaml` so a future
non-zero band can be added without code change.

### Suite 2 — `policy_compliance`

**Inputs.** `case.expected_fired_rules` from
`policy_compliance_labels.jsonl` joined by `invoice_case_id`; the set
returned by the injected `RuleFireSource.rule_ids_fired(workflow_run_id)`.

**Default source.** `PostgresAuditLogSource` runs:

```sql
SELECT rule_id
  FROM audit_log
 WHERE workflow_run_id = $1
   AND event_kind = 'rule_fired';
```

and returns the set. Synchronous — `audit_log` rows are committed before
the workflow returns, so no polling or async-ingestion failure mode.

**Scoring.** Set-equality: `observed == expected`. Failure detail records
`missing = expected - observed` and `extra = observed - expected`
separately.

The suite consumes the protocol, not the SQL — an adopter with a different
audit store (Langfuse trace events, S3 audit JSON, a different table
shape) provides their own `RuleFireSource` impl and the suite is
unchanged.

### Suite 3 — `cost_latency`

**Inputs.** The Langfuse trace's native cost/latency aggregates (token usage
rolled up, span timings).

**Scoring.** Passthrough — score is always 1.0; the body carries the
numbers. Optional thresholds in `run_config.yaml` (`warn_per_case_usd`,
`warn_p95_latency_ms`) trigger warning lines in the run summary, never a
pass/fail. The cost *budget* lives in pre-flight per §CLI, not per-case.

This is the only suite that depends on Langfuse for *content* (vs. for
score storage). If the trace is missing when the suite runs, it logs a
warning and writes a score of 1.0 with `comment="trace_not_ingested"` — the
case is not failed on infra issues.

## CLI

```sh
uv run python -m compass.eval \
  --workflow send_invoice \
  --mode train \
  --suites functional,policy_compliance,cost_latency \
  [--cases ir_0001,ir_0007]                          # subset for iteration
  [--ablation]                                       # run twice, policy on then off
  [--holdout-justification "release v0.1"]           # required when --mode holdout
  [--budget-cap 40.00]                               # override run_config default
  [--no-confirm]                                     # skip interactive holdout confirm
```

### Mode gates

Evaluated before any case runs. Exit code in parentheses.

| Condition | Behavior |
| --- | --- |
| `--mode holdout` without `--holdout-justification` | exit 2; message points at `--holdout-justification`. |
| `--mode holdout` and counter for this `git_sha` already at 3 | exit 3; message lists the three prior `run_id`s. |
| `--mode holdout` and pre-flight estimate > `--budget-cap` | exit 4; show estimate breakdown; offer `--budget-cap` override. |
| `--mode holdout` and Langfuse has <3 prior train+holdout runs of this workflow | use `cost_heuristic_usd_per_case` from `run_config.yaml`, log a warning, proceed. |
| `--mode train` | counter not incremented; budget cap is advisory only. |
| `--mode holdout` and working tree has uncommitted changes | warn and set `host_git_dirty=TRUE`; do not block. |
| `--mode holdout` and `--no-confirm` not passed | print summary (corpus, suites, estimate) and prompt for `y/N`. |

### Pre-flight cost estimate

Source: Langfuse's run-history API for the workflow, last N=5 train or
holdout runs (whichever direction has data). Mean per-case cost × corpus
size = estimate. Cold-start fallback when <3 prior runs exist uses
`cost_heuristic_usd_per_case` from `run_config.yaml`.

Spent dollars during the run are recorded only in Langfuse — Postgres
`eval_runs` does not track per-run spend at v0.1 (Langfuse is the
authoritative cost store).

### Stdout

```
compass.eval run_id=ev_a3f… mode=train workflow=send_invoice suites=functional,policy_compliance,cost_latency
  preflight: estimated $4.20 across 108 cases (Langfuse last 5 train runs avg=$0.039/case) — OK
  [001/108] ir_0001 ............................ PASS  (0.6s, $0.04, fn ✓ pc ✓ cl ✓)
  [002/108] ir_0003 ............................ FAIL  fn:field_mismatch:[total_cents] pc ✓ cl ✓
  ...
  ────────────────────────────────────────────
  functional:        106/108 (98.1%, Wilson 95% LB 93.5%)
  policy_compliance: 108/108 (100%,  Wilson 95% LB 96.6%)
  cost_latency:      $4.17 total, p50=0.6s p95=2.1s
  Langfuse: https://cloud.langfuse.com/project/.../runs/ev_a3f…
```

Failures never abort the run. The harness exits **1** iff any suite has at
least one case-level failure; **0** on a fully-passing run. The infra exit
codes (2–5) are reserved for the pre-flight gates above and cannot collide.

### Failure semantics

| Failure | Treatment |
| --- | --- |
| Workflow execution raises (Temporal error) | Case fails `functional` with reason `workflow_error:<type>`. Run continues. |
| Approval signal send fails | Case fails `functional` with reason `approval_signal_error`. |
| `audit_log` query returns empty for a case that produced a workflow_run_id | Case fails `policy_compliance` with reason `audit_log_empty` (defect — every workflow run produces audit rows). |
| Langfuse trace missing for `cost_latency` | Warning; suite scores 1.0 with `comment="trace_not_ingested"`. Not lethal. |
| Langfuse API down at pre-flight | exit 5; cost estimate cannot be computed. |
| Postgres unavailable | exit 5; cannot allocate `run_id`. |

## Config

### `evals/run_config.yaml`

```yaml
version: 1

send_invoice:
  temperature: 0.2
  cost_heuristic_usd_per_case: 0.30
  holdout_budget_usd: 40.00
  cost_latency_trace_poll_timeout_seconds: 60   # cost_latency suite only
  warn_per_case_usd: 0.50
  warn_p95_latency_ms: 30000

suites:
  functional:
    field_tolerances:
      total_cents: 0
  policy_compliance:
    assert_set_equality: true
  cost_latency:
    enabled: true
```

`evals/judge_config.yaml` ships empty (one-line comment placeholder) — Stage
19's trace coherence work populates it.

## Testing strategy

### Unit (no DB, no Temporal, no Langfuse) — ~45 cases total

* `functional` suite scorer: 15 cases across `expected_outcome` classes
  and field-mismatch reasons.
* `policy_compliance` suite scorer: 8 cases including empty / exact /
  missing / extra / disjoint rule sets.
* Corpus loader: 5 cases including the train-mode chroot-bypass attempt.
* Budget pre-flight: 6 cases for `(0, 2, 5 prior runs) × (estimate,
  used_heuristic)`.
* CLI argparser: 10 cases for the `--mode holdout` gates.

### Integration (Postgres only) — ~6 cases

* `eval_runs` counter increment under concurrent inserts: 4 parallel
  attempts with one `git_sha`; assert exactly 3 succeed, the 4th hits
  UNIQUE.
* `paired_run_id` round-trip: create an ablation pair, assert the lift
  query returns the expected join.

### End-to-end — 1 case

A single `pytest.mark.e2e` smoke test (skipped in normal CI, runnable on
demand). Runs three cases (one `sent`, one `declined`, one
`policy_rejected`) through the real workflow against Temporal + Postgres +
Langfuse Cloud; asserts all three suites score correctly. Tagged because
each run costs ~$0.10 and depends on external services.

## Risks

| Risk | Mitigation |
| --- | --- |
| Approval signal arrives before workflow reaches `wait_condition`. | Temporal signals buffer against the workflow id until consumed. Sending immediately after `start_workflow()` is safe by construction. Verified in the e2e smoke test. |
| Langfuse Datasets API rate-limited at concurrent score writes. | Cap per-case concurrency at 8 in the runner; rely on Langfuse SDK's built-in batching. |
| `OpenTelemetryPlugin` (experimental in `temporalio==1.27`) destabilizes under replay. | Plugin is conditional on Langfuse env vars in the worker. If unstable, disable it in `_setup_tracing()` and lose activity-level Langfuse observations; `policy_compliance` is unaffected (reads `audit_log`). |
| Corpus expansion shifts the four-amount-source headline slice. | The generator's `declined`-case selection runs after the headline slice is locked; cloned cases reuse non-headline indices. `verify.py` asserts headline composition is invariant. |
| Cost regression mid-run burns past budget. | Budget gate is pre-flight only; running evals are not interrupted. Acceptable trade-off: the gate catches the predictable cost of a known corpus; a 10× mid-run regression is an LLM-provider incident, not an eval design problem. |
| Empty `holdout_justification`. | CHECK constraint refuses it at the DDL level. |
| `compass.eval` couples to the runtime `audit_log` schema. | Acknowledged. If the audit log moves stores in v0.2+, `compass.eval` migrates with it — the assertion query is one SQL statement, not a subsystem. |

## Decision log

The brainstorm produced seven binding choices:

1. **Score store:** Langfuse Dataset Runs (per build plan); Postgres holds only `eval_runs` control state.
2. **Approval mechanism:** per-case `expected_outcome` in ground truth drives the runner's signal.
3. **Cost estimate:** Langfuse-derived running average of last N=5 runs; heuristic fallback when N<3.
4. **Ablation schema:** `paired_run_id` (self-FK) + `policy_enabled` columns on `eval_runs`.
5. **Trace-assertion source:** `audit_log` (corrected from Langfuse trace events after spec walkthrough surfaced the async-ingestion cost; build plan §4 explicitly endorses the audit_log path).
6. **Spec scope:** Stage 7 only; Stages 8 and 9 decide their own directory layout when written.
7. **Reusability surface:** four protocols (`WorkflowRunner`, `RuleFireSource`, `ScoreSink`, `EvalRunStore`) with default impls (`TemporalWorkflowRunner`, `PostgresAuditLogSource`, `LangfuseDatasetScoreSink`, `PostgresEvalRunStore`) ship at v0.1. Adopters with different runtime stores substitute impls without forking the suite code.
