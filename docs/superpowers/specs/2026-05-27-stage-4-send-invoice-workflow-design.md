# Stage 4 — Send Invoice Temporal Workflow (design)

Implements the v0.1 `SendInvoiceWorkflow` per `docs/build-plan.md` §Stage 4: a
durable Temporal workflow that wraps an OpenAI Agents SDK agent loop, gates on
a human approval signal, then writes the invoice and an audit row. Stage 4 is
deliberately scoped *below* the policy engine (Stage 5), the scope gate
(Stage 6), and the UI (Stage 12).

The build-plan section is the contract for this stage. This document records
the choices it leaves open: how approval is signalled in the absence of a UI,
what `execute_send` does without a real billing system, how Langfuse wiring
is gated, and the test layout.

## What ships

```
workflows/
└── send_invoice/
    ├── __init__.py
    ├── types.py           # SendInvoiceRequest, InvoiceProposal, LineItemProposal,
    │                      # ApprovalDecision, WorkflowResult — pydantic models
    ├── agents.py           # main_agent factory (instructions + output_type)
    ├── activities.py       # evaluate_policy (stub), execute_send, audit_log
    ├── workflow.py         # @workflow.defn SendInvoiceWorkflow
    └── worker.py           # entrypoint: runs the worker
scripts/
├── start_workflow.py       # CLI: start a workflow with a user message
└── approve_workflow.py     # CLI: signal approval / decline to a running workflow
```

Stages explicitly **not** in scope: the policy engine and primitive library
(Stage 5), the intent classifier (Stage 6), input/output guardrails (Stage 5),
the Next.js UI (Stage 12).

## Workflow shape

```
SendInvoiceWorkflow.run(req: SendInvoiceRequest) -> WorkflowResult
│
├─ 1. proposal = await Runner.run(
│         main_agent,
│         input=req.user_message,
│         mcp_servers=[openai_agents.workflow.mcp_server("bank")],
│      )
│      # OpenAIAgentsPlugin auto-wraps each LLM call and each MCP tool
│      # call as a Temporal activity. The workflow sees one logical step.
│
├─ 2. decision = await evaluate_policy(proposal)
│      # Stage 4: stub. Returns Decision(permit), writes audit row.
│      # Stage 5 fills in the real engine. On block/escalate, the
│      # activity raises PolicyDecisionError → audit_log(rejected) → END.
│
├─ 3. await workflow.wait_condition(
│         lambda: self._approval is not None,
│         timeout=req.approval_timeout,   # default 1 hour; overridable on the request
│      )
│      # @workflow.signal approve(decision: ApprovalDecision) sets
│      # self._approval. On timeout → audit_log(declined) → END.
│      # On decision.approved == False → audit_log(declined) → END.
│
├─ 4. invoice_id = await execute_send(proposal, approval=self._approval)
│      # Writes a row into invoices + invoice_line_items, idempotency-keyed
│      # on workflow_run_id. INSERT ... ON CONFLICT DO NOTHING makes replay
│      # safe. "Send" itself is a no-op log line — no email/PDF at v0.1.
│
└─ 5. await audit_log(event_kind="executed", payload={...})
      return WorkflowResult(invoice_id=invoice_id, status="sent")
```

### Why a single Temporal workflow

The full action lifecycle — agent reasoning, policy gate, human wait, side
effect, audit — runs as one durable run. A crash mid-loop replays from the
last checkpoint without re-executing completed activities. The alternative
(splitting reasoning from approval from execute into separate workflows
joined by signals) was rejected: it spreads the audit trail across multiple
trace IDs, and the workflow_run_id that audit_log keys on stops being a
single identifier for "this invoice attempt."

### Why the agent loop is one Temporal step

`OpenAIAgentsPlugin` auto-registers every LLM call and every MCP tool call
as a Temporal activity. From `workflow.run`'s perspective, `Runner.run(...)`
is a single `await`. We deliberately do **not** decompose "resolve customer
/ look up rates / draft" into separately-written Temporal activities —
that's LLM-internal reasoning the Agents SDK orchestrates, and writing it
ourselves would re-create what the plugin already provides while leaving us
to maintain trace correlation by hand.

### Why side effects live outside `Runner.run`

`execute_send` is a workflow-step activity, **not** exposed to the agent as
`activity_as_tool`. If the agent could call it directly it would bypass the
human-approval signal between proposal and execute. Build-plan §Stage 4
interop rule 1, recorded as a comment at the top of `activities.py`.

### Alternatives considered (and rejected)

