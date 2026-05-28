# Stage 6 — Intent Classifier / Scope Gate (design)

Implements the v0.1 binary scope gate per `docs/build-plan.md` §Stage 6,
§Architecture (the DAG block at workflow entry), and §Phases (the
`input_validation` row that fires twice — scope-gate agent then main
agent). Stage 6 adds the first activity in `SendInvoiceWorkflow.run`, a
classifier sub-agent whose structured output is checked by a single
new `input_validation`-phase policy rule, and the audit-log surface for
unsupported requests.

The build-plan section is the contract for this stage. This document
pins the choices it leaves open: which wiring mechanism the rule uses
(post-Runner `evaluate_policy` activity vs. OpenAI Agents SDK
`output_guardrail`), how the audit row for an out-of-scope request is
shaped, how the classifier output flows into the policy context, and
the test matrix.

## What ships

```
compass/policy/primitives/
└── intent.py                       # NEW. intent_in_allowlist(field, allowed)
                                    # framework-core, registered via @primitive

workflows/send_invoice/
├── scope_gate.py                   # NEW. IntentClassification (Pydantic),
│                                   # SCOPE_GATE_INSTRUCTIONS,
│                                   # build_scope_gate_agent().
├── workflow.py                     # MODIFIED. Inserts scope-gate step at
│                                   # the top of run(): Runner.run(scope_gate),
│                                   # evaluate_policy(input_validation),
│                                   # short-circuit on block.
├── types.py                        # MODIFIED. WorkflowOutcome literal gains
│                                   # "unsupported".
└── (no change to activities.py, worker.py, sandbox.py, primitives.py)

policies/
└── send_invoice.py                 # MODIFIED. One new Rule at
                                    # Phase.input_validation.

synthetic_account_1/
├── simulate.py                     # MODIFIED. expected_classification value
│                                   # for in-scope cases: "in_scope" →
│                                   # "send_invoice". Out-of-scope unchanged.
└── ground_truth/{train,holdout}/
    └── scope_gate_labels.jsonl     # REGENERATED. Deterministic from seed.

tests/
├── compass/policy/primitives/
│   └── test_intent.py              # NEW. Allowlist/denylist/missing-field
│                                   # cases for the new primitive.
├── policies/
│   ├── conftest.py                 # MODIFIED. Add happy_input_validation_ctx
│   │                               # and out_of_scope_input_validation_ctx
│   │                               # fixtures.
│   └── test_send_invoice_rules.py  # MODIFIED. Two new test cases.
└── workflows/send_invoice/
    ├── test_workflow_policy.py     # MODIFIED. Two new direct-activity tests
    │                               # for phase="input_validation".
    └── test_scope_gate.py          # NEW. Workflow-level orchestration via
                                    # TestModel with two canned responses
                                    # (scope-gate then main agent).
```

Stages explicitly **not** in scope: the UI "Unsupported at the moment"
surface (Stage 12), the eval harness that measures scope-gate
rejection accuracy (Stage 7), the holdout run that produces the
released-claim number (Stage 11), and the multi-class router upgrade
(Stage 16). Confidence-threshold gating is deferred — the classifier's
confidence value is captured in the audit payload for product
iteration but is not used as a gating predicate at v0.1.

## The intent literal

```python
# workflows/send_invoice/scope_gate.py
IntentLabel = Literal["send_invoice", "out_of_scope"]
```

Two values at v0.1. Stage 16's multi-class router adds
`"dispute_investigation"` as a third literal member — a purely additive
change to the union and to `intent_in_allowlist(allowed=...)`. The
classifier never returns "in_scope" as a value; "in_scope" is a corpus
artifact that translates 1:1 to "send_invoice" once the corpus is
regenerated (see §Corpus regeneration).

Why these labels and not `in_scope` / `out_of_scope`:

- The classifier's job is to route to *a workflow*. `"send_invoice"` is
  the workflow name; `"out_of_scope"` is "no workflow matches".
- At v0.2 the literal extends to `{send_invoice, dispute_investigation,
  out_of_scope}`. Keeping the v0.1 literal aligned with workflow names
  means the v0.2 transition adds one element to the union; if v0.1
  were `in_scope` we would rename + add (two changes).
- The build-plan §Stage 16 success criterion is "same audit format,
  same gateway integration" — the audit payload's `intent` field
  doesn't change shape between v0.1 and v0.2, only its value set
  widens.

