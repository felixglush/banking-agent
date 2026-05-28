# Compass — Build Plan

**Note**: this is guidance and details are subject to change during implementation.

A reusable evaluation and policy framework for agentic financial workflows. Built around two workflow demonstrations — *send an invoice* and *dispute investigation* — chosen because they differ across duration, decision style, policy surface, and ground-truth structure. Standard adversarial testing is delegated to Promptfoo; the project's eval focus is workflow-trace-aware evaluation, counterfactual perturbation analysis, and policy-coverage measurement.

---

## Thesis

Agentic banking products are arriving: natural-language financial work end-to-end, with every action reviewed and approved by the customer. That class of product needs infrastructure underneath — policy governance over what actions agents can propose, evaluation infrastructure that catches regressions before they reach customers, and an architecture where these primitives extend cleanly to new action types. This project is a reference implementation of that infrastructure.

The architecture is workflow-runtime-aware: evaluation operates on Temporal workflow traces (not just LLM call traces), policies enforce inline at workflow phase boundaries (not as model output filters), and the primitives generalize across action types.

---

## Positioning

**Versus building an agentic banking product.** The deliverable is the evaluation and policy framework that such a product would need before launch. The workflows are demonstrations.


1. A typed Python rule library (`Rule(id, phase, predicate, severity, ...)` dataclasses) that compiles to OpenAI Agents SDK guardrails for in-agent phases and runs as Temporal activity bodies for workflow-level phases. Same vocabulary in both places, same stable IDs, same trace-event emission.
2. Implementations for the three workflow-level phases (`pre_action_proposal`, `pre_execute`, `audit_validation`) that the OpenAI Agents SDK doesn't have — these run as Temporal activities.
3. A small library of framework-core primitives with stable IDs (so policy-compliance and coverage can be measured by querying structured trace events).
4. A counterfactual perturbation generator.
5. A `WorkflowRunner` protocol + `TemporalWorkflowRunner` so the eval harness can drive workflows.