1. **No Temporal — straight Python script.** Cheapest start. Rejected
   because the human approval wait is the central durability requirement:
   a script can't survive the worker crashing during the (potentially
   long) wait between proposal and approval signal. v0.2 disputes will
   need multi-day waits; building the same workflow on Temporal at Stage 4
   means v0.2 is purely additive.
2. **Two workflows joined by signal (proposal-workflow → approval →
   execute-workflow).** Cleaner conceptual separation. Rejected because it
   splits the audit trail across workflow_run_ids and forces a join in the
   audit-log SQL queries that the eval framework (Stages 7, 10) will run.
3. **Side effect as a normal Python coroutine inside `workflow.run`.**
   Rejected: Temporal workflows must be deterministic, and any IO must
   live in an activity. The plan-required `audit_log` write would also
   violate this.
4. **`execute_send` exposed as an agent tool via `activity_as_tool`.** This
   is what the Agents SDK supports out of the box for side-effecting
   activities. Rejected for safety: it would let the agent decide to send
   before the human signal arrives. The agent's tool surface stays
   read-only at v0.1 (the `bank` MCP).

## Agent loop

Single agent at Stage 4 — no sub-agents, no handoffs, no scope-gate
classifier (Stage 6 adds that).

```python
# workflows/send_invoice/agents.py
def build_main_agent() -> Agent[None]:
    return Agent(
        name="send_invoice_main",
        instructions=SEND_INVOICE_INSTRUCTIONS,   # in this file
        output_type=InvoiceProposal,              # pydantic
        model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
        mcp_servers=[openai_agents.workflow.mcp_server("bank")],
    )
```

The agent's job, per build-plan §Architecture "How the agent computes the
invoice amount":

