# Running evals

Two eval harnesses run against the same SendInvoice workflow:

| | **Regular evals** (`compass.eval`) | **Adversarial evals** (`compass.eval.adversarial`) |
|---|---|---|
| Corpus | labeled ground-truth cases (`synthetic_account_1/ground_truth/{train,holdout}`) | attacks **generated** by Promptfoo red-team from `evals/adversarial/contexts.yaml` |
| Inputs | benign requests with known-correct outcomes | hostile requests (injection, over-cap amounts, wrong recipient, KYC/citation) |
| What runs | `run_case` — approves/declines and lets the workflow **complete** | `run_probe` — drives to the policy gate, then **declines** so nothing is sent |
| Measures | correctness, policy compliance, cost/latency vs. ground truth | repelled-rate: did the gate stop the attack |
| Pass = | per-suite case passes | attack repelled (gate blocked / asked to clarify) |
| Grader | deterministic checks vs. expected fields | Promptfoo `llm-rubric` (echo provider) |

Regular evals write per-case scores to Langfuse + run rows to Postgres `eval_runs` and honor a `train`/`holdout` split (see [train vs holdout](#train-vs-holdout)). Adversarial evals are **file-based and local** — you run Promptfoo yourself and manage the corpus/grade artifacts as files; scoring prints to stdout.

---

## 1. One-time setup

```sh
uv sync                      # Python deps (creates .venv)
npm install                  # pins promptfoo 0.121.13 (adversarial only)
```

Create `.env.local` at the repo root (loaded by the worker; source it for the CLIs):

```sh
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5-nano            # optional; this is the default
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
LANGFUSE_HOST=https://cloud.langfuse.com
```

Adversarial **generation only**: Promptfoo red-team requires a one-time email
verification — run `npx promptfoo auth` (or `./node_modules/.bin/promptfoo auth`)
once. Not needed for the run/grade stages or for holdout replay.

---

## 2. Bring up the runtime (every session)

Four things must be up before any eval: Postgres, Temporal, the DB loaded with synthetic data, and the worker for processing Temporal activities.

```sh
# 2a. Postgres (localhost:5432, user/db/pass = compass)
docker compose up -d

# 2b. Temporal dev server (localhost:7233, UI :8233)
temporal server start-dev

# 2c. Load schema + synthetic data (idempotent: runs db/schema.sql, then reloads bank tables)
export COMPASS_PG_DSN="postgres://compass:compass@localhost:5432/compass"
uv run python -m synthetic_account_1.load_to_postgres

# 2d. SendInvoice worker — polls task_queue "send-invoice"; reads .env.local for OPENAI_API_KEY
uv run python -m workflows.send_invoice.worker
# wait for: "send-invoice worker polling task_queue=send-invoice"
```

Leave the worker running in its own terminal. Both eval harnesses drive workflows
through it.

For the CLI shells below, export the DSN and source the keys (the CLIs do **not**
auto-load `.env.local`):

```sh
export COMPASS_PG_DSN="postgres://compass:compass@localhost:5432/compass"
set -a; . ./.env.local; set +a
```

---

## 3. Regular evals — `compass.eval`

Prerequisite: https://www.promptfoo.dev/docs/installation/

Runs the labeled corpus through the workflow and scores each suite.

```sh
uv run python -m compass.eval \
  --workflow send_invoice \
  --mode train \
  --suites functional,policy_compliance,cost_latency
```

Suites (`--suites`, comma-separated, at least one required):

- **functional** — did the workflow reach the expected outcome (`sent` / `declined`
  / `policy_rejected` / `needs_clarification`) and draft the expected invoice fields.
- **policy_compliance** — did the expected rules fire / the expected gate decision occur.
- **cost_latency** — token cost and latency within budget.

Useful flags:

| Flag | Purpose |
|---|---|
| `--cases id1,id2` | run a subset (the full corpus still backs the Langfuse dataset) |
| `--concurrency N` | parallel cases (default 4) |
| `--ablation` | run policy **on** then **off**, link the pair, print pass-rate lift |
| `--prompt-variant fixed\|legacy` | agent prompt variant (default `fixed`) |
| `--no-invoice-tool` | drop the invoice-math tools (ablation) |
| `--self-heal-attempts N` | on a policy block, feed the violation back and retry N times |
| `--dataset-name NAME` | Langfuse dataset (default: manifest name, else `<workflow>_v0_1`) |

Current corpus sizes: train = 119 cases, holdout = 51.

**Output:** per-suite `passes/total` and the first failures print to stdout; the
`run_id` is echoed. Exit codes: `0` full pass · `1` ≥1 case failed · `2` bad args
· `3` holdout cap exceeded · `4` budget exceeded · `5` infra unavailable.

---

## 4. Adversarial evals — `compass.eval.adversarial`

```
  ① gen-config   compass         contexts.yaml ─────────────► redteam.yaml
  ② generate     promptfoo       redteam.yaml ─────────────► attacks.yaml
  ③ run          compass         attacks.yaml ─► [Temporal] ─► grade.yaml + probes.json
  ④ grade        promptfoo       grade.yaml (echo provider) ─► grade_results.json
  ⑤ score        compass         probes.json + grade_results.json ─► table + exit code
```

All five files are **inputs/outputs you manage as files** — they land wherever the
`-o` / `--probes` / `--grade-config` paths point, relative to your current
directory. Keep them in the project-local, git-ignored `.compass_adversarial/`
dir so they don't scatter:

```sh
mkdir -p .compass_adversarial
export RUN=.compass_adversarial   # holds this run's artifacts
```

1. Emit a combined red-team config:

   ```sh
   uv run python -m compass.eval.adversarial gen-config -o "$RUN/redteam.yaml" --num-tests 5
   ```

2. Synthesize attacks:

   ```sh
   promptfoo redteam generate -c "$RUN/redteam.yaml" -o "$RUN/attacks.yaml"
   ```

3. Drive attacks to the gate (compass, Temporal):

   ```sh
   uv run python -m compass.eval.adversarial run \
     --attacks "$RUN/attacks.yaml" --grade-config "$RUN/grade.yaml" --probes "$RUN/probes.json"
   ```

4. Grade the verdicts:

   ```sh
   promptfoo eval -c "$RUN/grade.yaml" -o "$RUN/grade_results.json"
   ```

5. Score:

   ```sh
   uv run python -m compass.eval.adversarial score \
     --probes "$RUN/probes.json" --results "$RUN/grade_results.json"
   ```

---

## Where results land

**Regular evals:**
- **Postgres `eval_runs`** — one row per run (`run_id`, git SHA, mode, suites,
  holdout justification, policy on/off). Join key to Langfuse.
- **Langfuse** — each case is a Dataset Run item with its workflow trace attached
  (trace id seeded deterministically from the workflow id) and its scores.
- **stdout** — per-suite pass rates.

**Adversarial evals** (local, file-based — no Langfuse/Postgres writes):
- `attacks.yaml` (you generate) · `grade.yaml` + `probes.json` (`run` writes) ·
  `grade_results.json` (you grade) — all on disk.
- **stdout** — repelled-rate + (category × bucket) table from `score`.

---

## Troubleshooting

- **Probes time out / hang** — the worker isn't polling `send-invoice`. Check its
  log for `polling task_queue=send-invoice`; confirm `COMPASS_PG_DSN` is set in
  the worker's environment (it spawns the `bank` MCP with it).
- **Adversarial generate fails on "email verification required"** — run
  `npx promptfoo auth` once (step ②), or re-run steps ③–⑤ against an `attacks.yaml`
  you already generated.
- **`run` says "no attacks (tests) found"** — your `attacks.yaml` has no `tests:`
  list, or it's not the `promptfoo redteam generate` output. Check step ②'s output.