## Where the rule fires

**Post-Runner `evaluate_policy(phase=input_validation)` from the
workflow body**, not `attach_to_agent` as an OpenAI Agents SDK
guardrail. Three reasons:

1. The rule's input is the classifier's *output*. An input_guardrail
   on the scope-gate agent only sees the user message — it cannot
   gate on `classification.intent`. The build-plan §Phases table
   captures this explicitly: "User message (+ scope-gate classifier
   output on second firing)". The classifier output is the rule's
   context.
2. The activity-call pattern matches `pre_action_proposal` exactly.
   `evaluate_policy` already switches on phase (Stage 5); adding
   `input_validation` to the switch is a one-line change. Snapshot
   write, sink wiring, sequence counter, error mapping all reuse the
   existing path.
3. `attach_to_agent`'s sink-factory plumbing was left stubbed at
   Stage 5 because no rules exercised the non-None path. Building it
   now to support one rule that doesn't need it would be unrelated
   scope.

The build plan permits this explicitly: §Architecture observation 2
notes that input/output validation rules "are wired in as agent
input_guardrails / output_guardrails (or evaluated against the
Pydantic structured output post-Runner)". The post-Runner clause is
sanctioned.

**What remains true of `Phase.input_validation`:** the phase name and
its position in the audit log do not change. A Stage-11 (or later)
addition of a *raw-input* rule — e.g., `prompt_injection_detected`
applied to the user message before the scope gate spends tokens —
would wire via `attach_to_agent` on the scope-gate agent and emit at
`Phase.input_validation` from there. Both wiring mechanisms feed the
same phase; the audit consumer can't tell them apart and shouldn't
need to.

## The `intent_in_allowlist` primitive

Framework-core. One small file: `compass/policy/primitives/intent.py`.

```python
@primitive("intent_in_allowlist")
def intent_in_allowlist(*, field: str, allowed: frozenset[str]):
    """Fails if the value at `field` is not in `allowed`.

    Generic over the intent vocabulary. At v0.1 send_invoice passes
    `allowed=frozenset({"send_invoice"})`; at v0.2 the dispute
    workflow's policy module would pass a different set, or a router
    policy module would pass the union.

    The factory accepts `allowed` as a `frozenset` so the registry's
    param-freezing (`_freeze`) treats it as a value, and so two calls
    with the same membership hash identically.
    """

    def check(ctx: Mapping[str, Any]) -> Violation | None:
        value = resolve_dotted(ctx, field)
        if value is MISSING:
            return Violation(
                rule_id="",
                message=f"field {field!r} missing from context",
                evidence={"field": field, "reason": "missing"},
            )
        if value not in allowed:
            return Violation(
                rule_id="",
                message=(
                    f"intent {value!r} is not in allowlist "
                    f"{sorted(allowed)}"
                ),
                evidence={
                    "field": field,
                    "value": value,
                    "allowed": sorted(allowed),
                },
            )
        return None

    return check
```

Why a new primitive instead of reusing `entity_status_equals`:
`entity_status_equals` compares to a single value. The intent rule
needs set membership for forward-compatibility with multi-class.
Generalizing `entity_status_equals` to accept a set would change its
public signature and break the existing `customer_kyc_verified` rule
without benefit; a separate primitive keeps each one focused.

`_freeze` in `compass/policy/registry.py` already accepts `frozenset`
as a hashable value (tested in `test_registry.py` indirectly via
`primitive(allowed=...)`). No registry change needed.

## The rule

```python
# policies/send_invoice.py — appended to RULES
Rule(
    id="intent_must_be_send_invoice",
    phase=Phase.input_validation,
    predicate=intent_in_allowlist(
        field="classification.intent",
        allowed=frozenset({"send_invoice"}),
    ),
    severity=Severity.BLOCK,
    regulatory_basis=("internal SOP-SCOPE-01",),
    tags=("scope_gate",),
    must_be_covered=True,
)
```

Severity is `BLOCK` (not `ESCALATE`): `Rule.__post_init__` already
rejects `ESCALATE` at `Phase.input_validation` (Stage 5 hard rule —
OpenAI Agents SDK guardrails are tripwire-only). The Stage-6 rule
does not use `attach_to_agent`, but the `__post_init__` check is
phase-based, not wiring-mechanism-based, so the constraint applies.
This is the intended behavior: a workflow that decides "the user's
intent is not one we serve" should reject, not escalate.

