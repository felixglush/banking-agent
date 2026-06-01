# `send_invoice` workflow

A durable Temporal workflow that wraps an OpenAI Agents SDK agent
loop, gates on a human approval signal, then writes the invoice and
an audit row. 

Diagram: https://app.excalidraw.com/s/AfsNGrQkY99/55Mn7raUZsW

- [`docs/superpowers/specs/2026-05-27-stage-4-send-invoice-workflow-design.md`](../../docs/superpowers/specs/2026-05-27-stage-4-send-invoice-workflow-design.md)
- [`docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md`](../../docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md)
- [`docs/superpowers/specs/2026-05-27-stage-6-intent-classifier-design.md`](../../docs/superpowers/specs/2026-05-27-stage-6-intent-classifier-design.md)
- [`docs/superpowers/specs/2026-05-31-policy-drift-reevaluation-design.md`](../../docs/superpowers/specs/2026-05-31-policy-drift-reevaluation-design.md)


## Components

| File | What it owns |
| --- | --- |
| `types.py` | Pydantic models |
| `agents.py` | `build_main_agent(mcp_server)` — single agent, `output_type=InvoiceProposal` |
| `scope_gate.py` | `build_scope_gate_agent()` + `IntentClassification` |
| `context.py` | Pure projections over `RunResult` → generate policy context dicts |
| `primitives.py` | App-specific Billing-integrity primitives (`require_amount_source`, `contract_consistency_check`, `prohibit_exceed_contract_cap`, `currency_consistency_check`). |
| `activities.py` | `evaluate_policy`, `execute_send` (idempotent invoice insert), append-only `audit_log`, `resolve_invoice_context` (DB-direct re-fetch of customer/KYC + contract for the `pre_execute` re-evaluation). |
| `workflow.py` | Where the Temporal `SendInvoiceWorkflow` is defined |
| `worker.py` | Runs the workflow via activities |

## Workflow diagram

Solid arrows are the main control flow inside `SendInvoiceWorkflow.run`.
Dashed arrows show how the external `approve` signal feeds the
`wait_condition`. Orange = audit-log write; purple = Temporal activity
(or auto-activity inside `Runner.run`); green = policy phase gate;
blue = terminal `WorkflowResult.outcome`.

```mermaid
flowchart TD
    Start([SendInvoiceRequest]) --> ScopeGate[Scope-gate sub-agent<br/>Runner.run, max_turns=1<br/>no MCP, no tools]
    ScopeGate --> HasClass{classification?}
    HasClass -- no --> AuditNoClass[audit: agent_no_output<br/>phase=input_validation]
    AuditNoClass --> EndUnsupported1([unsupported])
    HasClass -- yes --> InputGate{{evaluate_policy<br/>phase=input_validation}}
    InputGate -- block --> AuditUnsupported[audit: unsupported<br/>+ classifier payload]
    AuditUnsupported --> EndUnsupported2([unsupported])
    InputGate -- permit --> AuditIntent[audit: intent_classified]
    AuditIntent --> Agent[Main agent loop<br/>stateful MCP: bank<br/>Runner.run, max_turns=10]
    Agent --> HasOutput{proposal?}
    HasOutput -- no --> AuditNoOut[audit: agent_no_output<br/>phase=pre_action_proposal]
    AuditNoOut --> EndRejected1([policy_rejected])
    HasOutput -- yes --> ProposalGate{{evaluate_policy<br/>phase=pre_action_proposal}}
    ProposalGate -- block --> AuditPolReject[audit: policy_rejected<br/>rule_ids_fired]
    AuditPolReject --> EndRejected2([policy_rejected])
    ProposalGate -- permit/escalate --> Wait[wait_condition<br/>_approval is not None<br/>timeout=approval_timeout_seconds]
    Wait -- timeout --> AuditTimeout[audit: declined<br/>reason=approval_timeout]
    AuditTimeout --> EndTimeout([timeout])
    Wait -- signal --> AuditSignal[audit: approval_signal]
    AuditSignal --> Approved{approved?}
    Approved -- no --> AuditDeclined[audit: declined]
    AuditDeclined --> EndDeclined([declined])
    Approved -- yes --> ReResolve[resolve_invoice_context<br/>re-fetch customer/KYC + contract]
    ReResolve --> PreExecGate{{evaluate_policy<br/>re-run pre_action_proposal<br/>under current policy + tamper}}
    PreExecGate -- block / tamper --> AuditDriftReject[audit: policy_rejected<br/>re-eval block / silent-mod]
    AuditDriftReject --> EndRejected3([policy_rejected])
    PreExecGate -- unapproved escalate --> AuditReapproval[audit: reapproval_required<br/>policy tightened since approval]
    AuditReapproval -. reset _approval, guard rounds .-> Wait
    PreExecGate -- permit --> Execute[execute_send]
    Execute --> AuditExecuted[audit: executed<br/>is_terminal_event=True<br/>runs audit_validation]
    AuditExecuted --> EndSent([sent + invoice_id])

    Signal[/approve signal/]:::signal -. first wins .-> SetApproval[_approval = decision]
    Signal -. duplicate .-> AuditDup[audit: duplicate_approval_signal]
    SetApproval -. unblocks .-> Wait

    classDef audit fill:#fff4e6,stroke:#d9822b,color:#000
    classDef terminal fill:#e6f3ff,stroke:#1f6feb,color:#000
    classDef activity fill:#f0e6ff,stroke:#6f42c1,color:#000
    classDef policy fill:#e8f7e8,stroke:#2da44e,color:#000
    classDef signal fill:#e8f7e8,stroke:#2da44e,color:#000
    class AuditNoClass,AuditUnsupported,AuditIntent,AuditNoOut,AuditPolReject,AuditTimeout,AuditSignal,AuditDeclined,AuditDriftReject,AuditReapproval,AuditExecuted,AuditDup audit
    class EndUnsupported1,EndUnsupported2,EndRejected1,EndRejected2,EndRejected3,EndTimeout,EndDeclined,EndSent terminal
    class ScopeGate,Agent,ReResolve,Execute activity
    class InputGate,ProposalGate,PreExecGate policy
```

