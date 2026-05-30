# Compass Policy Engine — Visual Reference

Companion to `docs/build-plan.md §Policy Engine + Primitive Library` and
`docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md`.
This file is diagrams + plain-English explanations of the moving parts.

---

## 1. The end-to-end flow

```
┌───────────────────────────────────────────────────────────────────────────┐
│  SendInvoiceWorkflow.run    (Temporal workflow code — deterministic)       │
│                                                                            │
│  user message                                                              │
│      │                                                                     │
│      ▼                                                                     │
│  await Runner.run(agent, ...)        ◀── plugin auto-wraps each LLM call   │
│      │                                   and each MCP tool call as a       │
│      ▼                                   Temporal activity                 │
│  RunResult                                                                 │
│      │                                                                     │
│      ▼                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │ workflows/send_invoice/context.py  (pure functions, no I/O)         │  │
│  │   extract_tool_calls(run_result)         ─┐                         │  │
│  │   project_resolved_entities(tool_calls)   ├─▶ context: dict[str,…]  │  │
│  │   extract_reasoning_text(run_result)      │                         │  │
│  │   hash_proposal(proposal)  → proposal_hash┘                         │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│      │                                                                     │
│      ▼                                                                     │
│  workflow.execute_activity(evaluate_policy, EvaluatePolicyInput(            │
│      phase=Phase.pre_action_proposal,                                      │
│      context=ctx,                                                          │
│      starting_sequence_no=N,                                               │
│  ))                                                                        │
└───────────────────┬───────────────────────────────────────────────────────┘
                    │   (activity boundary; below is allowed to do I/O)
                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│  evaluate_policy activity body    (workflows/send_invoice/activities.py)   │
│                                                                            │
│  async with psycopg connection (one transaction):                          │
│                                                                            │
│    ┌───────────────────────────────────────────────────────────────────┐  │
│    │ compass.policy.snapshot.write_policy_snapshot(conn, ws, RULES)    │  │
│    │   ─▶ INSERT INTO policy_snapshots (policy_hash, workflow,         │  │
│    │       rules_json) ON CONFLICT DO NOTHING                          │  │
│    │   ─▶ returns policy_hash                                          │  │
│    └───────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│    sink = AuditLogSink(conn, workflow_run_id, allocator, policy_hash)      │
│                                                                            │
│    ┌───────────────────────────────────────────────────────────────────┐  │
│    │ decision = await compass.policy.evaluate(                         │  │
│    │     RULES, phase, context, sink=sink                              │  │
│    │ )                                                                  │  │
│    │                                                                    │  │
│    │   for rule in RULES:                                              │  │
│    │       if rule.phase != phase:  continue                           │  │
│    │       v = await rule.predicate(context)                           │  │
│    │       if v is None:                                                │  │
│    │           sink.emit({"event_kind": "rule_skipped", ...})  ─▶ DB   │  │
│    │       else:                                                        │  │
│    │           sink.emit({"event_kind": "rule_fired", evidence: v.…})  │  │
│    │           bucket by rule.severity:                                │  │
│    │             BLOCK    → violations[]                               │  │
│    │             ESCALATE → escalations[]                              │  │
│    │   return Decision(permit, violations, escalations, ids_fired)     │  │
│    └───────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│    if not decision.permit:                                                 │
│        raise PolicyDecisionError(...)                                      │
│                ─▶ at the boundary: ApplicationError(non_retryable=True)    │
│    conn.commit()                                                           │
│  return PolicyDecisionPayload(                                             │
│      policy_hash, rule_ids_fired, escalations, next_sequence_no            │
│  )                                                                         │
└───────────────────┬───────────────────────────────────────────────────────┘
                    │
                    ▼
              back to workflow
       (stores policy_hash + advances seq counter)
```

The `evaluate_policy` activity is invoked **three times** during one
workflow run — once per workflow-level phase:

