# Stage 8 — Adversarial eval (runbook)

Prereqs: `npm install` (pins promptfoo), Postgres (`COMPASS_PG_DSN`), a running
SendInvoice Temporal worker on `ADVERSARIAL_TASK_QUEUE` (default `send-invoice`),
OpenAI creds, Langfuse env.

Train (regenerate fresh, uncapped, spend logged):

    python -m compass.eval.adversarial --workflow send_invoice --mode train --num-tests 5

Holdout (freeze on first run per SHA, replay after):

    python -m compass.eval.adversarial --workflow send_invoice --mode holdout \
      --holdout-justification "release gate v0.x"

Scores `adversarial_response` (gating) and `adversarial_policy_fire` (diagnostic)
land on each attack's Langfuse trace; the run-level `adversarial` repelled-rate
and the (category × bucket) failure-pattern table print to stdout.