Every audit-write and activity above is allocated a monotonic
`sequence_no` from workflow state, so retries collide on the
`(workflow_run_id, sequence_no)` UNIQUE constraint and are idempotent.

## Configuration

Drop into `.env.local` at the repo root (already gitignored):

```
OPENAI_API_KEY=sk-...

# main reasoning agent — drafts the InvoiceProposal. Default gpt-4.1-mini.
# OPENAI_MODEL=gpt-4.1-mini

# scope-gate classifier — small structured-output task. Default reuses
# OPENAI_MODEL's default; override independently once a distilled or
# faster classifier is wired in.
# OPENAI_SCOPE_GATE_MODEL=gpt-4.1-mini

# the worker and the MCP subprocess both read this
COMPASS_PG_DSN=postgresql://compass:compass@localhost:5432/compass

# optional — set to push agent + workflow + activity spans to Langfuse.
# When unset, tracing is disabled and spans stay in-process.
# LANGFUSE_PUBLIC_KEY=pk-lf-...
# LANGFUSE_SECRET_KEY=sk-lf-...
# LANGFUSE_HOST=https://cloud.langfuse.com    # EU; US: https://us.cloud.langfuse.com
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
line item into `invoice_line_items`, and an `audit_log` chain whose
exact shape depends on which rules fire. On a clean send-invoice run
with permitted policy at every phase, the rows are:
`intent_classified` (scope-gate permitted) →
nine `rule_skipped` rows at `pre_action_proposal` →
`approval_signal` →
`pre_execute_reevaluation` (marker) →
nine `rule_skipped` rows (the `pre_action_proposal` rules re-evaluated under
the current policy) + one `rule_skipped` row (the `pre_execute` tamper check) →
`executed` (plus two `rule_skipped` rows at `audit_validation`).
If policy tightened during the approval wait, the re-evaluation instead emits a
`rule_fired` + `reapproval_required`, the workflow loops back to
`wait_condition`, and a second `approval_signal` follows (bounded by
`max_reapproval_rounds`).
Out-of-scope requests short-circuit after the scope gate with a
`rule_fired`-then-`unsupported` pair and never reach the main agent.

## Tests

```sh
uv run pytest tests/workflows/send_invoice/
```

Uses `temporalio.testing.WorkflowEnvironment.start_time_skipping()` plus
`AgentEnvironment` + `TestModel`. `TestModel` is two-shot: the first
response is the scope-gate classification, the second is the main
agent's `InvoiceProposal`. No OpenAI, no MCP subprocess.

Three layers of coverage:

- `test_workflow.py` — orchestration with `COMPASS_POLICY_DISABLE=1`
  (the in-process model never hits MCP, so the proposal-phase rules
  would block every run). Verifies the audit chain
  `intent_classified` → `approval_signal` → `executed` plus decline
  / timeout / duplicate-signal variants.
- `test_workflow_policy.py` — direct activity tests against real
  `compass_test` Postgres, parametrized per BLOCK rule. Covers
  input_validation, pre_action_proposal, pre_execute, audit_validation.
- `test_scope_gate.py` — end-to-end workflow exercise of the
  out-of-scope short-circuit with policy live, asserting the
  `rule_fired` + terminal `unsupported` audit pair.

The live OpenAI + MCP path is exercised by the demo above — not in CI.

## Rules wired in code

1. `execute_send` is a workflow-step activity, never exposed to the
   agent as `activity_as_tool`. The agent's tool surface is read-only
   (the `bank` MCP) — `workflows/send_invoice/workflow.py` top-of-file
   note.
2. `evaluate_policy` distinguishes decision errors (non-retryable)
   from engine / infra errors (retryable). Mapping happens at the
   activity boundary; the engine itself raises `PolicyEngineError` /
   `PolicyInfraError` with a positive `retryable` flag.
3. `audit_log` writes are idempotent: `(workflow_run_id, sequence_no)`
   UNIQUE + `ON CONFLICT DO NOTHING`. `sequence_no` is a deterministic
   monotonic counter in workflow state. `event_kind` is **not** part
   of the key.
4. All MCP tools are read-only (Stage 3's contract). The plugin
   auto-retries the auto-wrapped MCP activities; idempotency is the
   guarantee — re-verify whenever adding tools.