`must_be_covered=True` so Stage-10's CI gate fails if the holdout
corpus produces zero `out_of_scope` cases (a dead-code regression in
the only rule that defends the scope boundary).

Rule count after Stage 6: **13** (was 12 at Stage 5; the workflow's
conservative sequence-counter bumps in `workflow.py` need adjustment
— see §Workflow integration).

## The classifier agent

`workflows/send_invoice/scope_gate.py`:

```python
class IntentClassification(BaseModel):
    """Structured output of the scope-gate agent.

    Stored verbatim in the policy context dict under "classification",
    so primitive predicates can read fields via dotted paths.
    """
    model_config = ConfigDict(extra="forbid")

    intent: IntentLabel
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


SCOPE_GATE_INSTRUCTIONS = """\
You are the scope gate for a billing agent that can only do one
thing: draft and send invoices for a B2B SaaS company.

Classify the user's request as one of:

- "send_invoice": the user wants you to draft, prepare, or send an
  invoice to a customer for work performed or services rendered.
  Examples: "invoice Acme for last quarter's work", "bill Stark
  Industries $7,200 for the Q1 onboarding", "draft an invoice for
  the consulting we did in March".

- "out_of_scope": anything else. Wire transfers, account opens,
  refunds, dispute investigations, payment lookups, general
  questions, transfers between internal accounts, weather queries,
  small-talk. If you are unsure, classify as out_of_scope.

Always include:
  - intent: the classification
  - confidence: 0.0–1.0; how sure you are
  - rationale: one short sentence explaining the call

Do not invoke tools. Do not propose actions. You only classify.
"""


def build_scope_gate_agent() -> Agent[None]:
    return Agent[None](
        name="send_invoice_scope_gate",
        instructions=SCOPE_GATE_INSTRUCTIONS,
        output_type=IntentClassification,
        model=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        mcp_servers=[],   # no tool surface
    )
```

`DEFAULT_MODEL` is reimported from `workflows.send_invoice.agents` to
keep the model choice in one place. The scope-gate agent has no
`mcp_servers` and no `tools`; the classifier never needs to look at
any data. It runs inside the workflow as an auto-activity via
`OpenAIAgentsPlugin` exactly like the main agent.

Why a sub-agent instead of an explicit hand-rolled activity:

- The build-plan §Architecture observation says agent-loop steps stay
  inside `Runner.run(...)` and become auto-activities. A hand-rolled
  classifier activity would deviate from that pattern for no gain.
- LLM observability via Langfuse comes for free (the
  openinference instrumentation captures every `Runner.run`).
- v0.2 Stage 16 multi-class router slots in by widening
  `IntentLabel`, the instructions, and the allowlist set — no
  workflow-shape change. A hand-rolled activity would need
  re-architecting.

## Workflow integration

`workflows/send_invoice/workflow.py` grows three blocks at the top of
`run()`. The rest of the function is unchanged.

```python
@workflow.run
async def run(self, req: SendInvoiceRequest) -> WorkflowResult:
    run_id = workflow.info().workflow_id

    # ---- 1. scope-gate sub-agent (no MCP, no tools) -----------------
    scope_agent = build_scope_gate_agent()
    gate_result = await Runner.run(scope_agent, input=req.user_message, max_turns=1)
    classification = gate_result.final_output
    if classification is None:
        await self._audit(
            phase="input_validation",
            event_kind="agent_no_output",
            payload={"user_message": req.user_message},
        )
        return WorkflowResult(
            outcome="unsupported",
            detail="Scope gate returned no structured classification.",
        )

    # ---- 2. input_validation policy gate ----------------------------
    input_ctx = {
        "user_message": req.user_message,
        "classification": classification.model_dump(),
        "workflow_run_id": run_id,
    }
    try:
        payload = await workflow.execute_activity(
            evaluate_policy,
            EvaluatePolicyInput(
                workflow_run_id=run_id,
                starting_sequence_no=self._next_seq + 1,
                phase=Phase.input_validation.value,
                context=input_ctx,
            ),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_POLICY_DECISION_RETRY,
        )
    except ActivityError as e:
        cause = e.cause if isinstance(e.cause, ApplicationError) else None
        err_type = cause.type if cause else None
        # One rule at this phase today; bump conservatively.
        self._next_seq += 2
        await self._audit(
            phase="input_validation",
            event_kind="unsupported",
            payload={
                "user_message": req.user_message,
                "classification": classification.model_dump(),
                "error_type": err_type,
                "message": str(e),
            },
            decision="block",
            is_terminal_event=True,
        )
        return WorkflowResult(outcome="unsupported", detail=str(e))

    self._policy_hash = payload.policy_hash
    self._next_seq = payload.next_sequence_no - 1

    # Permitted: emit a non-rule audit row capturing the classifier
    # output so successful runs are inspectable too.
    await self._audit(
        phase="input_validation",
        event_kind="intent_classified",
        payload={
            "user_message": req.user_message,
            "classification": classification.model_dump(),
        },
        decision="permit",
    )

    # ---- 3. existing main-agent loop --------------------------------
    async with stateful_mcp_server("bank", config=...):
        ...   # unchanged from Stage 5
```

