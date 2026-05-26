# Pre-Production Considerations

Gaps surfaced against `build-plan.md`. Out of scope for v0.1/v0.2 demo deliverables, but each one will hit before real customer traffic. Ranked roughly by what breaks first under load.

---

## High-impact

### 1. PII in the trace pipeline

Langfuse captures full LLM inputs/outputs: customer names, amounts, contract text, freeform memos. At v0.1 it's a local Docker sidecar; production means hosted or self-hosted with retention, access controls, and field-level redaction at the OTLP exporter — *before* data leaves the network. The 7+ year `audit_log.payload` JSONB has the same exposure.

Right-to-erasure (GDPR/CCPA) collides with WORM-equivalent audit retention; the conflict needs an explicit policy, not a runtime surprise. Redaction discipline does not appear anywhere in v0.1 today.

### 2. Tenant/account scoping in the MCP, not just in prompts

The bank MCP runs parameterized SQL but no query in the plan is scoped by `tenant_id` / `account_id` at the SQL layer. With one synthetic account this never surfaces. With two, a malformed tool call or prompt-injection that flips a customer ID lets the agent read across tenants.

Policy rules at `pre_action_proposal` catch the wrong action. They cannot catch the wrong *read* that informed a plausible-looking right action. Tenant scoping has to be a non-bypassable layer below the MCP handlers (request-scoped DB role, RLS, or scoped connection per workflow run).

### 3. Cost governance is deferred but bites first

`tool_call_budget` / `model_call_budget` / `latency_budget` are listed for v1.0+. A single jailbreak loop or unhandled retry storm can burn a day's LLM budget in minutes. OpenRouter rate-limit responses translate into cascading Temporal retries against an upstream already throttling.

Required: per-workflow + per-tenant + per-day budgets, circuit-breakers on 429s, explicit non-retryable on quota errors.

### 4. Approval-signal authenticity

`actor` JSONB and `dual_control_above_threshold` cover the *record*. Nothing covers the *channel*. `workflow.wait_condition(approved)` accepts whatever signal Temporal hands it. Production approvals arrive via mobile push, email links, chat — each spoofable.

The signal-handling activity needs to verify a signed approval token (issued at proposal time, bound to proposal hash + approver), reject replays, and record auth method + MFA assertion in `actor`. The schema already has the column; the verification path doesn't exist.

### 5. "What the human saw at approval time" is not captured

Plan freezes proposal hash and policy hash. It does not freeze the *rendered explanation* shown in the approval UI. If a customer later disputes — "the agent didn't tell me X" — there is no tamper-evident artifact of the exact text/UI presented at approval.

Capture: hash + store the rendered approval payload (and the model+prompt artifacts behind it; see #6) alongside the `approval_signal` audit row. Under agentic UX, liability often does not follow approval if the disclosure was misleading.

### 6. Model and prompt artifact reproducibility, paralleling `policy_snapshots`

`policy_snapshots` solves "what rules fired five years ago." Nothing solves "what model + system prompt + tool descriptions were in force when this rule fired." OpenRouter routes can change behind a stable model name; providers deprecate.

To reconstruct a decision: snapshot model ID + provider routing + system prompt + agent definitions + MCP tool schemas, hashed, FK'd from `audit_log` the same way `policy_hash` is. Same write-on-first-use, same retention.

### 7. Workflow versioning across in-flight executions

Plan acknowledges no migration framework at v0.1. v0.2 disputes run multi-day; workers will be redeployed mid-flight. Temporal's Versioning API has to be wired in or replays break the first day after deploy.

Same concern for `InvoiceProposal` Pydantic schema evolution and for `audit_log` / `policy_snapshots` DDL changes against a populated audit table.

### 8. Real-time external dependencies

Hard Rule 1 says external facts (KYC freshness, sanctions, fraud) get loaded by a pre-loop activity. Behavior under provider outage is undefined: fail closed and block sends until OFAC is reachable? Fail open with a flag? Degrade to cached-with-staleness?

Each path needs an explicit primitive + SLO. `require_field_recency` is the right shape but does not distinguish "lookup returned stale data" from "lookup returned nothing because the provider is down" — different failure modes, different correct responses.

---

## Secondary

### 9. Eval corpus staleness

120 cases will go stale once real customer phrasings show up. Need a privacy-filtered production-trace-to-corpus pipeline; otherwise the >95% holdout claim decays silently. Same redaction infrastructure as #1.

### 10. Audit-log integrity

`BIGSERIAL` + Postgres rows are *trusted*, not *provable*. Regulators increasingly ask whether engineering can quietly delete or amend an audit row. A hash chain over `(prev_hash, sequence_no, payload)` or an append-only WORM store under `audit_log` answers this without a separate ledger product.

### 11. LLM-judge calibration drift

v0.2 leans on Langfuse LLM-as-judge for trace coherence. Pinning the judge model helps; provider-side fine-tuning still shifts scoring over months. Need a periodic recalibration against a small human-graded panel and an alert when judge-vs-human agreement crosses a threshold.

### 12. Customer-facing explainability

`regulatory_basis` + `rule_fired` events are the raw material for "why was this rejected." There is no surface that turns them into a customer-safe narrative. The constraint: explain enough to be useful, not enough to let an adversary map the rule surface. Needs an explicit redaction layer between audit and customer comms.
