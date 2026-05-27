# `send_invoice` workflow

Stage 4 deliverable: a durable Temporal workflow that wraps an OpenAI
Agents SDK agent loop, gates on a human approval signal, then writes the
invoice and an audit row. Full design in
[`docs/superpowers/specs/2026-05-27-stage-4-send-invoice-workflow-design.md`](../../docs/superpowers/specs/2026-05-27-stage-4-send-invoice-workflow-design.md);
build-plan §Stage 4 is the contract.

## Components

| File | What it owns |
| --- | --- |
| `types.py` | Pydantic models (`SendInvoiceRequest`, `InvoiceProposal`, `LineItemProposal`, `ApprovalDecision`, `PolicyDecision`, `WorkflowResult`). |
| `agents.py` | `build_main_agent(mcp_server)` — single agent, `output_type=InvoiceProposal`, default model `gpt-5-nano`. |
| `activities.py` | `evaluate_policy` (Stage 4 stub → permit), `execute_send` (idempotent invoice insert), `audit_log` (append-only). |
| `workflow.py` | `SendInvoiceWorkflow` — agent loop → policy → wait_condition(approved) → execute_send → audit_log. |
| `worker.py` | Loads `.env.local`, wires `OpenAIAgentsPlugin` + `StatefulMCPServerProvider("bank", …)`, runs the worker. |

## Configuration

Drop into `.env.local` at the repo root (already gitignored):

```
OPENAI_API_KEY=sk-...
# optional override; defaults to gpt-5-nano
OPENAI_MODEL=gpt-5-nano

# the worker and the MCP subprocess both read this
COMPASS_PG_DSN=postgresql://compass:compass@localhost:5432/compass

# optional — set to push agent + workflow spans to Langfuse (or any
# OTLP collector). When unset the SDK keeps spans in-process.
# LANGFUSE_OTLP_ENDPOINT=http://localhost:3000/api/public/otel/v1/traces
# LANGFUSE_OTLP_AUTH=Basic <base64(public:secret)>
```

## Local demo

Four terminals:

```sh
# 1. Postgres sidecar (already in docker-compose.yml)
docker compose up -d

# Load the synthetic bank data (one-time per dataset regeneration)
uv run python -m synthetic_account_1.simulate
uv run python -m synthetic_account_1.load_to_postgres

# 2. Local Temporal (in-memory backend — fine for minutes-long runs)
temporal server start-dev
# UI at http://localhost:8233 ; gRPC at localhost:7233

# 3. The worker
uv run python -m workflows.send_invoice.worker

# 4. Drive the workflow
uv run python -m scripts.start_workflow \
    --message "Invoice Acme for last quarter's onboarding work"
# prints WORKFLOW_ID

uv run python -m scripts.approve_workflow WORKFLOW_ID --approve \
    --approver felixglush

# or:
uv run python -m scripts.approve_workflow WORKFLOW_ID --decline \
    --approver felixglush --notes "scope mismatch"
```

After approval the workflow writes one row into `invoices`, one row per
line item into `invoice_line_items`, and three rows into `audit_log`
(`proposal` → `approval_signal` → `executed`).

## Tests

```sh
uv run pytest tests/workflows/send_invoice/
```

Uses `temporalio.testing.WorkflowEnvironment.start_time_skipping()` plus
`AgentEnvironment` + `TestModel` so the agent loop runs against a
canned JSON proposal — no OpenAI, no MCP subprocess. Coverage:

- Happy path: invoice row + line items written; audit chain `proposal`
  → `approval_signal` → `executed` with actor metadata.
- Decline: audit chain ends `declined`; no row in `invoices`.
- Approval timeout: audit chain ends `declined` with `reason=approval_timeout`.
- Duplicate signal: second signal is logged once as
  `duplicate_approval_signal` and ignored.

The live OpenAI + MCP path is exercised by the demo above — not in CI.

## Stage 4 interop rules wired in code

From build-plan §Stage 4, marked with comments where they live:

1. `execute_send` is a workflow-step activity, never exposed to the
   agent as `activity_as_tool`. The agent's tool surface is read-only
   (the `bank` MCP) — `workflows/send_invoice/workflow.py` top-of-file
   note.
2. `evaluate_policy` distinguishes decision errors from engine /
   infra errors. Stage 4's stub never throws decisions; the shape is
   ready for Stage 5 to drop in real `PolicyDecisionError`.
3. `audit_log` writes are idempotent: `(workflow_run_id, sequence_no)`
   UNIQUE + `ON CONFLICT DO NOTHING`. `sequence_no` is a deterministic
   monotonic counter in workflow state. `event_kind` is **not** part
   of the key.
4. All MCP tools are read-only (Stage 3's contract). The plugin
   auto-retries the auto-wrapped MCP activities; idempotency is the
   guarantee — re-verify whenever adding tools.