Notes:

- **`is_terminal_event=True` on the unsupported short-circuit.** The
  unsupported audit row is the workflow's terminal row, so it runs
  through `audit_validation` like any other terminal row. This means
  `log_policy_version` will fire on it; `log_data_sources_consulted`
  will look at `tool_calls_for_validation=[]` which is empty — the
  Stage-5 rule fires when tool_calls is empty (it's a workflow-defect
  detector). For an unsupported workflow, there genuinely are no tool
  calls (the main agent never ran). Two options:
  1. Suppress audit_validation on this path by leaving
     `is_terminal_event=False`. Loses some uniformity.
  2. Pass `is_terminal_event=True` and accept that
     `audit_has_data_sources` fires-but-doesn't-block (Stage 5
     `audit_log` activity does not raise on audit_validation
     failures — see `activities.py:120-124` "No raise on
     audit_validation BLOCK").
  3. Update `log_data_sources_consulted` to skip when the workflow
     terminated at `input_validation` (visible via `phase` on the
     candidate row).

  **Decision (revised during implementation): (1).** Match the
  existing precedent set at Stage 5 — `policy_rejected`, `declined`,
  and `timeout` terminal rows do *not* set `is_terminal_event=True`.
  Only the `executed` (success) path runs audit_validation as a
  defect detector. Unsupported short-circuits join that pattern: the
  audit row is written, the workflow ends, and audit_validation
  rules don't fire on it. Avoids false-positive defect signals from
  `log_policy_version` (which would fire because no main-loop
  `policy_hash` is threaded back through the failed activity). The
  `log_data_sources_consulted` defensive short-circuit on
  `input_validation` candidate phase is kept as a safety net for any
  future caller that does set `is_terminal_event=True` on an
  input_validation row.

- **`self._policy_hash` is set after the input_validation gate.** If
  input_validation permits and pre_action_proposal then runs, it
  overwrites `self._policy_hash` with the same value (the policy is
  one bundle hashed once per `evaluate_policy` invocation;
  invocations within the same worker process produce the same hash).
  Snapshot table's `ON CONFLICT DO NOTHING` makes the second write
  a no-op.

- **Sequence-counter discipline.** Stage 5's `workflow.py` uses
  conservative bumps (`self._next_seq += 12` at pre_action_proposal,
  `+= 4` at pre_execute, `+= 4` at audit_validation). After Stage 6,
  the rule count and per-phase distribution change. New conservative
  values:
  - input_validation: 1 rule → bump by 2 on error path.
  - pre_action_proposal: 8 rules (unchanged) → 12 (unchanged
    conservative).
  - pre_execute: 2 rules (unchanged) → 4 (unchanged).
  - audit_validation: 2 rules (unchanged) → 4 (unchanged).

- **The unsupported short-circuit returns before reaching the MCP
  `stateful_mcp_server` block.** No bank MCP subprocess is spawned
  on the unsupported path. This matters for cost: an out-of-scope
  request consumes only the scope-gate LLM call.

## Audit-row taxonomy

Two event_kind values are added to the de-facto taxonomy (the schema's
`event_kind` column is a free `TEXT` field, no CHECK constraint —
build-plan §Database lists the taxonomy in a comment but does not
enforce it):

| event_kind | When | Phase | is_terminal_event |
|---|---|---|---|
| `intent_classified` | After permit at input_validation | input_validation | False |
| `unsupported` | After block at input_validation (or after agent_no_output) | input_validation | True |