```
   evaluate_policy #1            evaluate_policy #2            evaluate_policy #3
   phase=input_validation        phase=pre_action_proposal     phase=pre_execute
   (after scope gate)            (after main agent)            (after approval)
        │                             │                             │
   permit ▶ run main agent       permit ▶ await approval        permit ▶ execute_send
        │                             │                             │
   block  ▶ audit unsupported    block ▶ audit reject           block ▶ audit reject
        │                             │                             │
        ▼                             ▼                             ▼
       END                           END                           END
```

`audit_validation` fires a fourth time inside the **audit_log** activity
for the terminal row (executed / declined / timeout / unsupported /
policy_rejected) — same `evaluate(...)` core, different phase.

---

## 2. The five phases

| Phase | Fires at | Mechanism | Context contains | v0.1 rule count |
|---|---|---|---|---|
| `input_validation` | After the scope-gate `Runner.run`, before the main agent | Explicit Temporal `evaluate_policy` activity (first call) | `user_message`, `classification` (scope-gate output), `workflow_run_id` | **1** — `intent_must_be_send_invoice` |
| `output_validation` | On the agent's structured Pydantic output | OpenAI Agents SDK `@output_guardrail` hook (`attach_to_agent`) — defined but **not wired** into the workflow today | `proposal` | 0 — Pydantic already validates structure |
| `pre_action_proposal` | After the main `Runner.run` returns, before human approval wait | Explicit Temporal `evaluate_policy` activity (second call) | `proposal`, `resolved_entities`, `tool_calls`, `reasoning_text`, `workflow_run_id` | **9** — bulk of policy load |
| `pre_execute` | After approval signal, before `execute_send` | Explicit Temporal `evaluate_policy` activity (third call) | …pre_action_proposal context… plus `approval`, `proposal_hash_at_proposal`, `policy_hash_at_proposal` | **2** — drift detection |
| `audit_validation` | Inside `audit_log` activity, before terminal-row INSERT | Inline `evaluate_audit_validation()` in the audit_log activity body | `audit_entry_candidate`, `policy_hash`, `tool_calls`, `reasoning_text` | **2** — completeness check |

Visualized along the workflow's timeline:

```
                                                                ◀── time ──▶
   ┌──────┐  ┌────────────────────┐  ┌────────────────┐  ┌───────────────┐  ┌──────────────┐
   │ user │─▶│ Runner.run         │─▶│ evaluate_policy│─▶│ wait for      │─▶│ evaluate_     │
   │ msg  │  │  ┌──────────────┐  │  │ pre_action_    │  │ approval      │  │ policy        │
   └──────┘  │  │input_guardr. │  │  │ proposal       │  │ signal        │  │ pre_execute   │
             │  │   ↑ phase 1  │  │  │   ↑ phase 3    │  │  (minutes-    │  │   ↑ phase 4   │
             │  └──────────────┘  │  └────────────────┘  │   hours)      │  └───────┬───────┘
             │       │            │                      └───────────────┘          │
             │       ▼            │                                                 ▼
             │  ┌──────────────┐  │                                          ┌────────────┐
             │  │MCP tool calls│  │                                          │ execute_   │
             │  │(auto-activity)│ │                                          │ send       │
             │  └──────────────┘  │                                          └─────┬──────┘
             │       │            │                                                │
             │       ▼            │                                                ▼
             │  ┌──────────────┐  │                                          ┌────────────┐
             │  │output_guardr.│  │                                          │ audit_log  │
             │  │   ↑ phase 2  │  │                                          │   ↑ phase 5│
             │  └──────────────┘  │                                          │  (terminal)│
             └────────────────────┘                                          └────────────┘
```

Why pre_execute even exists when pre_action_proposal could re-run: at
pre_action_proposal time, three things don't exist yet — (a) a confirmed
proposal to compare against, (b) a `policy_hash_at_proposal` to compare
against now, (c) the approver's identity from the signal. Pre_execute is
the only place rules that depend on those can fire.

