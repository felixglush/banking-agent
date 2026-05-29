#!/usr/bin/env bash
set -euo pipefail

# compass/ doesn't exist yet in Stage 1 — nothing to check.
if [ ! -d "compass" ]; then
    echo "compass/ not found — dependency-direction check skipped."
    exit 0
fi

# compass/eval/runner.py is the one intentional bridge: TemporalWorkflowRunner
# is the *default* WorkflowRunner impl that drives the v0.1 SendInvoiceWorkflow.
# The eval framework's reusable surface is the protocols (WorkflowRunner,
# RuleFireSource, ScoreSink, EvalRunStore); adopters substitute their own
# runner. The default impl shipped with compass.eval is allowed to import the
# v0.1 workflow it exists to run. Everything else in compass/ stays generic.
violations=$(
    grep -rnE "^[[:space:]]*(from|import)[[:space:]]+(workflows|mcp_bank|synthetic_account_1)(\.|[[:space:]]|$)" compass/ \
        | grep -vE "^compass/eval/runner\.py:" \
        || true
)
if [ -n "$violations" ]; then
    echo "$violations"
    echo "compass/ must not import from project code"; exit 1
fi

echo "Dependency-direction check passed."