`unsupported` is already named in the build-plan event_kind taxonomy
(§Database `audit_log` table comment line ~422); Stage 6 wires the
first writer for it. `intent_classified` is new — adds a
non-rule "this classifier said X" row so product iteration has
visibility into permitted classifications too, not just rejected
ones. Both fit the existing `audit_log` schema with no DDL change.

`payload` shape for both:

```json
{
  "user_message": "What's the weather in San Francisco?",
  "classification": {
    "intent": "out_of_scope",
    "confidence": 0.98,
    "rationale": "Weather queries are not billing operations."
  }
}
```

On the `unsupported` (error-path) row from `ActivityError`, the
payload additionally carries `error_type` and `message` so the audit
row reconstructs why the workflow short-circuited — same fields the
existing `policy_rejected` event_kind carries.

## Corpus regeneration

`synthetic_account_1/simulate.py` change is two lines:

```diff
-            "expected_classification": "in_scope",
+            "expected_classification": "send_invoice",
```

Re-run:

```sh
uv run python -m synthetic_account_1.simulate
uv run python -m synthetic_account_1.verify
```

Outputs change: `scope_gate_labels.jsonl` in both `train/` and
`holdout/`. The diff is mechanical — exactly the count of in-scope
cases per split (60 train + 24 holdout based on Stage 2's split
ratios; verify.py will catch any miscount). Out-of-scope rows do not
change. Other ground-truth files (`invoice_resolution_labels`,
`policy_compliance_labels`, `perturbation_stability_labels`) are
untouched; they don't reference the scope-gate vocabulary.

The Stage-7 eval scorer will read `expected_classification` as a
string and compare directly to the agent's `intent` value — no
translation layer needed once the corpus is regenerated.

`policy_compliance_labels.jsonl` already names the scope-gate rule as
`"scope_gate_in_scope"` (visible in simulate.py:96 grep above). At
Stage 11 / Stage 7 we will rename either the corpus or the rule_id
to match. Stage 6 ships the rule_id as `"intent_must_be_send_invoice"`
(descriptive of what it enforces); a separate one-line corpus update
can align them when Stage 7 lands.

## `attach_to_agent` future use

Stage 6 does NOT exercise `attach_to_agent` for scope-gate rules.
The mechanism stays wired (Stage 5 ships the no-op path); a future
addition would look like:

```python
# hypothetical Stage-11 addition
attach_to_agent(
    scope_agent,
    [Rule(id="prompt_injection_detected_in_message", ...,
          phase=Phase.input_validation, ...)],
    sink_factory=lambda: AuditLogSink(...),
)
```

Such a rule would gate on the *raw* user message before the
classifier spends tokens, complementing (not replacing) the
post-Runner intent allowlist. The two firings — input_guardrail on
the scope-gate agent, then post-Runner `evaluate_policy` — match the
build-plan §Phases description: "fires twice in send-invoice".

The `sink_factory` plumbing inside `attach_to_agent` is still
stubbed at Stage 5 (the existing code uses `NullSink` when no
factory is supplied). When the first real rule lands, the factory
needs to construct an `AuditLogSink` per invocation with a fresh DB
connection, since input_guardrail callbacks run inside auto-wrapped
activities that don't share workflow-level state. This is the
"sink_factory becomes load-bearing" deferred item from Stage 5's
spec. Stage 6 does not close it.

## Determinism and replay

Replay-safety analysis:

- `Runner.run(scope_gate, ...)`: auto-activities via
  `OpenAIAgentsPlugin`. Activity boundary records LLM output; replay
  reads the cached output. Same as the existing main-agent run.
- `evaluate_policy(phase=input_validation, ...)`: explicit Temporal
  activity. Result deterministic given fixed `RULES` (same as Stage
  5).
- `self._policy_hash`, `self._next_seq` assignments: ordinary
  workflow-state mutations. Replay-deterministic by construction.
- `self._audit(...)`: writes go through the activity boundary already
  hardened at Stage 5 (`ON CONFLICT DO NOTHING` on
  `(workflow_run_id, sequence_no)`).