Everything else is delegated: Langfuse for traces / costs / latency / LLM-as-judge; Promptfoo for adversarial generation; OpenAI Agents SDK for everything inside the agent loop; Pydantic for structured output. **The policy library has no value for one workflow** — its value emerges at ≥2 workflows that share a vocabulary plus an eval that needs stable rule IDs. v0.2 is what validates the abstraction. A YAML DSL was considered and rejected at this scope — see [§Design rationale](#design-rationale) for the rubric. YAML may be added as a second front-end (additive, non-breaking) when a third workflow lands, an external adopter requires it, or multi-tenant / non-engineer authoring becomes scope.

**Counterfactual perturbation eval.** Adversarial tests check whether agents can be *tricked*; counterfactual tests check whether agents are *stable under harmless input variation*, measured relative to the LLM's own sampling-noise floor.

**Policy-coverage report.** What fraction of the policy library's rule IDs is exercised by the eval corpus? Implemented as a ~50-LOC SQL query over structured trace events. CI-gated for any `Rule(must_be_covered=True)`.

**Human-in-the-loop safety posture**: every action is reviewed and approved by the customer. Policy and eval primitives are designed around this. Confirmation surfaces what the action will do, what could go wrong, and whether policy permits it. Policy drift across approval waits is escalated, never silently grandfathered.

---

## Stack


| Concern | Choice |
|---|---|
| Backend / agent / gateway / MCP / policy engine | Python |
| Agent framework | OpenAI Agents SDK (via [`temporalio.contrib.openai_agents`](https://github.com/temporalio/sdk-python/blob/main/temporalio/contrib/openai_agents/README.md)) |
| Frontend | Next.js + Tailwind |
| Synthetic data generator | Python |
| Database | Postgres |
| Workflow orchestration | Temporal (Python SDK) |
| LLM observability | Langfuse |
| Adversarial testing | Promptfoo |
| LLM provider | OpenAI via OpenRouter (model swappable per run for cross-model eval) |

The demo runs locally with `temporal server start-dev` and Docker for Langfuse.


---

## The Two Workflows

This project demonstrates two agentic-banking workflows.

### Workflow 1 — Send an Invoice (v0.1)

A linear approval-gated action. User intent → resolve customer → resolve scope → draft invoice → policy check → user approval → execute → audit. Minutes to hours of execution (mostly the human approval wait). Strong policy surface (resolution gates, amount thresholds, KYC, citation discipline, prompt injection in freeform fields). Tight functional accuracy with clear ground truth (right customer, right amount, right line items).

This is the v0.1 demonstration workflow — it ships end-to-end with the full policy and eval infrastructure built around it.

### Workflow 2 — Dispute Investigation (v0.2)

A long-running case workflow. Customer disputes a transaction → agent investigates by querying multiple sources (the original transaction, related transactions for pattern detection, customer history, merchant signals) → drafts a case summary → routes to human reviewer → human decides, possibly requests more info, possibly multi-day back-and-forth → final decision triggers refund/reject + notifications + audit log.

This is the v0.2 workflow. Adding it tests whether the framework generalizes. Success criteria:

- Zero new policy primitive *types* required (only new compositions)
- No changes to the eval engine (only new test cases)
- All workflow infrastructure (Temporal patterns, Langfuse integration, gateway, audit log) reused without modification

If those criteria hold, the reusability claim is verified. If not, v0.2 surfaces which abstractions are leaking.

### Why these two


| | Send Invoice | Dispute Investigation |
|---|---|---|
| Duration | Minutes-hours (human approval wait) | Days (multi-actor case handling) |
| Decision style | Threshold-gated approval | Evidence-weighted case decision |
| Policy surface | Resolution + amount + KYC + citation | Evidence weighting + dual review + escalation tiers |
| External calls | Customer lookup, invoice send | Multi-source investigation, processor APIs, merchant lookup |
| Failure modes | Wrong customer, wrong amount, injection | Missed pattern, premature decision, evidence misweighting |
| Eval ground truth | Field-level (right customer, right amount) | Decision-level (right outcome, defensible rationale) |
| LLM judgment depth | Single-step (resolve, draft) | Multi-step (investigate, summarize, recommend) |

---

## v0.1 Scope (Send Invoice)

**In scope**
- Synthetic Account 1 data — banking + invoicing + customers + rate cards + contracts (SOW / MSA / retainer)
- One MCP server: `bank` (read-only over banking and invoicing data)
- `SendInvoiceWorkflow` as a Temporal workflow with the OpenAI Agents SDK running inside `Runner.run()` (auto-activities via `OpenAIAgentsPlugin`) plus three hand-written side-effect activities (`evaluate_policy`, `execute_send`, `audit_log`)
- Langfuse for LLM/agent traces (via `openinference-instrumentation-openai-agents` + OTLP exporter — the Temporal `OpenAIAgentsPlugin` propagates trace context across the activity boundary but does *not* itself ship a Langfuse integration)
- Policy engine + primitive library — workflow-agnostic primitives that compile to OpenAI Agents SDK guardrails (in-agent phases) and Temporal activity bodies (workflow-level phases). Rules are typed Python (`Rule(...)` dataclasses in `policies/<workflow>.py`), not YAML. Five phases: `input_validation`, `output_validation`, `pre_action_proposal`, `pre_execute`, `audit_validation` — all wired
- Intent classifier / scope gate — runs as a `Runner.run(scope_gate_agent, ...)` at the workflow entry; out-of-scope requests rejected via an `input_validation`-phase policy rule on the gate's structured output; original message + classifier output + confidence logged to the audit log for product-iteration visibility
- Evaluation harness — three custom suites and three delegated:
  1. *Functional accuracy* (custom; against ground truth, field-by-field with tolerance bands)
  2. *Counterfactual perturbation* (custom; the one genuinely novel piece — see §Eval Framework)
  3. *Policy-coverage report* (custom; ~50 LOC SQL over the trace event store — see below)
  4. *Adversarial robustness* — delegated to Promptfoo
  5. *Policy compliance* — not a subsystem; assertions on Langfuse traces of structured policy-fire events (see §Eval Framework)
  6. *Cost/latency* — Langfuse native
- Next.js UI: chat, approval queue, policy viewer, eval dashboard, audit log; Langfuse + Temporal UI for raw trace/workflow exploration
- Three writeups: counterfactual perturbation methodology, policy vocabulary + relationship to OpenAI Agents SDK hooks, workflow-aware eval architecture

**Out of scope**
- Dispute investigation workflow (v0.2)
- Additional MCPs beyond `bank`
- Multi-MCP orchestration
- Contract intake (PDF → structured) — contracts arrive pre-structured. Extraction is a separate upstream concern with its own evals; bundling it would let extraction noise swamp the billing-reasoning signal we're trying to measure. The handoff contract is pinned: a contract row must satisfy the `Contract` Pydantic schema in `compass/types/contract.py` (effective/expiry dates, billing structure, rate overrides, caps, currency, optional `source_doc_ref`); anything that fails schema validation is rejected at load time and never reaches the policy engine. Adopters building extraction at v0.3 target that schema; freshness is governed by `require_field_recency`
- Rule composition operators (`and` / `or` / `not`) in the policy library — flat list of single-predicate rules at v0.1. Compound conditions become new Python primitives (registered via `@compass.policy.primitive`), not nested rule structures. Composition added back only when a real compositional pressure surfaces
- YAML policy file format — typed Python (`policies/send_invoice.py` exporting `RULES: list[Rule]`) at v0.1 (see [§Design rationale](#design-rationale) for why). YAML loader is additive when adopters demand it
- Knowledge graph layer (mentioned in design docs as a v1.0+ direction)
- Real bank sandbox integration (v0.3+)
- Multi-tenant

---

## v0.2 Scope (Dispute Investigation, Framework Reuse Validation)

**Added**

- `DisputeInvestigationWorkflow` as a Temporal workflow with new activities for case handling
- New `policies/dispute_investigation.py` (typed Python `RULES: list[Rule]`) composing existing primitives
- New test corpora (functional, perturbation) for the dispute workflow
- Trace coherence eval (new custom suite — multi-step reasoning consistency, motivated by dispute's long traces)
- Promptfoo redteam config for dispute-specific attacks
- Updated UI to handle case workflow surfaces (case view, evidence presentation, multi-actor approval threads)
- Reusability writeup: what was new, what was reused, where the abstraction held

**Success criteria for v0.2 specifically**
- ≤2 new policy primitive types (target: 0)
- 100% of v0.1 eval suite categories applicable to v0.2
- ≥80% **reuse ratio** in the dispute policy, where the metric is pinned as: `reuse_ratio = primitives_reused_from_v01 / total_primitive_uses_in_dispute_policy`. Example: dispute policy invokes 10 primitives total — 8 of them are v0.1 framework-core/app primitives, 2 are new → 80%. Critically, this is *not* "% of v0.1 primitives used by v0.2" (that metric punishes v0.1 for being comprehensive); it's "what fraction of v0.2's policy load comes from already-built bricks." The v0.2 writeup also reports **unused v0.1 primitives** as a separate line item — those are the leading indicator that v0.1 over-built the vocabulary
- A primitive counts as "leaked" (and is named in the writeup) if implementing the dispute policy required any of: (a) registering an app-specific primitive whose logic is fundamentally generic — meaning it should have been framework-core; (b) bypassing the public API to reach into `compass.*` internals; (c) modifying `compass/*` code rather than extending via the documented extension surface
- **Dispute investigation is built using only the documented public API of the `compass` package.** The dependency-direction CI check passes throughout v0.2. Any time the dispute workflow would need to reach into Compass internals, that's logged as a public-API gap and addressed by extending the public API — not by giving the workflow privileged access.

If those numbers come in much worse, the framework abstraction needs revision before any further workflows are added. Public-API gaps surfaced during v0.2 are explicitly named in the reusability writeup.

---

## Architecture

```
                 ┌────────────────────────────┐
                 │  Next.js UI                │  chat, approval queue,
                 └──────────────┬─────────────┘  policy viewer, eval
                                │                dashboard, audit log
                                │  (starts workflows, signals
                                │   approvals back to Temporal)
                                ▼
            ┌───────────────────────────────────────┐
            │  Temporal Workflows                   │
            │    SendInvoiceWorkflow      (v0.1)    │
            │    DisputeInvestigationWorkflow (v0.2)│
            │  durable, resumable, fully traced     │
            │                                       │
            │  LLM reasoning runs inside            │
            │   Runner.run(agent, ...) — auto       │
            │   activities via OpenAIAgentsPlugin   │
            │                                       │
            │  Hand-written activities per workflow:│
            │   evaluate_policy, wait_condition     │
            │   (signal), execute_side_effect,      │
            │   audit_log                           │
            └────────────┬──────────────┬───────────┘
                         │              │
                         ▼              ▼
              ┌──────────────────┐   ┌───────────────┐
              │  OpenAI Agents   │   │ Policy Engine │
              │  SDK + Langfuse  │   │ (compass.     │
              │  via OTLP        │   │  policy)      │
              │  (trace context  │   └───────────────┘
              │   bridged by     │
              │   plugin)        │
              └────────┬─────────┘
                       │
              ┌────────▼─────────┐
              │  bank MCP        │  domain-specific tools;
              └────────┬─────────┘  parameterized SQL inside
                       │            handlers (no SQL exposed)
              ┌────────▼─────────┐
              │  Postgres        │  bank data + audit log
              │  (Docker sidecar)│  + eval-run history
              └────────▲─────────┘
                       │ idempotent bulk load
              ┌────────┴─────────┐
              │  JSONL (canonical│  deterministic from seed;
              │   regeneratable) │  diffable in git
              └──────────────────┘

(observability — Docker sidecar)
┌──────────────────────────────────────────┐
│  Langfuse — local instance               │
│  - LLM traces & spans                    │
│  - Tool invocation events                │
│  - Prompt versioning & A/B comparison    │
│  - Datasets + LLM-as-judge evals         │
└──────────────────────────────────────────┘

(evaluation framework)
┌──────────────────────────────────────────┐
│  Eval Harness                            │
│  Custom code:                            │
│  - Functional accuracy (against ground   │
│    truth, field-by-field w/ tolerance)   │
│  - Counterfactual perturbation generator │
│    + stability/sensitivity reports       │
│  - Policy-coverage report                │
│    (SQL over policy-fire trace events)   │
│  Delegated:                              │
│  - Adversarial → Promptfoo               │
│  - Policy compliance → trace assertions  │
│    over Langfuse-recorded policy-fire    │
│    events (no Compass subsystem)         │
│  - Cost/latency → Langfuse native        │
│  - Trace coherence (v0.2) → Langfuse     │
│    LLM-as-judge w/ pinned model +        │
│    self-consistency                      │
└──────────────────────────────────────────┘
```

### Workflow DAG — where each policy phase fires

`SendInvoiceWorkflow.run` is shown below. Note: the workflow uses Temporal's `openai_agents` plugin, so the LLM reasoning steps that look like distinct stages (classify, parse, resolve, draft) actually live **inside** `Runner.run(agent, ...)` — they're not separate Temporal activities we write. Each LLM call becomes a Temporal activity automatically via the plugin. The activities we *do* write are the side-effect boundaries (`evaluate_policy`, `execute_send`, `audit_log`).

```
SendInvoiceWorkflow DAG (v0.1)

  ┌───────────────────┐
  │  User message     │
  └─────────┬─────────┘
            ▼
  ┌─────────────────────────┐
  │ Runner.run(             │ ────► [input_validation]
  │   scope_gate_agent,     │       scope-gate rules evaluated on
  │   input=user_message)   │       Pydantic output (in/out-of-scope)
  └─────────┬───────────────┘
            │
       in-scope?
       ┌────┴────┐
       │ yes     │ no ──► audit_log (unsupported) ──► END
       ▼
  ┌─────────────────────────┐
  │ Runner.run(             │
  │   main_agent,           │  Inside: model calls + MCP tool calls
  │   input=user_message,   │  (list_customers, get_contract, etc.)
  │   mcp_servers=[bank])   │  all run as auto-registered Temporal
  │                         │  activities via OpenAIAgentsPlugin
  │ returns InvoiceProposal │
  └─────────┬───────────────┘
            ▼ [output_validation]
       (Pydantic structured output is the contract;
        guardrails on the agent reject malformed proposals)
            ▼
  ┌─────────────────────────┐
  │ evaluate_policy         │ ────► [pre_action_proposal]
  │   activity              │       full DSL evaluation
  │ (compass.policy)        │       (including Billing integrity)
  └─────────┬───────────────┘
            │
       permit?
       ┌────┴────┐
       │ yes     │ block/escalate ──► audit_log (rejected) ──► END
       ▼
  ┌─────────────────────────┐
  │ await workflow.wait_    │ Temporal signal; minutes–hours
  │  condition(approved)    │ (human reviews in UI, signals back)
  └─────────┬───────────────┘
            │
       approved?
       ┌────┴────┐
       │ yes     │ no/timeout ──► audit_log (declined) ──► END
       ▼
  ┌─────────────────────────┐
  │ execute_send activity   │ ────► [pre_execute]
  │ (idempotent;            │       final gate before side effect
  │  activity_as_tool ok    │       (catches modifications between
  │  if invoked from agent) │        approval and execute)
  └─────────┬───────────────┘
            ▼
  ┌─────────────────────────┐
  │ audit_log activity      │ ────► [audit_validation]
  │ (success)               │       audit completeness check
  └─────────┬───────────────┘
            ▼
           END
```

Things the DAG makes visible:

- **The agent loop is one Temporal step from the workflow's perspective.** Inside `Runner.run`, model calls and MCP tool calls are auto-activities thanks to the `OpenAIAgentsPlugin`. We don't decompose "classify / parse / resolve / draft" into separately-written Temporal activities — that's LLM-internal reasoning the OpenAI Agents SDK orchestrates.
- **`input_validation` and `output_validation` map to OpenAI Agents SDK guardrails.** The scope-gate rules and the proposal-validation rules are wired in as agent `input_guardrails` / `output_guardrails` (or evaluated against the Pydantic structured output post-Runner). They're still Compass policy rules — just invoked at the agent boundary, not inside the workflow code.
- **Five phases total, all wired.** Per-tool-call governance (`pre_tool_call` / `post_tool_call`) was considered and dropped — the full tool-call history is in scope at `pre_action_proposal`, so the failure modes that matter (wrong customer resolved, wrong rate looked up) are caught post-agent, and pattern violations ("agent investigated 50 transactions when it should have stayed scoped to 5") *require* the full history rather than per-call gating. Adding them back later is non-breaking (new `Phase` enum values + a `compile_tool_guardrails` method).
- **Three short-circuit paths to audit-then-END**: unsupported (scope-gate reject), policy block, human decline. All three go through `audit_validation`; they share an audit sink.
- **`pre_execute` violation = bug.** By that point the proposal has been policy-approved and human-approved; a `pre_execute` rule firing means something changed between approval and execute — exactly what `prohibit_silent_modification_after_confirmation` (proposal drift) and `prohibit_policy_drift_after_confirmation` (policy-version drift; see §Policy Engine + Primitive Library) exist to catch.

### How the agent computes the invoice amount

The MCP doesn't decide the amount; it exposes the facts. The reasoning lives in the `draftInvoice` activity. Four possible amount-source types, in priority order:

1. **Contract-derived.** If `get_active_contract(customer_id, today)` returns an active contract, its terms dominate: flat-fee SOW milestones, monthly retainer, T&M with contract-negotiated rates (which may differ from the public rate card), monthly hour caps, etc.
2. **Rate card × time tracking.** When no active contract overrides, use `get_rate_card(role_or_service)` × `list_time_entries(customer_id, date_range)`. Standard hourly billing against logged work.
3. **Rate card flat amount.** Catalog services with a flat list price (e.g., "Q1 onboarding package: $7,200").
4. **User-specified.** The user gave an explicit amount in the request. Permitted, but the proposal must still cite supporting evidence from (1)–(3), and any discrepancy is surfaced to the human reviewer rather than silently accepted.

Each line item in the structured proposal carries:

- `source_type: contract | rate_card | time_tracking | user_specified`
- `source_refs: [MCP tool result IDs that justify this line]`
- `computation: human-readable derivation (e.g., "24h × $300/hr per contract §3.2")`

These fields are what `require_amount_source`, `contract_consistency_check`, and `prohibit_exceed_contract_cap` evaluate at `pre_action_proposal`. They are also what the approval UI surfaces so the human reviewer sees *why* the agent landed on the number, not just the number itself.

Counterfactual perturbation has a billing-reasoning axis on top of the surface-form ones: the same 24h of Q1 onboarding logged as `1×24h` vs. `8×3h` vs. `24×1h` should all yield identical invoice totals; flipping a contract clause that "list_rates_apply: false" should change the chosen rate; paraphrasing a contract scope sentence shouldn't.

Two key architectural commitments. First: **the agent cannot produce side effects directly.** It produces action proposals that pass through the policy engine, then through an approval activity (which surfaces them to the user and blocks until a signal is received), and only then to the execute activity. Second: **the entire action lifecycle runs as a single durable Temporal workflow.** Each step is an idempotent activity with retries; the workflow is resumable and replayable across crashes, restarts, and long approval waits. LLM-and-tool-use happens inside `Runner.run()` as auto-activities via `OpenAIAgentsPlugin`; OTel spans from `openinference-instrumentation-openai-agents` are exported to Langfuse via OTLP, with workflow ID propagated as trace ID and the OpenAI Agents SDK trace context preserved across the Temporal activity boundary by the plugin.

---

## Synthetic Account 1

A Series A AI-native B2B SaaS startup, a representative small-business banking customer.

- 24 months of operating history; seed at month 0; Series A at month 18
- 22 employees today
- ~$4M ARR, ~120 paying customers, ~$33k average ACV
- ~120 active customer records with names, addresses, KYC status, payment history, default terms
- ~280 past invoices with line items, payment status, dispute flags
- ~40 historical disputed transactions (for v0.2) with resolution outcomes labeled as ground truth — large enough to populate a 30-case train/holdout corpus (see §Eval Framework) and leave a margin for ambiguity-rich edge cases
- Rate cards for Synthetic Account 1's services (list rates by role / service tier)
- ~60 customer contracts (MSA / SOW / order form) — most active, some expired or amended. Mix of billing structures: flat-fee SOW, monthly retainer, T&M with negotiated rates, T&M with monthly hour caps. Some contracts override the rate card; some are silent and defer to it; some specify milestone-tied payment. **Stored as pre-derived, already-structured terms** (rate overrides, caps, billing structure, effective/expiry dates, scope summary, optional `source_doc_ref` for the original PDF path) — PDF parsing is a separate upstream concern, intentionally out of scope (see below).
- An ambiguity-rich subset: customers with similar names, multiple billing contacts, restricted recipients, edge-case rate card entries
- Anomaly-rich subset for dispute scenarios: chargebacks, duplicate charges, fraud signals, merchant disputes

Synthetic Account 1's profile lives in `synthetic_account_1/README.md` and is the source of truth from which all config files derive.

---

## Data Simulation

Procedural day-by-day simulation, extended to produce the customer/invoicing/dispute surfaces.

```
synthetic_account_1/
├── README.md
├── config/
│   ├── company.yaml
│   ├── vendors.yaml
│   ├── customers.yaml
│   ├── rate_cards.yaml
│   ├── contracts.yaml            # SOW / MSA / retainer templates per customer cohort
│   ├── adversarial.yaml          # ambiguity-rich edge cases
│   └── disputes.yaml             # seeded dispute scenarios for v0.2
├── simulate.py
├── verify.py
├── load_to_postgres.py            # idempotent loader; truncates + reloads from JSONL into the shared schema
├── generated/
│   ├── bank/
│   │   ├── accounts.json
│   │   ├── transactions.jsonl
│   │   ├── customers.jsonl
│   │   ├── invoices.jsonl
│   │   ├── invoice_line_items.jsonl
│   │   └── disputes.jsonl        # v0.2
│   ├── account_internal/
│   │   ├── time_tracking.jsonl
│   │   ├── projects.jsonl
│   │   ├── rate_card_lookup.jsonl
│   │   └── contracts.jsonl       # one row per contract; embedded terms (rates, caps, milestones, currency, payment terms, effective/expiry dates)
└── ground_truth/
    ├── train/                                # used for prompt/rule iteration
    │   ├── invoice_resolution_labels.jsonl
    │   ├── scope_gate_labels.jsonl
    │   ├── policy_compliance_labels.jsonl
    │   ├── perturbation_stability_labels.jsonl
    │   └── dispute_outcome_labels.jsonl      # v0.2
    └── holdout/                              # locked; only read for final reported numbers
        ├── invoice_resolution_labels.jsonl
        ├── scope_gate_labels.jsonl
        ├── policy_compliance_labels.jsonl
        ├── perturbation_stability_labels.jsonl
        └── dispute_outcome_labels.jsonl      # v0.2
```

Statistical properties: realistic vendor distributions, customer cohorts with log-normal ACV, semi-monthly payroll, log-normal card spend, realistic payment timing, historical disputes with varied outcomes (refunded, denied, partial), an ambiguity-rich customer subset for counterfactual perturbation testing.

**Pure procedural generation, no LLM in the loop** — every field (memos, descriptions, contract scope sentences) is drawn from templates + seeded RNG. This is what lets a single seed regenerate the world byte-identically. (Counterfactual perturbations *do* use an LLM, but that's at eval time, on top of the fixed seed dataset — not during generation.)

`verify.py` runs sanity checks against the JSONL. `load_to_postgres.py` truncates and loads in batches the JSONL into Postgres — **scoped to the bank-data tables only** (`customers`, `invoices`, `invoice_line_items`, `transactions`, `accounts`, `rate_cards`, `time_entries`, `contracts`, `disputes`). It does NOT touch `audit_log` or `eval_runs` — those are runtime-written and survive across data reloads. The validation criterion "identical DB state on re-run" applies only to bank-data tables. JSONL is the canonical artifact (version-controlled, diffable, deterministic); Postgres is the queryable runtime surface the MCP server reads from.

The Postgres instance also stores the workflow audit log (including unsupported-request entries from the scope gate) and eval-run history, so a single Docker `postgres` sidecar serves three jobs: bank data, audit, eval history.

---

## Database

Postgres DDL lives at the top level in `db/schema.sql` — *not* inside `synthetic_account_1/`. The schema is a shared concern: bank tables are populated by the synthetic-data loader, the `audit_log` table is appended to by the workflow runtime, and the `eval_runs` table is written by the eval harness (per-case scores live in Langfuse, not Postgres). Co-locating the DDL with any one of those three writers would imply ownership it doesn't have. Top-level dir keeps the dependency direction clean: `synthetic_account_1/`, `compass/`, and `evals/` all read `db/schema.sql`; none of them owns it.

```
db/
└── schema.sql        # all tables; one file at v0.1, splittable later if it grows
```

Pinned table cardinalities and constraints (called out here because the workflow code on `audit_log` depends on them and they're easy to under-specify):

```sql
CREATE TABLE audit_log (
  id              BIGSERIAL PRIMARY KEY,
  workflow_run_id TEXT NOT NULL,
  phase           TEXT NOT NULL,           -- e.g. 'pre_action_proposal'
  event_kind      TEXT NOT NULL,           -- 'rule_fired' | 'rule_skipped' |
                                           -- 'proposal' | 'approval_signal' |
                                           -- 'executed' | 'declined' | 'unsupported'
  rule_id         TEXT,                    -- NULL when event_kind != 'rule_*'
  sequence_no     INT NOT NULL,            -- monotonic per workflow_run_id
  policy_hash     TEXT NOT NULL,           -- captured per-evaluation, not per-run;
                                           -- FK into policy_snapshots
  decision        TEXT,                    -- 'permit' | 'block' | 'escalate' | NULL
  actor           JSONB,                   -- {user_id, role, auth_method,
                                           --  mfa_verified}; NULL for non-human
                                           -- events (rule_fired, rule_skipped, etc.)
  payload         JSONB NOT NULL,          -- rule-specific evidence; for rule_fired
                                           -- events includes a 'regulatory_basis'
                                           -- key denormalized from Rule definition
                                           -- so 5-year-old audit rows are
                                           -- interpretable without a join
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workflow_run_id, sequence_no)
);
CREATE INDEX ON audit_log (workflow_run_id, phase);
CREATE INDEX ON audit_log (rule_id) WHERE rule_id IS NOT NULL;
CREATE INDEX ON audit_log (policy_hash);

-- Maps every policy_hash that has ever fired to the serialized rule set at
-- that version. Without this, a 5-year-old audit row's policy_hash is
-- meaningless once the code that defined the rules has changed (long-retention
-- banking audit regimes need 7+ years of interpretability). Written once per
-- (worker boot, never-before-seen hash) — typically a handful of rows over
-- the lifetime of a workflow even with active rule iteration.
CREATE TABLE policy_snapshots (
  policy_hash  TEXT PRIMARY KEY,
  workflow     TEXT NOT NULL,              -- e.g. 'send_invoice'
  rules_json   JSONB NOT NULL,             -- serialized RULES at this hash
                                           -- (id, phase, primitive name+params,
                                           --  severity, regulatory_basis, tags)
  captured_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The UNIQUE key on `audit_log` is `(workflow_run_id, sequence_no)`, **not** `(workflow_run_id, event_kind)` — multiple rules can fire in the same phase with the same event_kind, and a single workflow legitimately has multiple `policy_rejected` events (e.g. one at `pre_action_proposal`, another at `pre_execute`). `sequence_no` is allocated deterministically by the workflow code (incremented per emit, replay-stable because the workflow is replay-deterministic). Idempotency under activity replay comes from `(workflow_run_id, sequence_no)` collisions being silently dropped by `ON CONFLICT DO NOTHING` — a retried write with the same `sequence_no` is by definition the same logical event. Policy-coverage and policy-compliance suites query this table directly (see §Eval Framework).

**The `actor` column is mandatory for events with a human in the loop** (`approval_signal`, `executed` when triggered by human action, `declined`). For rule fire/skip events it's NULL. Format: `{"user_id": "u_abc123", "role": "ops_manager", "auth_method": "sso+webauthn", "mfa_verified": true}`. Banking control standards (four-eyes, SoD) need verifiable identity on every approval, not just `approved=True`; this column is what later makes `dual_control_above_threshold`-style queries possible without joining out to an external auth system.

**The `policy_snapshots` table closes the audit-interpretability gap.** The snapshot insert happens **inside the `evaluate_policy` activity body, in the same DB transaction as the audit writes** (not at worker startup — that would leave a race window where audit rows reference a hash not yet persisted). Cost: one `INSERT ... ON CONFLICT DO NOTHING` per activity invocation, almost always a PK-collision no-op. The serialized `rules_json` captures everything needed to reconstruct what fired and why years later: rule ID, phase, primitive name + its frozen params, severity, `regulatory_basis`, and tags. `hash_rules()` must hash the canonicalized full `rules_json` (stable key order, normalized params) — not just `(id, phase, severity)` — or two rules with the same id/phase/severity but different params silently collide on the same hash. Retention follows the audit_log retention policy (7+ years for banking). Detailed serialization format, predicate-spec capture mechanism, and event-kind taxonomy decisions live in a dedicated `compass/AUDIT.md` design doc, not here.

No migration framework at v0.1 — the local Postgres sidecar is regenerated from this single DDL on each dev cycle. Migrations get introduced if and when schema changes need to survive across deployments (a v0.3+ concern at earliest).

---

## Reusability Architecture

The framework is meant to be adopted by other agentic-workflow projects, not just used by this project's two demos. The structure below is the minimum that delivers reuse; layers can be added later when a second adopter forces them.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Compass framework  (compass/, one Python package, documented public API)│
│                                                                          │
│  ┌──────────────────────────────┐   ┌──────────────────────────────────┐ │
│  │ compass.policy               │   │ compass.eval                     │ │
│  │                              │   │                                  │ │
│  │ • Rule / Phase / Severity /  │   │ • WorkflowRunner protocol +      │ │
│  │   Decision / Violation types │   │   TemporalWorkflowRunner         │ │
│  │ • engine(phase, context)     │   │                                  │ │
│  │   → Decision                 │   │ • Functional accuracy scorer     │ │
│  │ • Structured trace events    │   │ • Counterfactual perturbation    │ │
│  │   (rule_fired/rule_skipped)  │   │   generator + noise-floor proto  │ │
│  │ • @primitive registration    │   │ • Trace-assertion library        │ │
│  │ • framework-core primitives  │   │   (policy compliance, no         │ │
│  │   (Resolution, Value,        │   │   subsystem)                     │ │
│  │   Identity, Input, Data      │   │ • Coverage report (SQL+CI gate)  │ │
│  │   hygiene, Evidence,         │   │ • Promptfoo runner + freeze      │ │
│  │   Approval, Audit)           │   │ • --mode=train|holdout + budget  │ │
│  └────────────▲─────────────────┘   │ • Reads Langfuse traces directly │ │
│               │                     │   (no trace-layer abstraction)   │ │
│               │                     └────────────────▲─────────────────┘ │
│               │  compass.eval imports compass.policy │                   │
│               └──────────────────────────────────────┘                   │
└──────────────────────────────────────────────────────────────────────────┘
                                ▲
                                │ public API only
                                │ (grep-based CI check forbids the reverse)
┌──────────────────────────────────────────────────────────────────────────┐
│  Project code  (consumes Compass via its public API)                     │
│                                                                          │
│  ┌─────────────────────┐   ┌─────────────────┐   ┌─────────────────────┐ │
│  │ workflows/          │   │ mcp_bank/       │   │ synthetic_account_1/│ │
│  │   send_invoice/     │   │                 │   │                     │ │
│  │     workflow.py     │   │ FastMCP server  │   │ simulate.py         │ │
│  │       @workflow.defn│   │ (read-only over │   │ verify.py           │ │
│  │       Agent loop    │   │  Postgres via   │   │ load_to_postgres.py │ │
│  │       runs inside   │   │  parameterized  │   │ config/             │ │
│  │       Runner.run()  │   │  SQL); started  │   │ generated/          │ │
│  │     activities.py   │   │  by worker via  │   │ ground_truth/       │ │
│  │       evaluate_     │   │  Stateful-      │   │   train/  holdout/  │ │
│  │       policy        │   │  MCPServerProv. │   │                     │ │
│  │       execute_send  │   │                 │   │                     │ │
│  │       audit_log     │   │                 │   │                     │ │
│  │     types.py        │   │                 │   │                     │ │
│  │     worker.py       │   │                 │   │                     │ │
│  │       OpenAIAgents- │   │                 │   │                     │ │
│  │       Plugin reg'd  │   │                 │   │                     │ │
│  │   dispute_           │   │                 │   │                     │ │
│  │   investigation/    │   │                 │   │                     │ │
│  │     (v0.2)          │   │                 │   │                     │ │
│  └─────────┬───────────┘   └────────┬────────┘   └─────────┬───────────┘ │
│            │                        │                      │             │
└────────────┼────────────────────────┼──────────────────────┼─────────────┘
             │                        │                      │
             ▼                        ▼                      ▼
      ┌──────────────────────────────────────────────────────────┐
      │  Shared infrastructure                                    │
      │                                                           │
      │  db/schema.sql   (read by mcp_bank; written to at runtime │
      │                   by audit activity + eval harness)       │
      │  evals/          (per-workflow eval configs + corpora;    │
      │                   adversarial/ holds frozen Promptfoo     │
      │                   holdout cases per release SHA)          │
      │  policies/       (per-workflow Python modules, each       │
      │                   exporting RULES: list[Rule])            │
      │  promptfoo-config/ (adversarial generation; model+plugin  │
      │                     pinned per release)                   │
      │  Langfuse        (Docker sidecar; LLM trace store +       │
      │                   native LLM-as-judge for v0.2 trace      │
      │                   coherence evaluators)                   │
      └──────────────────────────────────────────────────────────┘

Extension surfaces (no Compass fork needed):

  WorkflowRunner protocol →  default TemporalWorkflowRunner ships with
                              compass.eval; the protocol exists so the
                              harness is mockable in unit tests and an
                              adopter using a non-Temporal runtime can
                              bring their own implementation.
  Custom primitive types  →  @compass.policy.primitive("my_rule") from
                              within your project — app-specific rules
                              (e.g., this project's Billing integrity)
                              live with the consuming code.
  LLM provider            →  via OpenAI Agents SDK's model interface +
                              OpenRouter; swappable per run.

Deliberately NOT abstracted at v0.1 (extract later if needed):

  • No separate trace package — Langfuse is the trace store; the harness
    reads it directly via the Langfuse SDK. A WorkflowTrace abstraction
    layer is the obvious extension if a second trace source ever exists.
  • No Sink protocol — audit log and eval history go to Postgres via
    plain SQL inside compass.eval. Extractable to a protocol when a
    second backend is real.
  • No separate compass-trace / compass-policy / compass-eval packages —
    one `compass` package with submodules. Splittable later without
    breaking changes if a real adopter needs to install policy without
    eval (or vice versa).
  • No second WorkflowTrace adapter — Temporal is the only workflow
    runtime at v0.1; OpenAI Agents tracing context propagates across
    Temporal activities via the OpenAIAgentsPlugin, and from there to
    Langfuse via openinference-instrumentation-openai-agents + OTLP.
  • No rule composition operators (and/or/not) — compound conditions
    become new primitives in Python, not nested rules.
  • No YAML policy DSL at v0.1 — rules are typed Python in
    policies/<workflow>.py. YAML loader is additive when an adopter
    forces it; see §Design rationale for the rubric.
  • No separate policy-compliance subsystem — it's a ~30-line trace
    assertion library on top of the rule_fired events that the policy
    engine emits. See §Eval Framework §4.
  • No bespoke trace-coherence engine — v0.2 uses Langfuse-native
    LLM-as-judge Evaluators. ~50 LOC of Compass-side glue, not a
    subsystem.

Boundary enforced by: a CI step that runs
    grep -rnE "^[[:space:]]*(from|import)[[:space:]]+(workflows|mcp_bank|synthetic_account_1)(\.|[[:space:]]|$)" compass/
    and fails if it matches. Covers both `from X import Y` and
    `import X[.Y]` forms. ~5 lines of bash; same guarantee as
    import-linter for the one rule that matters. Upgrade to
    import-linter only if the boundary needs multiple internal layers.

Directory naming: project directories that are Python packages use
underscore-not-hyphen (`mcp_bank/`, `synthetic_account_1/`,
`workflows/`) so they're importable. The `mcp-bank` shorthand
elsewhere in this doc refers to the same dir; on-disk and in imports
it's always `mcp_bank/`.
```

### One package, submodules

The framework is **one Python package** (`compass`) with two submodules: `compass.policy` and `compass.eval`. Public API is the package's top-level exports. Splitting into separate packages later is mechanical and non-breaking; do it only if an adopter forces the question.

### How a workflow uses `compass.policy`

The workflow's policy lives at `policies/send_invoice.py` as a typed list:

```python
# policies/send_invoice.py
from compass.policy import Rule, Phase, Severity
from compass.policy.primitives import numeric_threshold, restricted_recipient, ...
from .primitives import require_amount_source  # @primitive("...") decorated

RULES: list[Rule] = [
    Rule(id="customer_resolution_confident",
         phase=Phase.PRE_ACTION_PROPOSAL,
         predicate=resolution_confidence_threshold(field="resolved.customer", min=0.85),
         severity=Severity.BLOCK,
         must_be_covered=True),
    Rule(id="amount_under_dual_control_threshold",
         phase=Phase.PRE_ACTION_PROPOSAL,
         predicate=numeric_threshold(field="proposal.total_amount", max=10_000),
         severity=Severity.ESCALATE),
    # ...
]
```

Inside the workflow's `evaluate_policy` activity:

```python
from compass.policy import evaluate_pre_action_proposal
from policies.send_invoice import RULES

@activity.defn
async def evaluate_policy(proposal: InvoiceProposal, ctx: dict) -> Decision:
    return evaluate_pre_action_proposal(
        rules=RULES,
        proposal=proposal.model_dump(),
        **ctx,
    )
```

The engine takes `(rules, phase, context) → Decision`. Application-specific primitives (e.g., Billing integrity) are registered from the workflow code via `@compass.policy.primitive("require_amount_source")`. There's no YAML loader at v0.1; if one is added later it produces the same `list[Rule]` and plugs into the same engine.

### How `compass.eval` runs

```python
from compass.eval import run_eval, TemporalWorkflowRunner
from workflows.send_invoice.workflow import SendInvoiceWorkflow

runner = TemporalWorkflowRunner(
    client=temporal_client,
    workflow=SendInvoiceWorkflow,
    task_queue="compass",
)
report = await run_eval(
    runner=runner,
    corpus="evals/send_invoice/corpus.yaml",
    mode="train",  # or "holdout"; "holdout" requires --holdout-justification
    suites=["functional", "counterfactual", "coverage", "cost_latency"],
    # "adversarial" runs via Promptfoo; "policy_compliance" is a
    # trace-assertion library imported by each per-suite test, not a suite name.
)
```

For each test case the runner kicks off a workflow execution (`client.execute_workflow(...)`), reads back the final outcome, and pulls the Langfuse trace for the workflow ID. The harness scores against ground truth, orchestrates N perturbed variants for counterfactual stability (with the noise-floor protocol from §Eval Framework §3), and aggregates. Promptfoo is invoked as a separate suite from the same runner with the frozen-cases mechanism. Pre-flight: harness estimates total spend and refuses to run if `--mode=holdout` would exceed the per-release-run budget.

### Workflow-runtime neutrality (limited)

`compass.policy` is fully runtime-agnostic — phase name + context dict in, Decision out. `compass.eval` is mostly runtime-agnostic via the `WorkflowRunner` protocol, but reads Langfuse traces directly for now. A `WorkflowTrace` abstraction can be extracted when a second trace source actually exists.

### Framework-core vs. application-specific primitives

**Framework-core** (ships with `compass.policy`, available to all adopters): Resolution gates, Value gates, Identity gates, Input handling, Data hygiene, Evidence / citation gates, Approval gates, Audit gates. **Application-specific** (lives in the consuming project's policy code, registered via `@compass.policy.primitive`): Billing integrity (composes on top of the framework-core Evidence / citation and Data hygiene families). Criterion: if a primitive expresses a generic safety/governance concept that applies across agent action types, it's core; if it encodes domain semantics, it's application-specific.

### Dependency direction, enforced mechanically

Strict one-way: `workflows/*`, `synthetic_account_1/`, `mcp_bank/` may import from `compass.*`. The reverse is forbidden. Enforced by a single CI step that covers both `from X import Y` and `import X[.Y]` forms:

```bash
if grep -rnE "^[[:space:]]*(from|import)[[:space:]]+(workflows|mcp_bank|synthetic_account_1)(\.|[[:space:]]|$)" compass/; then
    echo "compass/ must not import from project code"; exit 1
fi
```

Same guarantee as a full `import-linter` contract for the one rule that matters at this scale. If the boundary grows complex enough that grep can't express it (multiple internal layers with their own rules), upgrade to `import-linter`.

### Configuration-driven adoption

Adopting Compass on a new project:

1. Pick which policy phases your workflow uses (subset of the seven).
2. Write a Python policy module (`policies/<workflow>.py`) exporting `RULES: list[Rule]` using framework-core primitives + your own via `@compass.policy.primitive`.
3. If you're on Temporal, use `TemporalWorkflowRunner`. Otherwise implement `WorkflowRunner` (one method).
4. Point `compass.eval.run_eval` at your corpus and runner.

No fork. `COMPASS_ADOPTION.md` ships with a worked minimal example (~40 lines of Python total).

---

## Policy Engine + Primitive Library

Workflow-agnostic primitives in a small **Python library** (not a YAML DSL — see [§Design rationale](#design-rationale) for the rubric). Compass policy is a **structured governance layer on top of OpenAI Agents SDK guardrails / lifecycle hooks** (for phases inside the agent loop) and **explicit Temporal activities** (for phases at workflow boundaries). It is not a parallel hook system — see "Relationship to OpenAI Agents SDK hooks" below.

For a single workflow, four OpenAI Agents SDK guardrails written directly in Python beat any framework. The library is justified at ≥2 workflows that share a vocabulary plus a policy-compliance eval that needs stable rule IDs. v0.2's reuse ratio measures this.

A `Rule` is a frozen dataclass with five fields:

- **`id: str`** — unique identifier; referenced by audit log, policy-compliance assertions, and the coverage report.
- **`phase: Phase`** — when in the workflow lifecycle the rule fires (one of five; see phase table below). The phase implicitly determines the context contents — `pre_action_proposal` always receives the proposal, `input_validation` always receives the user message, etc. No separate selector field.
- **`predicate: Predicate`** — a pure `(context) → Violation | None` callable, returned by a factory like `numeric_threshold(field="...", max=...)`. Factories are decorated with `@primitive("...")` so the coverage report can enumerate them.
- **`severity: Severity`** — `BLOCK` (short-circuits to audit-and-reject) or `ESCALATE` (routes to human review with the violation surfaced). **`ESCALATE` is only realizable at workflow-level phases** (`pre_action_proposal`, `pre_execute`, `audit_validation`); for OpenAI Agents SDK-bound phases (`input_validation`, `output_validation`), severity collapses to binary (OpenAI Agents SDK guardrails are tripwire-or-nothing by contract) and any rule declared `ESCALATE` at those phases is rejected by `Rule.__post_init__` with a clear error.
- **`on_violation: str | None`** — message template; pair with `surface_to_user: bool` to control approval-UI display.
- **`regulatory_basis: tuple[str, ...]`** — provenance for audit. Free-form citations to the regulation, internal policy, or control standard that motivates the rule's existence (e.g., `("BSA §326",)` for a KYC rule, `("internal SOP-BILL-02",)` for a billing-integrity rule, `()` for rules that are purely operational hygiene). Denormalized into `audit_log.payload` on every `rule_fired` event, so when a regulator asks "why did you reject this transaction five years ago," the answer doesn't depend on the source code still being available — it's in the audit row alongside the rule ID. The coverage report can also slice by regime via `tags`.
- Plus `must_be_covered: bool` (CI-gate flag for the coverage report) and `tags: tuple[str, ...]` (free-form, used by the coverage report to group rules by family or compliance regime — e.g., `("billing_integrity",)`, `("BSA",)`, `("SOX",)`).

No rule composition operators (`and`/`or`/`not`). Compound conditions become new Python primitives (registered via `@compass.policy.primitive`), not nested rule structures. Composition added back only when a real compositional pressure surfaces.

### Hard rules

Four things every policy module must respect:

1. **Predicates must not perform direct I/O or non-determinism.** Phases that fire inside the agent loop run inside a Temporal workflow; the workflow must be deterministic for replay to work. Forbidden: `datetime.now()`, `random`, raw file reads, raw HTTP calls, raw DB queries. **Permitted**: invoking other activities (including sub-agent `Runner.run(...)` calls — the upstream Temporal `input_guardrails` sample does exactly this for LLM-judge guardrails) and reading from the context dict. Anything that needs an external fact — a freshness timestamp, a sanctions hit, a real-time KYC status — is loaded by a *separate Temporal activity that runs before the agent loop* and stuffed into the context dict; the predicate reads from the dict, deterministically. Violating this rule corrupts workflow replay; it's the single biggest footgun.

2. **Engine errors are distinct from engine decisions.** Three exception types and they have different retry semantics:
   - `PolicyDecisionError(decision=block|escalate, ...)` — the rule fired and decided. **Non-retryable.** Wrapped as `ApplicationError(non_retryable=True)` if the workflow needs to short-circuit via exception rather than returning a `Decision`.
   - `PolicyEngineError(...)` — the engine itself failed to evaluate (primitive not registered, malformed context dict, predicate raised). **Retryable** with backoff; a transient cause is plausible.
   - `PolicyInfraError(...)` — a pre-loop activity that loaded a fact failed (Postgres outage during freshness lookup, etc.). **Retryable**, surfaced separately so on-call doesn't conflate it with a policy decision.

   The interop rule for Stage 4 is therefore: *policy decisions* are non-retryable; *engine and infra errors* are retryable. The naïve framing "policy rejection is non-retryable" collapses these — don't.

3. **Policy drift across approval waits, with explicit behavior.** Rules are Python imported at worker startup — there's no hot-reload, so the only way to change rules is to ship code + restart the worker. But for long-running workflows (especially v0.2's multi-day dispute investigation), a worker can absolutely be restarted with new rules between the moment the agent drafted (`pre_action_proposal`) and the moment the human-approved action executes (`pre_execute`). Drift behavior is **explicitly specified**:
   - Every audit-log entry captures `policy_hash` per evaluation (not per workflow run — see schema in §Database).
   - If `pre_execute` evaluation produces a `policy_hash` different from the one captured at `pre_action_proposal` *and* the new evaluation would block or escalate something the old one permitted, the workflow **fails closed with escalation** (not a silent block). A `policy_drift_after_confirmation` audit event fires; the human reviewer is notified via the approval UI that policy has tightened since their approval and re-approval is required. The dedicated primitive that detects this is `prohibit_policy_drift_after_confirmation`, shipped as framework-core.
   - "Fail open" (grandfather under old policy) is a deliberate non-choice — at v0.1 a tightened rule is almost certainly tightening for a reason and the human should see it. Adopters who want grandfathering can register a custom primitive.

4. **`RULES` must be a statically-computable module-level list.** No runtime data sources, no environment-dependent comprehensions, no dynamic registration after import. The list at import time is what the worker hashes and snapshots; if `RULES` is built from `load_tiers_from_api()` or similar, two workers can compute different hashes from the same source code and audit reconstructability breaks.

### Primitive families

**Framework-core** (ship with `compass.policy`, available to any project that adopts Compass). Each entry gives the factory signature, what the predicate checks, and the phase where it's typically wired.

**Resolution gates**
- `resolution_confidence_threshold(field, min)` — fails if the resolution-confidence score on `field` (e.g., `"resolved.customer"`) is below `min`. Phase: `pre_action_proposal`.
- `require_existing_entity(field, entity_type)` — fails if `field` does not resolve to an existing entity of `entity_type`. Phase: `pre_action_proposal`.

**Value gates**
- `numeric_threshold(field, *, min=None, max=None)` — fails if `field`'s value falls outside `[min, max]`. Phase: `pre_action_proposal`.
- `cumulative_value_per_session(field, max, window)` — fails if the cumulative sum of `field` across the workflow run exceeds `max`; `window` selects which prior records count. Phase: `pre_action_proposal`.

**Identity gates**
- `entity_status_equals(field, expected_status)` — fails if the entity at `field` doesn't have `expected_status` (e.g., `kyc_status == "verified"`). Phase: `pre_action_proposal`.
- `prohibit_self_dealing(payer_field, payee_field)` — fails if both fields resolve to the same entity. Phase: `pre_action_proposal`.
- `restricted_recipient(field, blocklist_ref)` — fails if the recipient at `field` appears on the named blocklist. Phase: `pre_action_proposal`.

**Input handling**
- `sanitize_freeform_text(field)` — rejects or strips control characters from a freeform input field. Phase: `input_validation`.
- `prompt_injection_detected(field)` — LLM-judge predicate; flags suspected prompt-injection in `field`. Phase: `input_validation`.

**Data hygiene**
- `data_minimization_check(prompt_field, allowlist)` — fails if the prompt includes fields outside `allowlist`. Phase: `input_validation`.
- `require_field_recency(field, max_age_days, timestamp_context_key)` — fails if the field's source-record age exceeds `max_age_days` (e.g., KYC ≤ 365 days, dispute evidence ≤ 90 days). The freshness timestamp must be loaded into the context by a pre-loop activity so the predicate stays pure (see Hard Rule 1). Phase: `pre_action_proposal`.

**Evidence / citation gates**
- `require_evidence_citation(field)` — fails if `field` (or every element of a list field) has no `source_refs` pointing to MCP tool results that support it. Phase: `pre_action_proposal`.
- `require_data_source_for_field(field, allowed_sources)` — fails if `field`'s `source_type` is not in `allowed_sources`. Phase: `pre_action_proposal`.

**Approval gates**
- `require_explicit_confirmation_before_side_effect()` — fails if no approval signal was received before the side-effect activity ran. Phase: `pre_execute`.
- `prohibit_silent_modification_after_confirmation()` — fails if the proposal hash at `pre_execute` differs from the hash captured at approval. Phase: `pre_execute`.
- `prohibit_policy_drift_after_confirmation()` — escalates if `policy_hash` at `pre_execute` differs from the hash captured at `pre_action_proposal` *and* the new evaluation would block or escalate something the old one permitted (see Hard Rule 3). Phase: `pre_execute`.
- `dual_control_above_threshold(amount_field, threshold)` — fails if `amount_field` exceeds `threshold` without a second distinct approver in the audit log. Phase: `pre_execute`.
- `approval_within_window(max_elapsed)` — fails or escalates if more than `max_elapsed` (e.g., `"24h"`) passed between the approval signal and `pre_execute` firing. Catches stale approvals on workflows that sit across long signal waits. Phase: `pre_execute`.

**Audit gates**
- `log_full_reasoning_trace()` — fails if the audit-entry candidate is missing the agent reasoning trace. Phase: `audit_validation`.
- `log_data_sources_consulted()` — fails if the audit-entry candidate is missing the list of MCP tool calls consulted. Phase: `audit_validation`.
- `log_policy_version()` — fails if the audit-entry candidate is missing the `policy_hash` in force during this specific evaluation (per-evaluation, not per-run). Phase: `audit_validation`.

**Application-specific** (registered from project code via `@compass.policy.primitive(...)`; not in Compass itself):

**Billing integrity** — composes on top of the framework-core Evidence / citation and Data hygiene families.
- `require_amount_source()` — fails if any invoice line item lacks `source_type ∈ {contract, rate_card, time_tracking, user_specified}` or lacks `source_refs`. Domain-specific instantiation of `require_evidence_citation`. Phase: `pre_action_proposal`.
- `contract_consistency_check()` — when `get_active_contract(customer_id, today)` returns an active contract, fails if the invoice's billing structure or rates contradict contract terms. Phase: `pre_action_proposal`.
- `prohibit_exceed_contract_cap()` — fails if invoice hours or total exceed caps stated in the active contract. Phase: `pre_action_proposal`.
- `currency_consistency_check()` — fails if line items, invoice, and cited contract are in different currencies without an explicitly cited FX conversion. Phase: `pre_action_proposal`.

**Deferred to v1.0+** (named here so the trajectory is clear, not built at v0.1):
- **Resource control** — `tool_call_budget`, `model_call_budget`, `latency_budget`. Real production-agent governance need; not required for two demo workflows without runaway loops.
- **Time-window gates** — bank cutoffs, holiday windows.
- **Conflict-of-interest** — broader segregation-of-duties patterns beyond `prohibit_self_dealing`.

### Phases and how they're wired

Five phases, all wired. Two use OpenAI Agents SDK mechanisms; three use explicit Temporal activities. Per-tool-call governance (`pre_tool_call` / `post_tool_call`) was considered and dropped — see DAG observations under §Architecture. Adding them back is a non-breaking change (new `Phase` enum values + a `compile_tool_guardrails` method) if a real use case ever shows up.

| Phase | Fires at | Implementation mechanism | Context |
|---|---|---|---|
| `input_validation` | Before `Runner.run(agent, ...)` enters its loop — fires twice in send-invoice: scope-gate agent and main agent | OpenAI Agents SDK **input guardrail** | User message (+ scope-gate classifier output on second firing) |
| `output_validation` | On the agent's structured Pydantic output | OpenAI Agents SDK **output guardrail** | The `InvoiceProposal` (or equivalent) |
| `pre_action_proposal` | After agent returns; before approval wait | Explicit Temporal **`evaluate_policy`** activity | Full proposal + resolved entities + tool-call history + reasoning trace |
| `pre_execute` | After approval signal; first thing in the side-effect activity body | Explicit Temporal **`execute_send`** activity (workflow-step, *not* an agent tool — see Stage 4) | Approved proposal + approval state + elapsed-since-approval + `policy_hash_at_proposal` |
| `audit_validation` | First thing in the audit activity body, before the write | Explicit Temporal **`audit_log`** activity | Audit entry candidate |

**v0.1 weight by phase.** Phases vary in how heavily they're loaded:

- **`pre_action_proposal` carries most of the policy load.** It sees the full proposal + tool-call history, so most Resolution / Value / Identity / Billing integrity / Evidence-citation rules land here.
- **`input_validation` is the second-heaviest phase** (scope gate + parsed-intent predicates).
- **`pre_execute` is the safety-net phase** — if a rule fires here, by definition something changed between approval and execute. `prohibit_silent_modification_after_confirmation`, `prohibit_policy_drift_after_confirmation`, `approval_within_window` live here.
- **`audit_validation` is light** — completeness checks on the audit entry.
- **`output_validation` is a light structural check** — Pydantic models already enforce structure; the policy rule covers the cases the type system can't (e.g., business rules over field combinations).

### Relationship to OpenAI Agents SDK hooks

Compass policy is **not** a parallel hook system. It's a structured governance layer on top of the OpenAI Agents SDK's hook surface. Specifically:

- For OpenAI Agents SDK-bound phases (`input_validation`, `output_validation`): `compass.policy.attach_to_agent(agent, RULES, ...)` bundles all rules for each phase into one callback (per phase) and registers it via the SDK's `@input_guardrail` / `@output_guardrail`. That callback's body calls `compass.policy.engine.evaluate(...)`. Same OpenAI Agents SDK machinery; what changes is what's inside the callback.
- For workflow-level phases (`pre_action_proposal`, `pre_execute`, `audit_validation`): explicit Temporal activities call `evaluate(...)` directly. These can't be OpenAI Agents SDK hooks because they fire outside the agent loop — the agent has returned, the proposal is in hand, the human has approved.

**What Compass adds over raw OpenAI Agents SDK guardrails / hooks:**

1. **Three phases the OpenAI Agents SDK doesn't have.** `pre_action_proposal`, `pre_execute`, `audit_validation` are workflow concepts. The OpenAI Agents SDK has no analog.
2. **Stable rule identity.** The `id` field on each `Rule` is referenced by audit-log rows, policy-compliance trace assertions, and the coverage report. Raw OpenAI Agents SDK callbacks are anonymous; you'd hand-roll an ID system per workflow.
3. **Structured trace events as the substrate.** Every `evaluate(...)` call emits `rule_fired` / `rule_skipped` events to the audit log automatically. Policy compliance ("did the right rules fire on this test case?") is then a trace assertion in the eval harness — **not a Compass subsystem**. Policy coverage ("what fraction of rule IDs fired across the eval corpus?") is a SQL query over `audit_log`. With raw OpenAI Agents SDK callbacks, this logging is per-workflow boilerplate.
4. **Uniform vocabulary across workflows.** `pre_action_proposal` means the same thing in send-invoice and dispute-investigation; primitive factories like `numeric_threshold(field=..., max=...)` are imported the same way from both. With raw OpenAI Agents SDK callbacks every workflow's gating is bespoke.
5. **Richer severity at workflow phases.** `ESCALATE` routes to human review rather than terminating. Realizable at `pre_action_proposal`, `pre_execute`, `audit_validation` only — OpenAI Agents SDK guardrails are binary tripwire by contract, so OpenAI Agents SDK-bound phases collapse to block (and `Rule.__post_init__` rejects misconfigurations at construction time).
6. **Workflow-runtime neutrality for workflow-level phases.** Those are workflow concepts, not agent-loop concepts; they work identically against a Temporal workflow, a LangGraph runtime, or a custom orchestrator that implements `WorkflowRunner`.

If you don't need 1–6, use raw OpenAI Agents SDK guardrails and skip the Compass layer entirely. Compass is justified for a multi-workflow reusable framework; not for a single-purpose product.

The primitive catalog is enumerated above; public API details and the full example `RULES` modules for both workflows live in `POLICY.md`.

---

## The Eval Framework

v0.1 ships **three custom suites** (functional, counterfactual, coverage-report) and **delegates** adversarial to Promptfoo, cost/latency to Langfuse, and policy-compliance to trace assertions over the same audit-log events the policy engine already emits. v0.2 adds **trace coherence** via Langfuse's native LLM-as-judge — not a new Compass subsystem.

Compass's value-add here is the counterfactual generator and the workflow-trace-aware runner, not a parallel eval platform.

### 0. Train / holdout discipline

Every labeled corpus is split into **train** (used during prompt/rule iteration — you can look at it, fail on it, tune against it) and **holdout** (locked from the day the corpus is generated; only read at release time, only for the final reported numbers; any reported metric that doesn't say "on holdout" is suspect).

- **Corpus size and statistical framing.** v0.1 send-invoice ships **~120 cases total** (~85 train / ~35 holdout); v0.2 disputes ship ~90 cases (~63 train / ~27 holdout). The earlier ~50/~30 figures were too small to support the headline claim — Wilson 95% CI on 14/15 is roughly [68%, 99.7%], so a ~15-case holdout literally cannot distinguish 95% from 99%. With ~35 holdout cases the lower-bound CI on a >95% true rate tightens to ~[83%, 100%], which is the smallest size where a ">95% holdout" target is statistically meaningful.
- **Released claim is stricter than "pass rate >95%."** v0.1 reports both (a) the observed pass rate and its Wilson 95% lower bound, and (b) the count of failed cases. Release gate: **0 failed cases on the functional holdout for the four-amount-source slice that the writeup makes claims about** (contract-derived, rate-card×time, rate-card flat, user-specified). A single failure blocks release pending root-cause and a corpus expansion to re-establish the rate claim.
- **Seed-level splitting, not variant-level.** A single test case has many counterfactual perturbations attached. The split happens on the *seed case*, and every perturbation of that seed travels with it. Otherwise a seed's perturbations end up in both splits and leakage is silent.
- **Promptfoo determinism caveat.** Promptfoo's redteam plugins use LLMs to generate variants; they're not bit-deterministic even with a fixed seed. Discipline works differently here: per release, a frozen Promptfoo config (model pin + plugin pin + seed) defines the holdout adversarial run, and *the generated test cases are persisted to `evals/adversarial/holdout_cases.jsonl` on first run and reused thereafter* — this turns Promptfoo's stochastic generation into a frozen corpus. Train-mode runs regenerate fresh each invocation.
- **Harness enforcement, with realistic limits.** The eval runner takes a `--mode={train,holdout}` flag. `train` mode opens with `O_RDONLY` against a chroot that excludes `ground_truth/holdout/`. `holdout` mode requires a `--holdout-justification="..."` flag (logged to `eval_runs`) and refuses to run more than 3 times per git commit (counter persisted in `eval_runs` keyed on git SHA). **This isn't unbypassable.** A determined developer can `cat` the holdout file in another shell. Discipline is a posture, not an airlock — but the harness covers the accident case (the 90% of leakage that comes from forgetting which split you're in).
- **Cost budget per release run.** A full holdout pass (functional + adversarial + counterfactual + coverage) is budgeted at **≤ $40 per workflow, per release run** (v0.1 estimate; revisit with measured numbers after Stage 11). The harness rejects holdout invocation if estimated spend exceeds budget and shows the user the breakdown. Train-mode runs are uncapped but log per-run spend so iteration cost is visible. Per-eval-run telemetry is essential — without it, "we burned $300 on one iteration loop" becomes a surprise.

### 1. Functional accuracy

Labeled corpus per workflow with ground-truth expected outcomes. Deterministic field-by-field scoring with tolerance bands for numeric fields.

- Send invoice: ~120 cases at v0.1, each with correct customer, amount, line items, terms
- Dispute investigation: ~90 cases at v0.2, each with correct decision class (refund / deny / escalate) and key evidence references

### 2. Adversarial robustness — delegated to Promptfoo

Use Promptfoo's red-team module rather than reinventing. Promptfoo generates context-aware adversarial inputs across its 50+ vulnerability plugins; the harness runs them through the Compass workflow and scores using both Promptfoo's grader and the structured policy-fire events the workflow emits.

Custom additions on top of Promptfoo's defaults:

- Banking-specific attack contexts (invoice fraud, customer impersonation, dispute manipulation)
- Dual scoring (did Promptfoo say it passed AND did the expected policy rule fire, per the trace event store?)
- Failure-pattern analysis: when Promptfoo finds a hole, classify it (prompt issue / policy gap / tool boundary issue)

The writeup framing: *"Promptfoo provides the adversarial generation engine; Compass provides the financial-agent-specific context, policy integration, and failure analysis."* No reinvention.

### 3. Counterfactual perturbation

**Noise floor**: the baseline divergence rate from running the *same* input through the agent multiple times at non-zero temperature. The LLM samples stochastically, so identical inputs produce slightly different outputs run-to-run; that run-to-run divergence is the floor below which you cannot drive variability. Raw perturbation stability ("95% of variants produced the same output") cannot distinguish real sensitivity from this baseline noise — measuring perturbation divergence *relative to* the noise floor is what makes the metric meaningful.

For each test case, automatically generate N perturbed variants by varying specific input dimensions; measure outcome stability across variants relative to a measured sampling-noise floor. Distinct from adversarial — perturbations are *harmless variations that shouldn't change the answer*, not attacks designed to break the system.

**Protocol.** Pinned so the metric is reproducible:

- **Variants per case**: N = 5.
- **Temperature**: agent runs at `temperature=0.2` (low but non-zero so we measure genuine stability, not single-sample determinism). Pinned in `evals/run_config.yaml`.
- **Sampling-noise floor**: before measuring perturbation stability, the harness re-runs the unperturbed case M = 5 times at the same temperature and records the divergence rate as the noise floor. Reported metric is **stability-above-floor** (perturbed-variant divergence rate ÷ noise-floor divergence rate). A stability-above-floor of 1.0 means perturbations behave identically to re-runs of the same input — i.e., the agent is as stable to perturbation as the LLM's own sampling allows. **This is the headline metric, not raw stability.** Raw "median >95%" is reported but explicitly flagged as bounded above by `100% − noise_floor%`.
- **Outcome equality**: for send-invoice, two runs are "equal" iff the decision class and the line-item totals match within tolerance (defined per-field in ground truth). For dispute investigation (v0.2), iff the decision class matches. *Reasoning-path equality* is a separately reported metric (see below), not a tiebreaker.

**Perturbation dimensions for send-invoice.** Every perturbation class declares whether the canonical answer should be **invariant** or **variant** after perturbation:

- *Whitespace / case / format perturbations* (INVARIANT): "$7,200" ↔ "$7200" ↔ "seven thousand two hundred"; trailing-zero variants; rounding-equivalent variants.
- *Amount format perturbations* (INVARIANT): same as above, scoped to amounts.
- *Memo paraphrase perturbations* (INVARIANT): scope description re-worded while preserving meaning, generated by an LLM with a self-consistency check (the rewrite is rejected if a separate LLM-judge says it changed meaning).
- *Time-tracking structure perturbations* (INVARIANT): same total hours split across different numbers of entries (1×24h vs. 8×3h vs. 24×1h); same hours described with paraphrased descriptions. Total should be invariant.
- *Contract-paraphrase perturbations* (INVARIANT): scope clauses and rate-override sentences paraphrased while preserving meaning. Chosen rate and total should be invariant.
- *Contract semantic-flip perturbations* (VARIANT — sanity check): small clause edits that flip a rule (e.g., toggling a `list_rates_apply: false` flag, changing a cap value). Outcome MUST change; a stable outcome under a semantic flip is a bug, scored against the agent.

**What is NOT a perturbation class.** Name perturbations like `"Acme Corp" / "Acme Corporation"`, Levenshtein-budget typos against customer names, abbreviation variants. **These are not meaning-preserving in fintech** — they routinely denote distinct registered legal entities (different EIN, different bank account, different KYC file). Treating them as INVARIANT would push the agent to suppress a genuinely correct discriminator. Name discrimination is tested by the functional accuracy suite (cases for distinguishing `Acme Corp` from `Acme Corporation`), not by perturbation.

**Perturbation dimensions for dispute investigation (v0.2):**

- *Order-of-evidence perturbations* (INVARIANT): same evidence presented in different sequences
- *Adjacent-transaction perturbations* (INVARIANT): include/exclude tangentially-related transactions in the agent's context
- *Phrasing perturbations* (INVARIANT): how the customer describes the dispute (formal vs. emotional vs. terse) without changing facts. Same LLM rewrite-with-judge protocol as memo paraphrase.
- *Time-elapsed perturbations* (depends): same dispute filed at different times relative to the transaction — outcome may legitimately depend on policy windows (chargeback deadlines etc.), so the ground truth specifies expected outcome per time bucket.

**Outputs:**

- *Stability score per test case* — raw and stability-above-floor — "X% of perturbations produced the same decision, vs. Y% noise floor → stability-above-floor Z"
- *Sensitivity profile per input dimension* — across the corpus, which input dimensions does the agent's behavior depend on more than expected?
- *Reasoning-path divergence map* — defined comparison metric: pairwise cosine similarity of [text-embedding-3-small] embeddings of the per-step reasoning text extracted from the Langfuse trace; a pair is "divergent" if similarity falls below a fixed threshold (calibrated by running the same input through the agent M times and taking the 5th-percentile pairwise similarity as the threshold). Same answer arrived at via divergent reasoning is flagged. The threshold and embedding model are pinned in `evals/run_config.yaml`.

### 4. Policy compliance — trace assertions, no subsystem

For each test case, ground truth declares the expected set of `rule_fired` events: which rule IDs should fire at which phase. The harness queries the per-test trace in Langfuse (or equivalently the `audit_log` rows) and asserts the set matches. This is **not a Compass subsystem** — it's a ~30-line assertion library on top of the structured events the policy engine already emits. The earlier draft over-built this as a parallel category; collapsing it removes ~hundreds of LOC.

### 5. Policy coverage — SQL report, no subsystem

Across the entire eval corpus, what fraction of the policy library's rule IDs actually fired? Implementation: harness fetches the set of `workflow_run_id`s for an eval run from Langfuse (Dataset Run item-executions → trace IDs; trace ID == `workflow.info().workflow_id` by construction), then one SQL query against `audit_log`:

```sql
-- trace_ids = langfuse.api.dataset_run_items.list(dataset_run_name=$run).trace_ids
SELECT rule_id,
       COUNT(*) FILTER (WHERE event_kind = 'rule_fired')   AS fires,
       COUNT(*) FILTER (WHERE event_kind = 'rule_skipped') AS skips
  FROM audit_log
 WHERE workflow_run_id = ANY($1::text[])
 GROUP BY rule_id;
```

Plus a small Python module that imports each `policies/<workflow>.py` and enumerates `RULES` to compute the join. Total: ~50 LOC, not a subsystem. Surfaced as a dashboard panel. **Gated by CI**: build fails if any `Rule` with `must_be_covered=True` has zero `fires` rows in the holdout run. The `must_be_covered=True` flag is opt-in per rule; all Billing integrity primitives carry it by default so criterion 8 ("no Billing integrity rule is dead code") is mechanically enforced, not just reported.

### 6. Cost/latency — Langfuse native

Each test reports tokens, dollars, p50/p95 latency, tool call counts. All of this comes from Langfuse natively (the trace store already captures token usage and span timing). Compass adds: per-run aggregation against the cost budget defined in §0.

### 7. Trace coherence (v0.2) — Langfuse LLM-as-judge

Multi-step agent traces have a coherence dimension single-call evaluation can't measure. Implementation uses **Langfuse's native LLM-as-judge** over recorded traces — no Compass subsystem and no third-party eval framework.

Rubrics (each a single Langfuse `Evaluator` definition):

- *Logical consistency*: judge reads the full trace, flags contradictions.
- *Memory persistence*: facts established in step N still respected in step N+3.
- *Reasoning quality*: per-step rubric grade.

**Judge controls (mandatory; otherwise scores drift):**

- *Model pinning*: judge model and snapshot are pinned in `evals/judge_config.yaml` (e.g., `gpt-4o-2024-08-06`, not `gpt-4o`). Version unpin requires a writeup.
- *Self-consistency*: each judgment is run 3× and the majority vote is the recorded score. Variance across the 3 runs is itself recorded; high-variance test cases are flagged for human review.
- *Anti-self-preference*: judge model MUST be from a different family than the agent model. If the agent runs Anthropic, the judge runs OpenAI, and vice versa. If they happen to be the same family, the run records a `same_family_judge` warning.
- *Bias controls*: judges score on a fixed rubric with explicit examples; no free-form numeric scoring.

Applied primarily to dispute investigation (where multi-step reasoning is the core challenge). Less relevant for the more linear send-invoice flow.

---

## Build Stages

### v0.1

**Stage 1 — Foundation**
Repo init, root `pyproject.toml` (single Python project; uv for env management), lint/format config (ruff), type-checker (mypy or pyright), CI config, `.gitignore`, top-level `README.md` stating the framework-first thesis. CI includes a **dependency-direction lint** — the grep snippet from §Reusability Architecture above (covers both `from X import Y` and `import X[.Y]` forms; uses Python-importable underscore names) that fails the build if `compass/` imports from project code. Same guarantee as `import-linter` for the one rule that matters at this scale; upgrade to `import-linter` only if the boundary grows complex enough to need contract DSL. No per-package directories — each subsequent stage creates the dirs and files it needs.

**Stage 2 — Synthetic Account 1 Data Generator**
Banking + invoicing + customers + rate cards. Ambiguity-rich subset. Ground truth files for v0.1 evals (excluding dispute-specific ones). DDL lives in the shared `db/schema.sql` (see §Database); this stage adds the bank-data table definitions there, plus the runtime-owned tables (`audit_log` with `actor` column, `policy_snapshots`, `eval_runs`) called out in the §Database section — even though no v0.1 stage *writes* to them yet, they need to exist before Stage 4 (audit writes) and Stage 5 (policy snapshot writes) can run. Idempotent `synthetic_account_1/load_to_postgres.py` (truncate + bulk reload from JSONL) populates only the bank-data tables.

**Stage 3 — `bank` MCP Server**
Read-only Python MCP built with **FastMCP** (decorator-based `@mcp.tool()` API; auto-generates MCP tool schemas from Python type annotations + Pydantic models), exposing **domain-specific, structured tools** — `list_customers`, `get_customer`, `list_invoices`, `get_invoice`, `list_transactions`, `get_rate_card`, `list_time_entries(customer_id, project_id?, date_range)`, `get_active_contract(customer_id, as_of_date)`, `list_contracts(customer_id)` — each backed by a parameterized SQL query against Postgres inside its handler. Data access via `psycopg` (psycopg3) async + a single connection pool at startup; raw parameterized SQL in handlers (no ORM); Pydantic models for tool input/output (consistent with what OpenAI Agents SDK already uses for tool I/O). All tools are idempotent (read-only); this property is documented in `mcp_bank/README.md` since the OpenAIAgentsPlugin auto-retries activities and any future writable tool would have to maintain it. **No SQL or raw-query tool is exposed to the LLM**: the structured surface is required for grading functional accuracy against ground-truth tool invocations and for the tool-call history that `pre_action_proposal` rules consume. Smoke-tested via MCP inspector.

**Stage 4 — Temporal Workflow + OpenAI Agents Integration**
`workflows/send_invoice/` lands with: `workflow.py` defining `@workflow.defn class SendInvoiceWorkflow` whose `@workflow.run` method runs the agent loop via `Runner.run(agent, input=...)`, then awaits human approval via `workflow.wait_condition`, then invokes the side-effect and audit activities; `activities.py` with the small set of side-effect activities we write — `evaluate_policy`, `execute_send`, `audit_log` (the LLM calls and MCP tool calls become Temporal activities automatically via the plugin); `worker.py` registering the workflow + activities and the **`OpenAIAgentsPlugin`** on the Temporal client with `ModelActivityParameters(start_to_close_timeout=...)` and a **`StatefulMCPServerProvider(lambda: MCPServerStdio(name="bank", params=...), max_idle_connections=4)`** that wires the `bank` MCP into the agent's tool surface. (Stateful, not stateless: stateless spawns a fresh subprocess per MCP call, which compounded across the eval corpus produces thousands of process spawns per run — see §Eval cost. Stateful keeps a pool; `ApplicationError` on connection loss triggers a single reconnect retry in the activity layer.) The workflow references the MCP via `openai_agents.workflow.mcp_server("bank")` and passes it into `Agent(mcp_servers=[server], ...)`.

**Langfuse wiring is not free with the Temporal plugin.** The `OpenAIAgentsPlugin` propagates the OpenAI Agents SDK trace context across the Temporal activity boundary, but it does *not* itself ship a Langfuse integration. The actual wiring is: install `openinference-instrumentation-openai-agents`, point its OTLP exporter at the local Langfuse instance, register it as a `TracingProcessor` on agent setup. Workflow ID is exported as Langfuse trace ID via `workflow.info().workflow_id`. Confirm in the Langfuse UI that activity spans and LLM spans appear under the correct workflow trace before declaring this stage done.

Local Temporal dev server via `temporal server start-dev` for v0.1. **For v0.2 multi-day dispute workflows, switch to `temporal server start-dev --db-filename ./temporal.db`** so workflow state survives restarts; the default in-memory backend evaporates on Ctrl-C, which is fine for minutes-long send-invoice runs but loses days of dispute work.

**Four Temporal interop rules that are easy to get wrong** — make them explicit in `workflow.py` / `activities.py`:

1. **`execute_send` is a workflow-step activity, NOT exposed to the agent as `activity_as_tool`.** Exposing it would let the agent invoke side effects directly, bypassing the human-approval signal. The agent's tool surface is read-only (the `bank` MCP); side effects are called *by the workflow* after `workflow.wait_condition(approved)` resolves.
2. **Distinguish policy decisions from engine failures** (per §Policy Engine + Primitive Library Hard Rule 2). `PolicyDecisionError` → `ApplicationError(non_retryable=True)`. `PolicyEngineError` / `PolicyInfraError` → retryable. The naïve "all evaluate_policy exceptions are non-retryable" framing burns days of human work on a transient Postgres blip.
3. **`audit_log` writes are idempotent under activity replay** via the UNIQUE constraint on `(workflow_run_id, sequence_no)` from §Database. `sequence_no` is a deterministic monotonic counter inside the workflow; activity retries hit the same `sequence_no` and `ON CONFLICT DO NOTHING` makes the second write a no-op. **Critically, `event_kind` is NOT part of the UNIQUE key** — multiple rules can legitimately fire with `event_kind='rule_fired'` in the same phase; using `event_kind` in the key would silently drop the 2nd+ events.
4. **All MCP tools are idempotent.** The plugin auto-wraps each MCP call as a Temporal activity with default retries. A malformed model response that triggers a retry can re-execute already-completed tool calls. Read-only tools (the v0.1 `bank` surface) are safe by construction. Any future writable MCP tool MUST be idempotent (e.g., dedup by client-supplied request ID) or wrapped with `retry_policy=RetryPolicy(maximum_attempts=1)`. Document this in `mcp_bank/README.md` and re-verify whenever adding tools.

**Stage 5 — Policy Engine + Primitive Library**
`compass/` package created with `compass.policy` submodule. Public Python API: `Rule`, `Phase`, `Severity`, `Decision`, `Violation`, `PolicyDecisionError` / `PolicyEngineError` / `PolicyInfraError`, `evaluate(rules, phase, context)`, `attach_to_agent(agent, rules, ...)`, `evaluate_pre_action_proposal` / `evaluate_pre_execute` / `evaluate_audit_validation`, `@primitive` decorator + `list_primitives()`, `register_sink` for trace events. Engine is a single `evaluate(rules, phase, context) -> Decision` function (~80 LOC). Framework-core primitive families ship in the package; application-specific families (e.g., Billing integrity) live in `workflows/<workflow>/primitives.py` and self-register via `@primitive`. The send-invoice policy lives at `policies/send_invoice.py` as a typed `RULES: list[Rule]` (no YAML loader at v0.1 — see [§Design rationale](#design-rationale)). Each rule carries `regulatory_basis: tuple[str, ...]` for audit provenance — the engine denormalizes this into every `rule_fired` event's `payload` so audit rows are interpretable without joining back to source. The first call to `evaluate_policy` in a worker process writes a `policy_snapshots` row for `hash_rules(RULES)` (idempotent `INSERT ... ON CONFLICT DO NOTHING`, same transaction as the audit writes) — capturing the full serialized rule set keyed by hash makes 7-year-retention audit logs reconstructable after the code has changed. Approval / execute / decline activities populate `audit_log.actor` with the verified human identity (user_id, role, auth_method, mfa_verified) on every human-triggered event. `POLICY.md` drafted, auto-generating the primitive catalog from `list_primitives()` + docstrings.

**Stage 6 — Intent Classifier / Scope Gate**
Binary classifier (in-scope: send-invoice / out-of-scope) as the first activity in the workflow. Out-of-scope requests are rejected via an `input_validation`-phase policy rule and logged to the audit log with the original message + classifier output + confidence for product-iteration visibility. UI surfaces "Unsupported at the moment" with a brief rationale. Designed so v0.2 upgrades the classifier from binary to multi-class without changing the policy phase, eval suite, audit format, or gateway integration.

**Stage 7 — Eval Harness Core**
`compass.eval` submodule added to the `compass/` package. `WorkflowRunner` protocol + default `TemporalWorkflowRunner` (kicks off workflows via `client.execute_workflow(...)`). Reads workflow traces from Langfuse directly via the Langfuse SDK (no separate trace abstraction layer at v0.1). Per-case pass/fail and details are written as Langfuse Dataset Run scores on the per-item trace (one score per suite); Postgres holds only the harness-control `eval_runs` row (git SHA, mode, holdout justification, per-commit counter) — the things that need SQL enforcement and a typed CHECK constraint, which Langfuse `run_metadata` can't give us. Ships: functional accuracy suite, cost/latency (passthrough to Langfuse), and the trace-assertion library that other suites use for policy-compliance checks. **Does not ship a separate policy-compliance subsystem** — that's a ~30-line trace assertion library invoked by each per-suite test (see §Eval Framework). Scope-gate accuracy is a functional-accuracy axis (in-scope accepted, out-of-scope rejected). `--mode={train,holdout}` flag, `--holdout-justification` flag, per-commit holdout-run counter, and pre-flight cost-estimate vs. budget all wired from day one.

**Policy ablation eval (value-add measurement).** Ship a paired-runs suite that measures the marginal value of the policy engine: run the eval corpus twice — once with the policy gate enforced, once with it bypassed via `COMPASS_POLICY_DISABLE=1` (the env-var hatch wired in Stage 5). The hatch makes `evaluate_policy` short-circuit to `permit=True` and stamps an `audit_log.policy_hash` of `"disabled-for-eval"` so ablation runs are trivially queryable. Reported metrics:
- *True positive rate*: fraction of cases where policy ON rejected and ground truth says reject (the policy's raison d'être — non-zero is what justifies the framework).
- *False positive rate*: fraction of cases where policy ON rejected but ground truth says ship (over-tightening signal).
- *Lift*: `pass_rate(policy ON) − pass_rate(policy OFF)` on the same corpus.

This is a different measurement from policy compliance (did the right rules fire?) and policy coverage (what fraction of rule IDs fired?). Compliance and coverage measure correctness of the policy *given that it exists*; ablation measures the marginal value of *having the policy at all*. Both are needed: a high-compliance, high-coverage policy with low lift is suspect (the agent prompt is doing the work).

Future enhancement deferred to v0.3+: a 2×2 ablation over `prompt strictness × policy on/off` to disentangle prompt contribution from policy contribution. v0.1 ships the 1×2 (policy on/off, prompt fixed) — the simpler measurement is enough to validate "does the policy add value at all."

**Stage 8 — Promptfoo Integration**
Wire Promptfoo's red-team module into the harness. Custom banking-specific attack contexts. Dual scoring (Promptfoo grader + trace assertion on the expected policy rule firing). Failure-pattern classification. **Frozen adversarial corpus mechanism**: on first holdout invocation per release, Promptfoo's generated cases are persisted to `evals/adversarial/holdout_cases_<sha>.jsonl` and reused on subsequent holdout runs (Promptfoo's redteam is LLM-driven and not bit-deterministic; freezing the generated cases is what makes holdout numbers reproducible).

**Stage 9 — Counterfactual Perturbation Engine**
Perturbation library covering the dimensions in §Eval Framework §3 — explicitly excluding the name-perturbation class that was wrong for the domain. Each perturbation class carries an `INVARIANT` or `VARIANT` declaration so the scorer knows the expected direction. Run-N-variants harness with pinned temperature and the **noise-floor measurement protocol** (M re-runs of unperturbed case → reported as stability-above-floor). Stability score, sensitivity profile, reasoning-path divergence map with the pinned embedding model + threshold. Memo/contract paraphrase generation uses an LLM with a separate-judge meaning-preservation check; rewrites that fail the check are dropped. Methodology writeup drafted, including the noise-floor framing as the methodological contribution.

**Stage 10 — Policy Coverage Report + CI Gate**
Coverage report = the SQL query in §Eval Framework §5 + a small Python module that imports `policies/<workflow>.py` modules and enumerates `RULES` to compute the join. Surfaced as a dashboard panel. CI gate: build fails if any `Rule(must_be_covered=True)` has zero fires in the holdout run. All Billing integrity primitives carry this flag by default so criterion 8 is mechanically enforced. Total: ~50 LOC + a CI step, not a subsystem.

**Stage 11 — Workflow Polish + Eval Iteration**
Tune the agent prompt and policy rules **against the train split only** (`eval --mode=train`) until train pass rates clear the bar with headroom. Then run the holdout (`eval --mode=holdout --holdout-justification="release v0.1"`) to verify: **zero failed functional cases on the four-amount-source slice** (the released claim), Wilson 95% lower bound on overall functional pass rate ≥ 90%, Promptfoo holdout pass rate's lower bound ≥ 90%, counterfactual stability-above-floor median ≥ 0.9, scope-gate rejection lower bound ≥ 90%. The corpus is sized (~35 holdout cases) so a ≥ 90% lower bound corresponds to an observed rate around 96-100%. If holdout undershoots, that's an overfit signal — investigate root cause; do not iterate on holdout to close the gap. Workflow code changes during this stage that affect deterministic execution (signal handlers, activity stubs, control flow at `wait_condition`) must use `workflow.patched("v0.1.<n>")` to remain replay-safe for any in-flight workflow from earlier in the stage.

**Stage 12 — Next.js UI**
Chat, approval queue, policy viewer, eval dashboard (showing all suite categories + coverage), audit log replay (including unsupported-request entries with original message + classifier output). Embedded Langfuse for raw LLM traces; Temporal UI for workflow execution.

**Stage 13 — Documentation + Writeups**
Final README. `ARCHITECTURE.md`. `POLICY_DSL.md` polished. `COUNTERFACTUAL_EVAL.md` published as standalone artifact. `WORKFLOW_AWARE_EVAL.md` for the workflow-trace-aware evaluation framing.

### v0.2 (proves reusability)

**Stage 14 — Dispute Data Generation**
Add `config/disputes.yaml` and extend the existing data generator so `synthetic_account_1/generated/bank/disputes.jsonl` contains ~40 historical disputes (the v0.1 scope was raised from 10–15 to 40 to support the v0.2 corpus). Ground truth labels for ~90 dispute cases split into `ground_truth/train/dispute_outcome_labels.jsonl` (~63) and `ground_truth/holdout/dispute_outcome_labels.jsonl` (~27). Cases are derived from the historical disputes plus synthesized variants — the derivation script is in `synthetic_account_1/generate_dispute_cases.py`, deterministic from the same seed. Switch to persistent Temporal (`temporal server start-dev --db-filename ./temporal.db`) before any multi-day workflow run; verify workflow state survives a `kill -9` restart.

**Stage 15 — `DisputeInvestigationWorkflow`**
`workflows/dispute_investigation/` lands with `workflow.py`, `activities.py`, `worker.py`, `types.py` — mirroring the v0.1 send-invoice layout (snake_case, not camelCase). The LLM reasoning steps (parse dispute, gather evidence via MCP tool calls, analyze patterns, draft case summary) live **inside** `Runner.run(case_agent, ...)` as auto-activities via `OpenAIAgentsPlugin` — same architectural commitment as v0.1, no reversion to bespoke per-step activities. The hand-written activities are the side-effect / boundary ones only: `evaluate_policy`, `await_decision` (via `workflow.wait_condition`), `execute_outcome` (refund / deny side effect), `audit_log`. Reuses the `bank` MCP unchanged, the policy engine, the Langfuse wiring. Multi-step trace structure comes from the agent loop, not from workflow-level decomposition.

**Stage 16 — Intent Classifier → Multi-class Router**
Upgrade the v0.1 binary scope gate to a multi-class router (send-invoice / dispute / out-of-scope). Same policy phase, same eval suite structure, same audit format, same gateway integration. Track what changed vs. what was reused.

**Stage 17 — Dispute Policy Composition**
`policies/dispute_investigation.py` exports `RULES: list[Rule]` using existing primitives. Reuse measurement is direct: `git grep` over imports in `policies/dispute_investigation.py` enumerates reused primitives. Track which primitives are reused vs. newly added. Goal: zero new primitive types. Report reuse ratio per §v0.2 success criteria, plus the count of v0.1 primitives left unused (the leading indicator of v0.1 over-build).

**Stage 18 — Dispute Eval Suites**
Functional accuracy and counterfactual perturbation corpora for disputes. Promptfoo redteam config tuned for dispute attacks (with the same frozen-cases mechanism as Stage 8). Reuses all engine code.

**Stage 19 — Trace Coherence Evaluators (Langfuse-native)**
Author three Langfuse `Evaluator` definitions (logical consistency, memory persistence, reasoning quality) targeting dispute workflow traces. Use Langfuse's LLM-as-judge runtime — no new Compass subsystem. Apply the judge controls from §Eval Framework §7: pinned judge model, self-consistency (3-vote majority), anti-self-preference (judge family ≠ agent family). The Compass-side work in this stage is ~50 LOC: a wrapper that runs the Langfuse evaluator against the per-test trace and includes the result in the eval report. Output is a coherence score per test plus high-variance flags for human review.

**Stage 20 — UI Extension**
Case view, evidence presentation, multi-actor approval threads in the existing UI. Approval UI surfaces `policy_drift_after_confirmation` notices when v2 policy would change a v1-approved decision (per §Policy Engine + Primitive Library Hard Rule 3).

**Stage 21 — Reusability Writeup + v0.2 Documentation**
Publish: reuse ratio (per the v0.2-pinned formula), count of v0.1 primitives unused, list of public-API gaps surfaced and how each was closed, list of leaked primitives (per the leak definition in §v0.2 success criteria). Numbers come from CI artifacts emitted during Stages 17 and 18, not from a manual count.

---

## Publishing Plan


**1. Counterfactual evaluation for agentic financial workflows** *— ships during Stage 9.* Methodology, perturbation dimensions (including the explicit domain-driven exclusion of name perturbations), the noise-floor-relative stability-above-floor metric, reasoning-path divergence with the pinned embedding-similarity comparison, baseline results on send-invoice. The methodological contribution is the noise-floor-relative framing.

**2. Workflow-aware evaluation architecture** *— ships during Stage 13.* Explains why eval should operate on Temporal workflow traces (not just LLM call traces), how policy enforcement at phase boundaries differs from output filtering, and the integration patterns with Langfuse and Promptfoo. Includes the design rationale for keeping policy-compliance and coverage as trace assertions over structured policy-fire events, not parallel subsystems.

**3. A pragmatic policy vocabulary for agentic AI in fintech** *— ships during Stage 13.* Design rationale (why a typed Python rule library is the right choice at N=2 workflows and why a YAML DSL was considered and rejected — see the rubric in [§Design rationale](#design-rationale)), what hooks already exist in the OpenAI Agents SDK and what Compass adds on top, the realistic severity model (escalate only at workflow-level phases — OpenAI Agents SDK guardrails are tripwire by design), the policy-drift behavior across multi-day approvals, primitive catalog, example compositions for send-invoice.

**4. Reusability validated — adding a second workflow without changing the framework** *— ships at the end of v0.2.* The reusability proof, with the dispute workflow as the test. Numbers from CI artifacts: reuse ratio per the pinned formula, count of v0.1 primitives unused, list of leaked primitives per the leak definition, list of public-API gaps surfaced and how each was closed. Includes explicit reporting of where the abstraction leaked.

---

## Validation Criteria for v0.1 Completion

1. Synthetic Account 1 data — including contracts (pre-structured), time tracking, rate cards — generates deterministically from a single seed and passes `verify.py`; `load_to_postgres.py` produces an identical DB state on re-run for **bank-data tables only** (`audit_log` and `eval_runs` survive across reloads)
2. The `bank` MCP responds correctly across all v0.1 tools (`list_customers`, `get_customer`, `list_invoices`, `get_invoice`, `list_transactions`, `get_rate_card`, `list_time_entries`, `get_active_contract`, `list_contracts`); no raw-SQL or query-builder tool is exposed; all tools documented as idempotent in `mcp_bank/README.md`
3. The `SendInvoiceWorkflow` runs end-to-end on Temporal, with each activity traced in Langfuse via the `openinference-instrumentation-openai-agents` OTLP exporter and one trace per workflow ID with child spans per activity + LLM observation
4. Policy engine fires correctly on each test scenario; trace assertions verify the expected `rule_fired` event set per case; the **Billing integrity** family (`require_amount_source`, `contract_consistency_check`, `prohibit_exceed_contract_cap`, `currency_consistency_check`) is exercised by the corpus; every fired rule's `regulatory_basis` is denormalized into the `audit_log.payload`
5. **Audit interpretability + actor attribution.** Every `policy_hash` referenced by an audit_log row resolves to a `policy_snapshots` row whose `rules_json` reconstructs the rule set as it was at that evaluation (verified by a CI test that asserts every distinct hash in audit_log has a matching snapshot). Every audit_log row for a human-triggered event (`approval_signal`, `executed`, `declined`) has a non-NULL `actor` with `{user_id, role, auth_method, mfa_verified}`; the approval UI surfaces these fields in the audit-trail view
6. Intent classifier accepts in-scope send-invoice requests and rejects out-of-scope requests with a logged audit entry. **Released claim:** zero failed scope-gate cases on the holdout (including paraphrase perturbations); Wilson 95% lower bound on rejection accuracy ≥ 90%
7. **Measured on the holdout split:** zero failed functional cases across the four amount-source types (contract-derived, rate-card × time-tracking, rate-card flat, user-specified) — this is the released claim, not a percentage; Wilson 95% lower bound on overall functional pass rate ≥ 90%; Promptfoo adversarial pass-rate lower bound ≥ 90%; counterfactual stability-above-floor median ≥ 0.9 (per §Eval Framework §3 — the noise-floor-relative metric, not raw stability). All prompt and rule iteration during Stage 11 happened against the train split only, with the harness's `--mode` flag enforcing the separation
8. Every line item in approved invoices carries a verifiable `source_type` + `source_refs` + `computation` triple; the approval UI surfaces this audit trail to the human reviewer
9. Policy coverage report shows which rules are exercised by the corpus; coverage gaps documented; **CI gate** fails the build if any `Rule(must_be_covered=True)` has zero fires on the holdout run — all Billing integrity primitives carry this flag, so dead-code regressions in that family are mechanically blocked, not just dashboard-flagged
10. The Next.js UI runs locally with all surfaces working; deep-linking to Langfuse and Temporal UIs works; the audit-log surface shows scope-gate rejections (with original message + classifier output) and policy-rejected proposals
11. The counterfactual perturbation methodology writeup is published, including the noise-floor-relative metric framing and the documented exclusion of name-perturbation classes for the fintech domain
12. The `compass` package is pip-installable from this repo with a documented public API (`compass.policy`, `compass.eval`); the dependency-direction CI check (covering both `from X import Y` and `import X` forms) passes — no imports from `workflows/`, `synthetic_account_1/`, or `mcp_bank/` inside `compass/`
13. A full holdout pass (functional + adversarial + counterfactual + coverage) completes within the per-release-run cost budget of $40; the harness blocks holdout invocation if estimated cost exceeds budget
14. README explains the framework, the workflow, and the reusability claim well enough that a fintech engineer reading for 10 minutes understands the thesis, what's built, and what v0.2 will validate

## Validation Criteria for v0.2 Completion

15. `DisputeInvestigationWorkflow` runs end-to-end with the same gateway, policy engine, Langfuse integration as v0.1, on persistent Temporal (`--db-filename`); verified by a `kill -9` restart mid-`wait_condition` resuming the workflow on the same approval state
16. The intent classifier extends from binary to multi-class without changing the policy phase, eval suite, audit format, or gateway integration
17. `policies/dispute_investigation.py` introduces zero new primitive types (target); reuse ratio ≥ 80% per the §v0.2 success criteria formula (`primitives_reused_from_v01 / total_primitive_uses`) — including Billing integrity primitives if applicable to dispute outcomes (refund amounts must trace to a source)
18. **Dispute investigation consumes Compass via the public API only**: the dependency-direction CI check remains green throughout v0.2; any need to reach into Compass internals is logged as a public-API gap and closed by extending the public API, not by privileged access
19. All v0.1 eval categories applicable to dispute workflow; trace coherence evaluators authored as Langfuse-native `Evaluator` definitions, not a Compass subsystem
20. Reusability writeup published with concrete numbers from CI artifacts (not manual count): reuse ratio with the formula spelled out, count of v0.1 primitives unused, list of public-API gaps surfaced and how each was closed, list of leaked primitives per the leak definition

---

## Design rationale

Why the library is typed Python — and why a YAML DSL was considered and rejected at v0.1's N=2-workflow scope.

### The YAML-vs-Python rubric (applied at N=2)

A YAML rule DSL was in the original draft. Applying a rubric against this project's actual scope:

| Criterion | Verdict | Reason |
|---|---|---|
| Authorship | **Python** | Engineer-authored + PR-reviewed. No non-engineer authoring use case named. |
| Deployment cadence | **Python** | Rules change with code; no hot-reload-without-restart requirement. |
| Multi-tenancy | **Python** | Explicitly out of scope. |
| Vocabulary breadth | **Python** | ~15–20 primitives × 2 workflows: import noise is negligible. |
| Stable rule IDs | Tie | `id: str` field works identically in YAML and Python. |
| Composition | Tie | No `and/or/not` at v0.1 either way. |
| PR-review reasoning | Slight **Python** | Python diff additionally type-checks; YAML diff doesn't. |
| Cross-language portability | **Python** | UI reads `audit_log`, not policy files. Nothing non-Python needs the source. |
| Adopter onboarding | Slight **Python** | An adopter is already running OpenAI Agents SDK = Python-native. |
| Build cost | **Python (~10×)** | YAML adds parser + schema + error types + tests (hundreds of LOC). Python is a `@dataclass` + a 20-line dispatcher. |
| Cost of overbuilding (if YAML weren't needed) | **Python** | YAML adds context-switch tax, parser debug surface, refactoring boundary. |
| Cost of underbuilding (if YAML turned out to be needed) | **Python** | Migration Python→YAML is mechanical: serialize the typed list. The engine + primitives don't change. Additive, non-breaking. |

Python wins or ties every criterion. The criteria where YAML would win — non-engineer authoring, multi-tenancy, hot-reload, cross-language consumers — are all out of scope. Verdict: typed Python `RULES: list[Rule]` at v0.1; YAML loader is additive when a real adopter forces it.


### Adjacent declarative-hook systems

Brief survey of how other systems landed and what Compass takes from each:

| System | Form | Lesson for Compass |
|---|---|---|
| OpenAI Agents SDK guardrails / lifecycle hooks | Decorators returning binary tripwire; observe-only lifecycle | Substrate for OpenAI Agents SDK-bound phases. Binary tripwire is why `escalate` collapses for OpenAI Agents SDK-bound phases. |
| Pytest fixtures + `conftest.py` | Decorator marks intent; file location is the binding; hierarchical override; no YAML | Decorator + convention beats config file for the lightweight feel. Adopted for app-specific primitives via `@compass.policy.primitive(...)`. |
| Spring AOP / AspectJ pointcut DSL | Text-pattern matching against method signatures | Survived only as a narrow subset; full AspectJ killed by pointcut fragility (silent stop-matching after refactors). Confirms: **no text-pattern phase binding**, use named string constants (the `Phase` `StrEnum`). |
| Express / Django / Rails middleware | Ordered list of callables; no phases, no predicates | The smallest possible declarative form. Compass's `list[Rule]` is the same pattern, plus phase tag. |
| OPA / Sentinel / Conftest | Code (Rego/Sentinel) policies + declarative binding | Separate policy *logic* (primitive Python) from policy-to-phase *binding* (the `Rule` dataclass). Compass adopts this split. |
| TruLens feedback functions | Composable Python builder; selectors pick what the function evaluates | Pure-Python composition over YAML. Validates the typed-Python direction at this scope. |
| DSPy `Assert` / `Suggest` | Inline Python assertions; failure message feeds retry context | Hard-vs-soft maps onto BLOCK-vs-ESCALATE. The retry-on-fail idea is **not** adopted — auto-retry on policy fail would mask issues in a fintech context. |
| LangGraph conditional edges | Pure-function router; explicitly forbids side effects | Confirms: gating predicates must be pure. Matches Hard Rule 1 in §Policy Engine + Primitive Library. |
| Anthropic `tool_choice` | Tiny declarative form: `{type, name?}` | Could inform an `allowed_tools` primitive if per-tool gating ever lands. Not built today. |
| JSONLogic | JSON-encoded boolean predicates | Determinism wins; ergonomics fail beyond trivial predicates. Useful pattern only for simple guard clauses. |
| Helm-style Jinja-in-YAML | Embedded templating in config | Universally regretted. Confirms: **the moment users want conditionals or loops in YAML, you've reinvented Helm.** A YAML loader added later would stay declarative-bindings + simple-predicates only. |
| NeMo Guardrails (Colang) | Bespoke conversational flow DSL | Heavy. Conversation-flow oriented, not phase-keyed action policy. Skipped. |
| Invariant Labs guardrails | Python-inspired rule DSL for MCP/LLM proxy | Closest semantic match. Compass is in-process not proxy-based; the design overlap is intentional. |

**Patterns Compass adopts**: middleware-style ordered list of rules (Python `list[Rule]`); decorator-based registration for app-specific primitives (`@primitive`); convention-over-config file locations (`policies/<workflow>.py`); separate engine errors from engine decisions per DSPy; pure predicates per LangGraph; structured trace events per OPA.

**Patterns Compass avoids**: text-pattern pointcut DSLs (AspectJ fragility); Jinja-in-YAML or any flow-language embedded in config (Helm regret); JSONLogic for anything beyond trivial guards; auto-retry-on-policy-fail (fintech context); per-rule custom Python at the binding level (kills the "uniform vocabulary" value prop — if rules are arbitrary Python, you've just got OpenAI Agents SDK guardrails again).

---