# Stage 8 — Adversarial eval (runbook)

Five steps; **you run Promptfoo yourself** for generate + grade. Compass emits
configs, runs the Temporal probes, and scores locally. Full guide:
`docs/running_evals.md`.

Prereqs: `npm install`, `npx promptfoo auth` (one-time, for generate), Postgres
(`COMPASS_PG_DSN`), a SendInvoice worker polling `$ADVERSARIAL_TASK_QUEUE`
(default `send-invoice`), OpenAI creds.

All artifacts land wherever the `-o`/`--probes`/`--grade-config` paths point.
Keep them in the project-local, git-ignored `.compass_adversarial/` dir.

```sh
mkdir -p .compass_adversarial; RUN=.compass_adversarial

# ① compass: contexts.yaml -> one combined red-team config
uv run python -m compass.eval.adversarial gen-config -o "$RUN/redteam.yaml" --num-tests 5

# ② you: synthesize attacks
promptfoo redteam generate -c "$RUN/redteam.yaml" -o "$RUN/attacks.yaml"

# ③ compass: drive attacks to the gate (Temporal) -> grade config + probes
uv run python -m compass.eval.adversarial run \
  --attacks "$RUN/attacks.yaml" --grade-config "$RUN/grade.yaml" --probes "$RUN/probes.json"

# ④ you: grade the verdicts (echo provider, Node only)
promptfoo eval -c "$RUN/grade.yaml" -o "$RUN/grade_results.json"

# ⑤ compass: bucket table + exit code (local; exit 1 if any attack leaked)
uv run python -m compass.eval.adversarial score \
  --probes "$RUN/probes.json" --results "$RUN/grade_results.json"
```

`run` is in-process Python (no Promptfoo `python:` provider) and `grade` uses the
echo provider — so no `PYTHONPATH`/venv setup is needed.

`contexts.yaml` is the source of truth for categories (policy text + expected
rule ids). `run` recovers each generated attack's category by matching the policy
text inside the test. Freeze a corpus by keeping the `attacks.yaml` you generated.
