# `send_invoice` workflow

A durable Temporal workflow that wraps an OpenAI Agents SDK agent
loop, gates on a human approval signal, then writes the invoice and
an audit row. Stage 5 added the policy engine; Stage 6 added the
scope-gate classifier at workflow entry. Full design in:

- [`docs/superpowers/specs/2026-05-27-stage-4-send-invoice-workflow-design.md`](../../docs/superpowers/specs/2026-05-27-stage-4-send-invoice-workflow-design.md)
- [`docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md`](../../docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md)
- [`docs/superpowers/specs/2026-05-27-stage-6-intent-classifier-design.md`](../../docs/superpowers/specs/2026-05-27-stage-6-intent-classifier-design.md)

Build-plan §Stage 4, §Stage 5, §Stage 6 are the contracts.

## Components

| File | What it owns |
| --- | --- |
| `types.py` | Pydantic models (`SendInvoiceRequest`, `InvoiceProposal`, `LineItemProposal`, `ApprovalDecision`, `PolicyDecisionPayload`, `WorkflowResult`). |
| `agents.py` | `build_main_agent(mcp_server)` — single agent, `output_type=InvoiceProposal`, default model `gpt-4.1-mini` (override `OPENAI_MODEL`). |
| `scope_gate.py` | `build_scope_gate_agent()` + `IntentClassification` (Pydantic) — no MCP, no tools. Default model `gpt-4.1-mini` (override `OPENAI_SCOPE_GATE_MODEL`). |
| `context.py` | Pure projections over `RunResult` → policy context dicts; `hash_proposal`. |
| `primitives.py` | App-specific Billing-integrity primitives (`require_amount_source`, `contract_consistency_check`, `prohibit_exceed_contract_cap`, `currency_consistency_check`). |
| `activities.py` | `evaluate_policy` (phase-switched; snapshot + sink in one tx), `execute_send` (idempotent invoice insert), `audit_log` (append-only; `is_terminal_event` runs audit_validation). |
| `workflow.py` | `SendInvoiceWorkflow` — scope-gate → input_validation gate → main agent → pre_action_proposal → wait_condition(approved) → pre_execute → execute_send → audit_log. |
| `worker.py` | Loads `.env.local`, wires `OpenAIAgentsPlugin` + `StatefulMCPServerProvider("bank", …)`, runs the worker. |

## Workflow diagram

Solid arrows are the main control flow inside `SendInvoiceWorkflow.run` —
including the clarification round-trip (agent asks → `clarify` signal →
re-run) and the self-heal loop (a `pre_action_proposal` block feeds the
violations back to the agent for a bounded retry). Dashed arrows show how
the external `approve` and `clarify` signals feed their `wait_condition`s.
Orange = audit-log write; purple = Temporal activity (or auto-activity
inside `Runner.run`); green = policy phase gate; blue = terminal
`WorkflowResult.outcome`.

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
    AuditIntent --> Agent[Main agent loop<br/>stateful MCP: bank<br/>Runner.run, max_turns=20]
    Agent --> HasOutput{proposal?}
    HasOutput -- no --> AuditNoOut[audit: agent_no_output<br/>phase=pre_action_proposal]
    AuditNoOut --> EndRejected1([policy_rejected])
    HasOutput -- yes --> NeedsClar{needs_clarification?}
    NeedsClar -- yes --> AuditClarAsk[audit: needs_clarification]
    AuditClarAsk --> WaitClar[wait_condition<br/>_clarification is not None<br/>optional clarification_timeout_seconds]
    WaitClar -- timeout --> EndNeedsClar([needs_clarification])
    WaitClar -- clarify signal --> AuditClarAns[audit: clarification_answered]
    AuditClarAns --> Agent
    NeedsClar -- no --> ProposalGate{{evaluate_policy<br/>phase=pre_action_proposal}}
    ProposalGate -- block --> SelfHeal{self-heal attempt left?<br/>and PolicyDecisionError}
    SelfHeal -- yes --> AuditHeal[audit: self_heal_retry<br/>feedback to agent]
    AuditHeal --> Agent
    SelfHeal -- no --> AuditPolReject[audit: policy_rejected<br/>rule_ids_fired]
    AuditPolReject --> EndRejected2([policy_rejected])
    ProposalGate -- permit/escalate --> Wait[wait_condition<br/>_approval is not None<br/>timeout=approval_timeout_seconds]
    Wait -- timeout --> AuditTimeout[audit: declined<br/>reason=approval_timeout]
    AuditTimeout --> EndTimeout([timeout])
    Wait -- signal --> AuditSignal[audit: approval_signal]
    AuditSignal --> Approved{approved?}
    Approved -- no --> AuditDeclined[audit: declined]
    AuditDeclined --> EndDeclined([declined])
    Approved -- yes --> PreExecGate{{evaluate_policy<br/>phase=pre_execute}}
    PreExecGate -- block --> AuditDriftReject[audit: policy_rejected<br/>drift/silent-mod]
    AuditDriftReject --> EndRejected3([policy_rejected])
    PreExecGate -- permit/escalate --> Execute[execute_send]
    Execute --> AuditExecuted[audit: executed<br/>is_terminal_event=True<br/>runs audit_validation]
    AuditExecuted --> EndSent([sent + invoice_id])

    Signal[/approve signal/]:::signal -. first wins .-> SetApproval[_approval = decision]
    Signal -. duplicate .-> AuditDup[audit: duplicate_approval_signal]
    SetApproval -. unblocks .-> Wait
    ClarifySignal[/clarify signal/]:::signal -. first wins .-> SetClar[_clarification = response]
    SetClar -. unblocks .-> WaitClar

    classDef audit fill:#fff4e6,stroke:#d9822b,color:#000
    classDef terminal fill:#e6f3ff,stroke:#1f6feb,color:#000
    classDef activity fill:#f0e6ff,stroke:#6f42c1,color:#000
    classDef policy fill:#e8f7e8,stroke:#2da44e,color:#000
    classDef signal fill:#e8f7e8,stroke:#2da44e,color:#000
    class AuditNoClass,AuditUnsupported,AuditIntent,AuditNoOut,AuditClarAsk,AuditClarAns,AuditHeal,AuditPolReject,AuditTimeout,AuditSignal,AuditDeclined,AuditDriftReject,AuditExecuted,AuditDup audit
    class EndUnsupported1,EndUnsupported2,EndRejected1,EndRejected2,EndRejected3,EndNeedsClar,EndTimeout,EndDeclined,EndSent terminal
    class ScopeGate,Agent,Execute activity
    class InputGate,ProposalGate,PreExecGate policy
```

### Policy phases wired in this workflow

| Phase | Where it fires | What it checks |
| --- | --- | --- |
| `input_validation` | After scope-gate `Runner.run`, before main agent | Classifier output (`intent_must_be_send_invoice`). |
| `pre_action_proposal` | After main agent `Runner.run`, before approval wait | Resolution, KYC, billing integrity, evidence citation, amount cap (9 rules). |
| `pre_execute` | After approval signal, before `execute_send` | Proposal-hash drift, policy-hash drift between proposal and execute (2 rules). |
| `audit_validation` | Inside `audit_log` on the terminal `executed` row only | Audit-completeness defect detectors (2 rules). |

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
two `rule_skipped` rows at `pre_execute` →
`executed` (plus two `rule_skipped` rows at `audit_validation`).
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

## Stage 4 interop rules wired in code

From build-plan §Stage 4, marked with comments where they live:

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