---

## 3. The Predicate type

```
type PredicateFn = Callable[
    [Mapping[str, Any]],                              ◀── input:  context dict
    Awaitable[Violation | None] | Violation | None    ◀── output: maybe-violation,
                                                              sync or async
]
```

In plain English, a `PredicateFn` is **a function that takes the context
dict and returns either a `Violation` (the rule fired) or `None` (the
rule didn't fire). It may be sync or async.**

```
        context dict                                  Violation | None
       (proposal, etc.)
              │                                              ▲
              ▼                                              │
   ╔══════════════════════════════════════════════════════════╗
   ║              predicate fn body                           ║
   ║                                                          ║
   ║   value = resolve_dotted(ctx, "proposal.total_cents")   ║
   ║   if value > max_allowed:                                ║
   ║       return Violation(rule_id="",                      ║
   ║                        message="amount too high",      ║
   ║                        evidence={"value": value})       ║
   ║   return None                                            ║
   ╚══════════════════════════════════════════════════════════╝
```

The `Predicate` dataclass wraps that function with metadata:

```
              Predicate (frozen dataclass)
   ┌──────────────────────────────────────────────────────┐
   │                                                       │
   │  primitive_name: "numeric_threshold"  ◀── registry key │
   │  params:         {"field": "proposal.total_cents",    │
   │                   "max":   10_000_000}                │
   │                          ◀── frozen kwargs;            │
   │                              hash_rules serializes them│
   │  fn:             <function check at 0x…>              │
   │                          ◀── the actual PredicateFn    │
   │                                                       │
   │  __call__(ctx) ─▶ awaits fn(ctx) if needed            │
   │                  ─▶ returns Violation | None          │
   └──────────────────────────────────────────────────────┘
```

Why a wrapper class and not just a bare function?

- `hash_rules` needs `primitive_name` + frozen `params` to serialize the
  rule set canonically. Two `numeric_threshold` instances with different
  `max` values must hash differently.
- `list_primitives()` needs the name to enumerate the catalogue for
  Stage 10's coverage report and v0.2's reuse-ratio metric.
- Bare lambdas can't reliably carry those attributes.

Async vs sync:

```
   sync predicate (the common case)           async predicate (LLM-judge)
   ─────────────────────────────────          ───────────────────────────
   def check(ctx):                            async def check(ctx):
       v = ctx["proposal"]["total"]               result = await Runner.run(
       if v > 10:                                     judge_agent, input=ctx
           return Violation(...)                  )
       return None                                if result.flagged:
                                                      return Violation(...)
                                                  return None

   Returns Violation | None directly.        Returns Awaitable[Violation | None].
   Predicate.__call__ runs it inline.        Predicate.__call__ awaits it.
```

Stage 5 ships only sync predicates. The async path exists so
`prompt_injection_detected` (Stage 8) and dispute LLM judges (v0.2) can
land without changing the engine.

---

## 4. Primitives — what they are, how registration works

A **primitive** is a re-usable rule template: a factory function that
produces a `Predicate` for a specific configuration.

```
                      ┌──────────────────────────────────────┐
                      │       @primitive("numeric_threshold")│
                      └────────────┬─────────────────────────┘
                                   │ wraps the factory
                                   ▼
   def numeric_threshold(*, field, max=None, min=None):  ◀── kwargs only
       def check(ctx):                                       (so hash is stable)
           value = resolve_dotted(ctx, field)
           if max is not None and value > max:
               return Violation(rule_id="", message=…, evidence=…)
           if min is not None and value < min:
               return Violation(rule_id="", message=…, evidence=…)
           return None
       return check                                       ◀── the PredicateFn

   # Calling the decorated factory:
   pred = numeric_threshold(field="proposal.total_cents", max=10_000_000)

   # What you get back:
   pred ─▶ Predicate(
              primitive_name = "numeric_threshold",   ◀── from @primitive(…)
              params         = {field: "proposal.total_cents",
                                max:   10_000_000},  ◀── frozen kwargs
              fn             = check                  ◀── the closure above
           )

   # The registry now contains:
   _REGISTRY["numeric_threshold"] = the wrapped factory
   list_primitives()  ─▶ {"numeric_threshold": …, …}
```

A **Rule** binds a configured predicate to an identity:

```
   Rule(
       id="invoice_amount_cap",                  ◀── stable handle in audit_log
       phase=Phase.pre_action_proposal,          ◀── when to fire
       predicate=numeric_threshold(              ◀── configured Predicate
           field="proposal.total_cents",
           max=10_000_000,
       ),
       severity=Severity.ESCALATE,               ◀── BLOCK or ESCALATE
       regulatory_basis=("internal SOP-BILL-04",), ◀── denormalized to audit
       tags=("amount_threshold",),               ◀── coverage report grouping
       must_be_covered=True,                     ◀── Stage-10 CI gate flag
   )
```

### Two flavors of primitive

```
   ┌───────────────────────────────────────────────────────────────────┐
   │  Framework-core primitives                                         │
   │  ────────────────────────                                          │
   │  Live in compass/policy/primitives/                                │
   │  Generic across workflows; ship with the framework                 │
   │                                                                    │
   │    numeric_threshold                                               │
   │    entity_status_equals                                            │
   │    require_existing_entity                                         │
   │    require_evidence_citation                                       │
   │    intent_in_allowlist                                             │
   │    prohibit_silent_modification_after_confirmation                 │
   │    prohibit_policy_drift_after_confirmation                        │
   │    log_policy_version                                              │
   │    log_data_sources_consulted                                      │
   └───────────────────────────────────────────────────────────────────┘

   ┌───────────────────────────────────────────────────────────────────┐
   │  Application-specific primitives                                   │
   │  ────────────────────────────                                      │
   │  Live next to the workflow that uses them                          │
   │  Self-register via @primitive on import                            │
   │                                                                    │
   │    workflows/send_invoice/primitives.py:                           │
   │      require_amount_source                                         │
   │      require_contract_exists                                       │
   │      contract_consistency_check                                    │
   │      prohibit_exceed_contract_cap                                  │
   │      currency_consistency_check                                    │
   └───────────────────────────────────────────────────────────────────┘

   Registration trigger chain:
       policies/send_invoice.py
         imports workflows.send_invoice.primitives
           @primitive("require_amount_source") fires at import
              ─▶ _REGISTRY updated
```

---

## 5. Lifecycle of a single rule firing

```
Step 1.  policies/send_invoice.py declares a Rule:

         Rule(id="require_evidence_citation",
              phase=Phase.pre_action_proposal,
              predicate=require_evidence_citation(
                  field="proposal.line_items[*].source_refs"
              ),
              regulatory_basis=("internal SOP-BILL-02",),
              must_be_covered=True,
              tags=("billing_integrity","evidence"))

Step 2.  evaluate_policy activity runs at pre_action_proposal:

         await evaluate(RULES, Phase.pre_action_proposal, ctx, sink=sink)

Step 3.  Engine reaches this rule; ctx looks like:

         { "proposal": {
              "line_items": [
                 {"description":"…","source_refs":["te_001"], …},
                 {"description":"…","source_refs":[],          …},  ◀── BAD
              ], …
           }, "resolved_entities": {…}, "tool_calls":[…] }

Step 4.  Predicate runs (synchronous):

         refs_per_line = resolve_dotted(
             ctx, "proposal.line_items[*].source_refs"
         )
         # ─▶ [["te_001"], []]
         for i, refs in enumerate(refs_per_line):
             if not refs:
                 return Violation(rule_id="",
                                  message=f"line {i} missing refs",
                                  evidence={"line_no": i})
         # falls through with i=1, refs=[]; returns Violation

Step 5.  Engine fills in rule_id; buckets by severity (BLOCK default):

         violations = [
             Violation(rule_id="require_evidence_citation",
                       message="line 1 missing refs",
                       evidence={"line_no": 1})
         ]
         rule_ids_fired = ("require_evidence_citation",)

Step 6.  Engine emits to sink (AuditLogSink writes a row):

         await sink.emit({
             "event_kind": "rule_fired",
             "rule_id":    "require_evidence_citation",
             "phase":      "pre_action_proposal",
             "decision":   "block",
             "evidence":   {"line_no": 1},
             "message":    "line 1 missing refs",
             "regulatory_basis": ["internal SOP-BILL-02"],
         })

         ─▶ INSERT INTO audit_log (
                workflow_run_id, sequence_no, phase, event_kind,
                rule_id, policy_hash, decision, actor, payload
            ) VALUES (
                'wf-abc', 7, 'pre_action_proposal', 'rule_fired',
                'require_evidence_citation', 'abc123…', 'block', NULL,
                '{"message": "...", "evidence":{"line_no":1},
                  "regulatory_basis":["internal SOP-BILL-02"]}'
            ) ON CONFLICT (workflow_run_id, sequence_no) DO NOTHING

Step 7.  Engine returns Decision(permit=False, violations=[…], …).

Step 8.  Activity sees not decision.permit:
            raises PolicyDecisionError
              ─▶ ApplicationError(type="PolicyDecisionError",
                                   non_retryable=True)
                  ◀── only place the non_retryable double-negative lives

Step 9.  Workflow catches the ApplicationError, audits 'policy_rejected',
         returns WorkflowResult(outcome="policy_rejected", …). END.

Step 10. Five years later, the auditor asks why:
            SELECT * FROM audit_log
            WHERE workflow_run_id='wf-abc' AND rule_id='require_…';
            ─▶ row from Step 6

            SELECT rules_json FROM policy_snapshots
            WHERE policy_hash='abc123…';
            ─▶ full RULES at that hash, including the regulatory_basis,
               primitive params, severity — fully reconstructable
```

---

## 6. The Sink — decoupling "engine knows" from "audit_log row exists"

```
                                ┌──── one method ────┐
                                │ async emit(event)  │
                                └────────────────────┘
                                          ▲
                ┌─────────────────────────┼─────────────────────────┐
                │                         │                         │
       ┌────────┴───────┐        ┌────────┴───────┐       ┌─────────┴─────┐
       │ AuditLogSink   │        │ InMemorySink   │       │   NullSink    │
       │ (production)   │        │ (unit tests)   │       │  (default)    │
       │                │        │                │       │               │
       │ writes one row │        │ events.append  │       │  no-op        │
       │ to audit_log   │        │ for assertions │       │               │
       │ per emit       │        │                │       │               │
       └────────┬───────┘        └────────────────┘       └───────────────┘
                │
                ▼
         compass_test
          Postgres
```

The engine doesn't import psycopg. The sink does. Tests don't need a
database to verify the engine emits the right events at the right times.

---

## 7. Mental model — one sentence each

- **Phase** — *when* a rule fires (one of five points in the workflow).
- **Rule** — one named, parameterized constraint; what audit_log.rule_id holds.
- **Policy** — the bundle of all rules for a workflow; what policy_hash identifies.
- **Predicate** — the callable check inside a rule: `(ctx) → Violation | None`.
- **Primitive** — a re-usable predicate template; `numeric_threshold` produces many predicates with different `max=` values.
- **Sink** — where rule_fired / rule_skipped events go (audit_log, in-memory list, /dev/null).
- **Engine** — the pure async loop that runs each phase's rules, emits events, returns a Decision.
- **Snapshot** — the row in `policy_snapshots` that lets you reconstruct a policy from its hash 5 years later.