No `workflow.patched("v0.1.<n>")` is needed for in-flight workflows
because Stage 5 has not yet been deployed against real users — there
are no in-flight workflows to be replay-broken. (If Stage 5 had been
deployed, the addition of new pre-amble steps in `run()` would shift
every existing workflow's history and require a `patched` gate.)

## Test matrix

### `tests/compass/policy/primitives/test_intent.py` (new)

Pure unit tests; no Postgres, no Temporal. Use `InMemorySink`.

| Test | Input | Expected |
|---|---|---|
| `test_value_in_allowlist_skips` | ctx with `classification.intent="send_invoice"`, allowed=`{"send_invoice"}` | Returns `None` |
| `test_value_not_in_allowlist_blocks` | intent=`"out_of_scope"`, allowed=`{"send_invoice"}` | Returns `Violation` with evidence `{field, value, allowed}` |
| `test_missing_field_blocks` | ctx without `classification.intent` | Returns `Violation` with evidence `{field, reason: "missing"}` |
| `test_multi_class_allowlist_skips` | intent=`"dispute_investigation"`, allowed=`{"send_invoice", "dispute_investigation"}` | Returns `None` — verifies multi-class extensibility |
| `test_primitive_registered` | `list_primitives()` | Contains `"intent_in_allowlist"` |

### `tests/policies/conftest.py` (modified)

Two new fixture builders:

```python
def happy_input_validation_ctx() -> dict[str, Any]:
    return {
        "user_message": "Please send an invoice to Acme Corp for $7,200.",
        "classification": {
            "intent": "send_invoice",
            "confidence": 0.98,
            "rationale": "Direct invoice request.",
        },
        "workflow_run_id": "test-run-id",
    }


def out_of_scope_input_validation_ctx() -> dict[str, Any]:
    return {
        "user_message": "What's the weather in SF?",
        "classification": {
            "intent": "out_of_scope",
            "confidence": 0.95,
            "rationale": "Weather query is not a billing operation.",
        },
        "workflow_run_id": "test-run-id",
    }
```

### `tests/policies/test_send_invoice_rules.py` (modified)

| Fixture | Expected |
|---|---|
| `happy_input_validation_ctx` | `Decision.permit=True`, `rule_ids_fired=()`, one rule_skipped event for `intent_must_be_send_invoice` |
| `out_of_scope_input_validation_ctx` | `Decision.permit=False`, `rule_ids_fired=("intent_must_be_send_invoice",)`, one rule_fired event |

### `tests/workflows/send_invoice/test_workflow_policy.py` (modified)

Two new direct-activity tests against the real `evaluate_policy`:

| Test | Input | Expected audit |
|---|---|---|
| `test_input_validation_permits_send_invoice` | phase=input_validation, ctx with intent=send_invoice | snapshot row exists; one rule_skipped row at phase=input_validation; activity returns permit=True |
| `test_input_validation_blocks_out_of_scope` | phase=input_validation, ctx with intent=out_of_scope | one rule_fired row; activity raises `ApplicationError(type="PolicyDecisionError", non_retryable=True)` |

### `tests/workflows/send_invoice/test_scope_gate.py` (new)

Workflow-level orchestration tests via `TestModel`. The
scope-gate agent runs FIRST inside the workflow; `TestModel`'s
response cycle has to return the classifier output, then (on the
permit path) the main agent's `InvoiceProposal`. The Agents SDK
matches structured output by parsing the assistant's text against
`output_type`; sequencing is by call order.

```python
def _scope_then_proposal_model(
    classification: dict, proposal: dict | None,
) -> TestModel:
    responses = [json.dumps(classification)]
    if proposal is not None:
        responses.append(json.dumps(proposal))
    iterator = iter(responses)
    return TestModel(lambda: ResponseBuilders.output_message(next(iterator)))
```

| Test | Classifier output | Proposal output | Expected outcome | Audit assertions |
|---|---|---|---|---|
| `test_out_of_scope_short_circuits` | `{intent: "out_of_scope", confidence: 0.99, rationale: ...}` | (not reached) | `unsupported` | audit_log contains rule_fired row for `intent_must_be_send_invoice` and a terminal `event_kind="unsupported"` row carrying the original message + classification payload; no pre_action_proposal rows |

The in-scope happy-path workflow test is intentionally NOT in this
file. With policy live, an in-process `TestModel` path would block at
`pre_action_proposal` (the TestModel never calls MCP so
`resolved_entities.customer` is missing and `customer_must_exist`
fires). The orchestration shape is verified by `test_workflow.py`
(with policy disabled via `COMPASS_POLICY_DISABLE=1`, where the
audit log shows the new `intent_classified` row sequence); the
input_validation gate's `permit` path is verified directly at
activity level by `test_workflow_policy.py`.

The `test_no_classifier_output_falls_through_to_unsupported` case
is deferred: forcing `TestModel` to produce a non-parseable response
requires bypassing the Agents SDK's structured-output retry loop
(the SDK re-prompts on malformed output), which is its own piece of
scaffolding. The `agent_no_output` branch in `workflow.py` is
straightforward enough that the production path's manual smoke
covers it.

The fixture uses `COMPASS_POLICY_DISABLE` ONLY for the
`pre_action_proposal` phase via the rule-set, not as an env-var
toggle — the input_validation gate MUST run live. Achieved by
running the in-scope test with the policy disabled at proposal time
(env var) but the workflow code path still reaches the gate; the gate
itself is enforced via the live `evaluate_policy` activity. (The env
var disables ALL phases when set, including input_validation. For the
in-scope test we want input_validation enforced but pre_action_proposal
bypassed because TestModel never calls MCP. To achieve this, the
in-scope test bypasses ONLY pre_action_proposal by leaving
COMPASS_POLICY_DISABLE=1 and explicitly asserting that input_validation
rule_fired/rule_skipped events were written to audit_log BEFORE the
env var took effect — wait, no, that doesn't work either.)

**Revised approach for `test_in_scope_routes_to_main_agent`**: set
`COMPASS_POLICY_DISABLE=1` for the whole run; assert only the
*orchestration* shape (scope-gate audit row appears via the
`intent_classified` event_kind, which the workflow writes
unconditionally via `_audit` rather than through `evaluate_policy`).
The policy *rules* at input_validation are exercised by
`test_input_validation_permits_send_invoice` and
`test_input_validation_blocks_out_of_scope` in
`test_workflow_policy.py` — the activity-level tests. The
workflow-level `test_scope_gate.py` tests cover orchestration only
(does the workflow route the right way, does the audit log have the
right shape), consistent with Stage 5's separation of orchestration
tests (`test_workflow.py`) from policy tests (`test_workflow_policy.py`).

For `test_out_of_scope_short_circuits` the workflow must take the
block path, which the `evaluate_policy` activity raises by examining
the classifier output — `COMPASS_POLICY_DISABLE=1` would defeat the
short-circuit. Solution: run this single test with
`COMPASS_POLICY_DISABLE` unset (override the autouse fixture). The
test then exercises the policy gate end-to-end at the workflow level.

### Existing test impact

- `tests/workflows/send_invoice/test_workflow.py` (orchestration
  tests, policy disabled): the workflow now does an extra
  `Runner.run(scope_gate)` at the start. `TestModel` needs to return
  the classification first, then the proposal. The existing
  `_proposal_response` fixture builds a one-shot model; it needs to
  become a two-shot model that yields classification then proposal.
  Mechanical change, no logic change.
- `tests/workflows/send_invoice/test_workflow_policy.py` (existing
  pre_action_proposal / pre_execute / audit_validation tests): these
  run the `evaluate_policy` activity directly with constructed
  context dicts; they do not start a workflow run. No change.

## Open issues / future work

- **`scope_gate_in_scope` rule_id in the corpus.** Stage-2-era
  `policy_compliance_labels.jsonl` names a rule that Stage 6 ships
  under a different id (`intent_must_be_send_invoice`). Reconcile in
  Stage 7 when the policy-compliance trace assertion library lands;
  either rename the corpus value or accept that Stage 7 is the right
  moment to standardize all rule_id references. Documented here so
  it's not surprising at Stage 7.
- **`sink_factory` for `attach_to_agent`.** Still stubbed. Stage 11+
  problem; closed when a real rule needs the path.
- **`log_data_sources_consulted` short-circuit for unsupported
  workflows.** Documented above as the chosen fix; implemented in
  Stage 6.
- **Cost-impact note for production traffic.** Adding a sub-agent
  call adds one LLM round-trip to every workflow, including
  obviously-in-scope ones. At cheap models (gpt-4.1-mini per Stage 4
  default) this is sub-cent per run; not optimized at v0.1. A faster
  classifier model — or an embedding-based fast path before the LLM
  call — is a Stage 11+ tuning question, not Stage 6 scope.