1. Parse the natural-language message ("invoice Acme for last quarter's
   onboarding work").
2. Resolve the customer via `list_customers` / `get_customer`.
3. Pick an amount source in priority order:
   - `get_active_contract(customer_id, today)` returns a contract → its
     terms dominate (`source_type="contract"`).
   - Otherwise rate card × time tracking
     (`get_rate_card` + `list_time_entries`) → `source_type="rate_card"` or
     `"time_tracking"`.
   - Catalog flat rate → `source_type="rate_card"`.
   - User-specified explicit amount → `source_type="user_specified"`, must
     still cite supporting evidence.
4. Return an `InvoiceProposal` whose every line item carries `source_type`,
   `source_refs` (MCP tool result IDs that justified the line), and
   `computation` (human-readable derivation).

The instructions are explicit that the agent **must not** invent customers,
amounts, or rate-card entries — every line item must trace back to MCP tool
output. (Stage 5 turns this into enforceable policy via
`require_evidence_citation` and `require_amount_source`; at Stage 4 it's
prompt discipline only.)

### Why a single agent (not multi-agent)

Build-plan §Architecture is explicit: one main agent at v0.1, with the
scope-gate as a separate `Runner.run(...)` added in Stage 6. Sub-agents
or handoffs would add coordination cost without solving a current
problem. The Agents SDK supports adding them later non-breakingly.

### Why structured output via `output_type=InvoiceProposal`

The Pydantic `InvoiceProposal` is the workflow's typed contract for
"agent done." It's what `evaluate_policy` receives (Stage 5), what the
approval UI will render (Stage 12), and what `execute_send` writes. The
SDK enforces strict JSON-schema structured output against this model,
which gives us "agent failed to produce a valid proposal" as a distinct,
typed error path — distinct from "agent produced a proposal that policy
rejected."

## Model wiring (OpenAI Agents SDK + OpenAI API)

Direct OpenAI API. No OpenRouter, no custom base URL. The Agents SDK
defaults to the OpenAI Responses API, which is what we use.

- `OPENAI_API_KEY` (required) — read from environment at worker startup.
- `OPENAI_MODEL` (optional) — defaults to `gpt-4.1-mini`. Override for
  cross-model eval comparisons (build-plan §Stack notes the model is
  swappable per run).

`.env.local` at the repo root is the canonical place for these (already in
`.gitignore`). `worker.py` loads it via `python-dotenv` so `uv run python
-m workflows.send_invoice.worker` "just works."

## Activities

Three activities, all in `workflows/send_invoice/activities.py`. Default
Temporal retry policy on all three; specific overrides noted.

### `evaluate_policy(proposal: InvoiceProposal) -> Decision`  (Stage 4 stub)

At Stage 4 this is a stub that:
- Writes one audit row with `event_kind="proposal"`, `decision="permit"`,
  and `payload={"proposal": proposal.model_dump()}`.
- Returns `Decision(permit=True, violations=[])`.
- Carries a `TODO(stage-5)` comment pointing at the build-plan section.

The interop rule from build-plan §Stage 4 #2 is wired now even though
there's nothing to throw yet: the activity signature is shaped so it can
raise `PolicyDecisionError` (mapped to `ApplicationError(non_retryable=True)`
at the call site) when Stage 5 fills in the body. `PolicyEngineError` and
`PolicyInfraError` paths are retryable.

### `execute_send(proposal: InvoiceProposal, *, approval: ApprovalDecision, idempotency_key: str) -> str`

Writes a row into `invoices` and one row per line item into
`invoice_line_items`, using the workflow_run_id as the idempotency key.
The invoice's `id` is derived from the idempotency key (`f"inv-{workflow_run_id}"`)
so two retries of the same activity produce the same primary key, and
`INSERT ... ON CONFLICT DO NOTHING` makes the second write a no-op. Returns
the invoice id.

The "send" verb is a no-op log line at v0.1 — there's no email service, no
PDF generation, no payment processor. The persisted DB row *is* the
artifact; downstream eval queries can verify the right row was written.

Retry policy: default, but the idempotency key is the durability guarantee.

### `audit_log(event_kind, payload, *, decision=None, actor=None, rule_id=None) -> None`

Appends to the `audit_log` table. `sequence_no` is a deterministic
monotonic counter held in the workflow state (incremented per emit;
replay-stable because the workflow is replay-deterministic). The
`UNIQUE (workflow_run_id, sequence_no)` constraint + `ON CONFLICT DO
NOTHING` make activity retries idempotent. Build-plan §Stage 4 interop
rule 3.

At Stage 4 `policy_hash` is a placeholder constant (`"stage-4-stub"`).
Stage 5 introduces real policy hashing.

## Approval signal

```python
@workflow.signal
async def approve(self, decision: ApprovalDecision) -> None:
    if self._approval is not None:
        # First signal wins — late duplicates are dropped (logged) rather
        # than overriding. Audit row captures the duplicate for visibility.
        await workflow.execute_activity(
            audit_log, args=[...],   # event_kind="duplicate_approval_signal"
        )
        return
    self._approval = decision
```

`ApprovalDecision` is a pydantic model:

```python
class ApprovalDecision(BaseModel):
    approved: bool
    approver_id: str
    notes: str | None = None
    # actor metadata captured at signal time so audit row reflects who
    # actually pushed the button, not "the workflow approved itself"
```

CLI for the demo:

```bash
# start
uv run python -m scripts.start_workflow \
    --message "Invoice Acme for last quarter's onboarding work"
# prints WORKFLOW_ID

# approve
uv run python -m scripts.approve_workflow WORKFLOW_ID --approve \
    --approver felixglush

# or decline
uv run python -m scripts.approve_workflow WORKFLOW_ID --decline \
    --approver felixglush --notes "scope mismatch"
```

The CLI wraps `client.get_workflow_handle(id).signal(SendInvoiceWorkflow.approve, ApprovalDecision(...))`.

### Why first-signal-wins

Trying to handle "approver changes their mind" or "multiple approvers
race" would put the workflow logic in charge of approval policy, which
belongs in Stage 5 (`dual_control_above_threshold` and friends). At
Stage 4 the simplest behavior that's not silently wrong is to log
duplicates and ignore them.

## Worker

`workflows/send_invoice/worker.py` registers:

- `SendInvoiceWorkflow` (the `@workflow.defn` class).
- `evaluate_policy`, `execute_send`, `audit_log` activities.
- `OpenAIAgentsPlugin` on the Temporal client, with
  `ModelActivityParameters(start_to_close_timeout=timedelta(seconds=60))`.
- A `StatefulMCPServerProvider(lambda: MCPServerStdio(name="bank",
  params=StdioServerParameters(command="uv", args=["run", "python", "-m",
  "mcp_bank"], env={"COMPASS_PG_DSN": ...})), max_idle_connections=4)`.

Why stateful: stateless spawns a fresh subprocess per MCP call, which
compounded across the eval corpus produces thousands of process spawns
per run (build-plan §Stage 4). Stateful keeps a pool of MCP subprocesses
warm; on connection loss the activity raises `ApplicationError`, which
gets one retry.

### Why `OpenAIAgentsPlugin` and not bare `Runner.run`

The plugin propagates the OpenAI Agents SDK trace context across the
Temporal activity boundary. Without it, the LLM-call activities and the
workflow-step activities appear in two disconnected trace trees and
"what tool calls did this proposal come from?" requires a manual join.

## Langfuse / OpenInference tracing

Wired but **gated**:

```python
# workflows/send_invoice/worker.py (setup)
if os.environ.get("LANGFUSE_OTLP_ENDPOINT"):
    from openinference.instrumentation.openai_agents import (
        OpenAIAgentsInstrumentor,
    )
    OpenAIAgentsInstrumentor().instrument()
    # exporter wired to LANGFUSE_OTLP_ENDPOINT
else:
    # local-console exporter — useful for development without Langfuse running
    ...
```

Build-plan's acceptance criterion is "confirm in the Langfuse UI that
activity spans and LLM spans appear under the correct workflow trace."
That confirmation is part of Stage 4 *if Langfuse is running*; if not,
the wiring is verifiable by inspecting the console exporter output.
Adding the Docker sidecar for Langfuse is a one-line change to
`docker-compose.yml` deferred to whenever the user wants it.

The workflow exports its `workflow.info().workflow_id` as the trace ID so
agent spans and workflow spans link under one root in either exporter.

## Local Temporal

`temporal server start-dev` (in-memory backend) is the v0.1 default; fine
for the minutes-long send-invoice runs. The build-plan calls out
`temporal server start-dev --db-filename ./temporal.db` for v0.2 disputes
that need multi-day persistence — that's a runtime flag, not a code
change, so Stage 4 doesn't pre-commit to either.

A short README section under `workflows/send_invoice/README.md` documents
the run sequence:

```
# terminal 1: postgres
docker compose up -d

# terminal 2: temporal
temporal server start-dev

# terminal 3: worker
uv run python -m workflows.send_invoice.worker

# terminal 4: drive the demo
uv run python -m scripts.start_workflow --message "..."
uv run python -m scripts.approve_workflow <id> --approve --approver felixglush
```

## Tests

`tests/workflows/send_invoice/` mirrors the package layout.

### `test_workflow.py` — workflow test against a fake model

`temporalio.testing.WorkflowEnvironment.start_time_skipping()` for an
in-memory Temporal. The agent is wired with a fake model that returns a
canned `InvoiceProposal` directly — no network, no MCP subprocess. This
gives deterministic CI coverage of:

- Happy path: proposal → policy (stub permit) → approve signal → execute
  → audit. Assertion: invoice + line items exist in the test DB with the
  right values; audit_log rows exist with the right `event_kind`s in the
  right order.
- Decline path: approve signal carries `approved=False`. Assertion:
  audit_log shows `declined`; no row in `invoices`.
- Timeout path: never signal; advance time past timeout. Assertion: same
  as decline.
- Idempotency: run `execute_send` activity twice with the same key.
  Assertion: only one row in `invoices`.

### `test_signal.py` — approval-signal edge cases

Duplicate signal, signal before agent finished, malformed
`ApprovalDecision` payload.

### Manual smoke test (documented, not in pytest)

A `make demo` (or just a documented command sequence) that runs the worker
against the real OpenAI API and walks through one invoice. Required at
least once before declaring the stage done; not part of CI because the
free-model latency + cost makes it a poor CI fit.

### Why not live model in CI

Free-model rate limits + flakiness would dominate CI runtimes. The
WorkflowEnvironment + fake model is sufficient for "did the orchestration
code break"; the manual smoke is sufficient for "is the OpenAI API + MCP
wiring actually working."

## Dependencies (pinned per CLAUDE.md rule 1)

Newly added to `pyproject.toml`, latest stable, exact-pinned. Versions
locked to the current `==` per `CLAUDE.md`; resolved + committed to
`uv.lock`.

- `temporalio` — Temporal Python SDK.
- `openai-agents` — OpenAI Agents SDK.
- `openinference-instrumentation-openai-agents` — Langfuse-compatible
  tracing processor (added now, gated at runtime).
- `python-dotenv` — `.env.local` loading.
- `openai` — pulled in transitively by `openai-agents`; pinned explicitly
  so model upgrades are deliberate.

Exact versions are resolved + locked at implementation time.

## Acceptance criteria

1. `uv run pytest tests/workflows/send_invoice/` passes.
2. `make demo` (or its documented equivalent) drives one invoice end to
   end against the real OpenAI API, producing an `invoices` row and a
   complete `audit_log` trail (`proposal` → `approval_signal` →
   `executed`).
3. `OPENAI_API_KEY` is read from `.env.local`; no hardcoded credentials.
4. CI passes the dependency-direction check (`scripts/check_dependency_direction.sh`)
   — `workflows/` imports from `mcp_bank/` and `db/`, never the reverse,
   and `compass/` is not yet involved.
5. `mcp_bank/README.md`'s idempotency contract holds — the only writes
   inside the agent loop go through read-only MCP tools.
