# Stage 5 — Policy Engine + Primitive Library (design)

Implements the v0.1 `compass.policy` submodule per `docs/build-plan.md` §Stage 5
and §Policy Engine + Primitive Library. Stage 5 introduces the policy engine,
the primitive registry, the small set of framework-core primitives that
`policies/send_invoice.py` actually uses, and the integration into the
existing `SendInvoiceWorkflow`. Stage 5 ships the audit-interpretability
contract (policy_hash + policy_snapshots), wires all five phases through the
public API, and end-to-end tests the policy review gate with passing,
escalating, and failing proposals.

The build-plan section is the contract for this stage. This document records
the choices it leaves open: how the workflow constructs the policy context
from a `RunResult`, how the sink abstraction routes trace events, how
exceptions map to Temporal retry semantics, and the test layout.

## What ships

```
compass/
├── __init__.py
└── policy/
    ├── __init__.py             # public API re-exports
    ├── types.py                # Rule, Phase, Severity, Decision, Violation, Predicate
    ├── errors.py               # PolicyDecisionError, PolicyEngineError, PolicyInfraError
    ├── engine.py               # async evaluate(...) + phase-specific wrappers
    ├── registry.py             # @primitive decorator + list_primitives()
    ├── sink.py                 # Sink protocol, InMemorySink, NullSink, MultiSink,
    │                           # register_sink, AuditLogSink
    ├── agent.py                # attach_to_agent(agent, rules, ...)
    ├── hashing.py              # hash_rules(RULES); canonical serialization
    ├── snapshot.py             # write_policy_snapshot(conn, workflow, rules)
    └── primitives/
        ├── __init__.py
        ├── resolution.py       # require_existing_entity
        ├── identity.py         # entity_status_equals
        ├── value.py            # numeric_threshold
        ├── evidence.py         # require_evidence_citation
        ├── approval.py         # prohibit_silent_modification_after_confirmation,
        │                       # prohibit_policy_drift_after_confirmation
        └── audit.py            # log_policy_version, log_data_sources_consulted

policies/
└── send_invoice.py             # RULES: list[Rule] — the v0.1 send-invoice policy

workflows/send_invoice/
├── primitives.py               # NEW. Billing integrity primitives; self-register via
│                               # @primitive (require_amount_source,
│                               # contract_consistency_check,
│                               # prohibit_exceed_contract_cap,
│                               # currency_consistency_check)
├── context.py                  # NEW. Pure functions over RunResult that produce
│                               # the policy context dict (no I/O, safe in workflow)
├── activities.py               # MODIFIED. evaluate_policy switches on phase; runs
│                               # write_policy_snapshot in same tx; raises
│                               # PolicyDecisionError → ApplicationError(non_retryable).
│                               # audit_log gets an is_terminal_event flag that
│                               # runs evaluate_audit_validation before insert.
├── types.py                    # MODIFIED. EvaluatePolicyInput grows phase + context.
│                               # PolicyDecision grows policy_hash, escalations,
│                               # next_sequence_no.
└── workflow.py                 # MODIFIED. After Runner.run: builds context via
                                # workflows.send_invoice.context. After approval:
                                # calls evaluate_policy with phase=pre_execute. Final
                                # audit_log call sets is_terminal_event=True.

tests/
├── compass/policy/             # unit tests of engine + primitives
│   ├── conftest.py
│   ├── test_engine.py
│   ├── test_hash.py
│   ├── test_registry.py
│   ├── test_rule_validation.py
│   └── primitives/
│       ├── test_resolution.py
│       ├── test_identity.py
│       ├── test_value.py
│       ├── test_evidence.py
│       ├── test_approval.py
│       └── test_audit.py
├── policies/
│   └── test_send_invoice_rules.py   # RULES driven directly by evaluate()
│                                    # against fixture contexts: happy +
│                                    # one fail case per rule + escalate case
└── workflows/send_invoice/
    ├── test_context.py              # NEW. Pure-function tests of context.py
    └── test_workflow_policy.py      # NEW. End-to-end workflow tests where the
                                     # canned TestModel proposal drives the gate
                                     # through pass / block / escalate paths
```

Stages explicitly **not** in scope: scope-gate classifier and any input_validation
rules on the main agent (Stage 6); LLM-judge primitives like
`prompt_injection_detected` (Stage 8 area); CI gate over `must_be_covered`
coverage (Stage 10); Next.js approval UI that would let a human edit the
proposal between proposal and execute (Stage 12).

## Naming: `Rule` vs `Policy`

The build-plan uses both terms for different things, and the code preserves
that split:

- **Policy** — the entire `RULES: list[Rule]` module hashed as one unit.
  This is what `policy_hash`, `policy_snapshots.policy_hash`, and
  `prohibit_policy_drift_after_confirmation` reference.
- **Rule** — one constraint inside a policy. This is what
  `audit_log.rule_id` holds and what the coverage report's `GROUP BY rule_id`
  counts.

Calling the granular thing `Policy` would leave the bundle unnamed even
though the bundle is what gets versioned, snapshotted, and drift-checked.
The same split is standard across declarative governance systems (Open
Policy Agent, AWS IAM "statement-in-policy", Cedar).

## Primitive scope at Stage 5

Stage 5 ships **only** the primitives `policies/send_invoice.py` actually
references at v0.1. The build-plan's broader catalog
(`resolution_confidence_threshold`, `cumulative_value_per_session`,
`prohibit_self_dealing`, `restricted_recipient`, `sanitize_freeform_text`,
`prompt_injection_detected`, `data_minimization_check`,
`require_field_recency`, `require_data_source_for_field`,
`dual_control_above_threshold`, `approval_within_window`,
`log_full_reasoning_trace`) is deferred to Stage 17 (dispute workflow)
when actual use can drive the design.

Stage 5 ships:

| Primitive | Phase | Type |
|---|---|---|
| `require_existing_entity(field, entity_type)` | pre_action_proposal | framework-core |
| `entity_status_equals(field, expected_status)` | pre_action_proposal | framework-core |
| `numeric_threshold(field, *, min=None, max=None)` | pre_action_proposal | framework-core |
| `require_evidence_citation(field)` | pre_action_proposal | framework-core |
| `prohibit_silent_modification_after_confirmation()` | pre_execute | framework-core |
| `prohibit_policy_drift_after_confirmation()` | pre_execute | framework-core |
| `log_policy_version()` | audit_validation | framework-core |
| `log_data_sources_consulted()` | audit_validation | framework-core |
| `require_amount_source()` | pre_action_proposal | app-specific (send_invoice) |
| `contract_consistency_check()` | pre_action_proposal | app-specific |
| `prohibit_exceed_contract_cap()` | pre_action_proposal | app-specific |
| `currency_consistency_check()` | pre_action_proposal | app-specific |

App-specific primitives self-register at import via `@primitive`; they live
in `workflows/send_invoice/primitives.py` to keep `compass/` free of
project-specific concerns.

## Types

### `Phase`

```python
class Phase(StrEnum):
    input_validation     = "input_validation"
    output_validation    = "output_validation"
    pre_action_proposal  = "pre_action_proposal"
    pre_execute          = "pre_execute"
    audit_validation     = "audit_validation"
```

`StrEnum` because `audit_log.phase` is a TEXT column; equality to the
string is the natural read path.

### `Severity`

`BLOCK` or `ESCALATE`. `Rule.__post_init__` rejects `ESCALATE` at
`input_validation` / `output_validation` — OpenAI Agents SDK guardrails
are tripwire-or-nothing by contract (build-plan §Policy Engine).

### `Predicate`

A small dataclass wrapper, *not* a bare callable. We need to capture the
primitive's name and frozen params so `hash_rules` can serialize the
rule set canonically and `list_primitives` can enumerate them. Bare
functions cannot carry that metadata reliably.

```python
@dataclass(frozen=True)
class Predicate:
    """The check a Rule actually runs. Returned by a primitive factory.

    Constructed by primitive factories — not directly. The factory
    decorated with @primitive("foo") returns a Predicate whose
    ``primitive_name="foo"`` and ``params={...kwargs passed to factory}``
    so the rule's identity and configuration are introspectable for
    hashing, coverage, and audit reconstruction.
    """
    primitive_name: str
    params: Mapping[str, Any]
    fn: Callable[[Mapping[str, Any]], Awaitable[Violation | None] | Violation | None]

    async def __call__(self, ctx: Mapping[str, Any]) -> Violation | None:
        result = self.fn(ctx)
        if inspect.isawaitable(result):
            return await result
        return result
```

Sync predicates are the common case; async exists so future LLM-judge
primitives can `await Runner.run(judge_agent, ...)` without changing
the engine.

### `Rule`

Frozen dataclass with the fields from build-plan §Policy Engine, minus
`on_violation` (which duplicated the predicate's `Violation.message`).

```python
@dataclass(frozen=True)
class Rule:
    """One constraint inside a policy.

    The id is referenced by audit_log.rule_id, by trace assertions in the
    eval framework, and by the coverage report — it is the stable handle
    on this rule across audit retention windows. Renaming an id in use
    breaks historic queries; treat ids as append-only.

    The phase implicitly determines what's in the context dict the
    predicate receives — see §Context schemas.
    """
    id: str
    phase: Phase
    predicate: Predicate
    severity: Severity = Severity.BLOCK
    surface_to_user: bool = True
    regulatory_basis: tuple[str, ...] = ()
    must_be_covered: bool = False
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.severity is Severity.ESCALATE and self.phase in {
            Phase.input_validation, Phase.output_validation,
        }:
            raise ValueError(
                f"Rule {self.id!r}: ESCALATE is not realizable at phase "
                f"{self.phase.value} — OpenAI Agents SDK guardrails are "
                "tripwire-only. Use BLOCK or move the rule to a workflow-"
                "level phase."
            )
```

### `Violation` and `Decision`

```python
@dataclass(frozen=True)
class Violation:
    """A predicate's report that the rule fired.

    The predicate constructs this with rule_id=""; the engine fills
    rule_id in from the surrounding Rule. ``evidence`` is rule-specific
    structured data that lands in audit_log.payload — keep it small and
    JSON-serializable.
    """
    rule_id: str
    message: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class Decision:
    """The engine's verdict for one (phase, context) evaluation.

    ``permit=False`` only when at least one BLOCK rule fired.
    Escalations route to human review but do not flip permit — the
    workflow already gates on a human signal, so an escalation surfaces
    in the approval UI with the violation visible to the reviewer.
    """
    permit: bool
    violations: tuple[Violation, ...]
    escalations: tuple[Violation, ...]
    rule_ids_fired: tuple[str, ...]
```

### Wart acknowledged (stringly-typed paths)

Predicates take `ctx: Mapping[str, Any]` and look up paths like
`"proposal.line_items[*].source_refs"`. Typos fail at runtime, not load
time. The cleaner alternative is per-phase Pydantic context models, but
that adds ~200 LOC of boilerplate per phase and the build-plan explicitly
says "the phase implicitly determines the context contents." Stage 5
accepts the runtime-error risk; a boot-time `validate_rules(RULES)`
helper that smoke-runs every predicate against a synthetic context to
catch path typos is named here as a deferred improvement, not Stage 5
scope.

## Registry — `@primitive`

```python
_REGISTRY: dict[str, Callable[..., Predicate]] = {}

def primitive(name: str):
    """Mark a factory as a registered primitive.

    The decorated factory must take only keyword arguments (the engine
    serializes them as a sorted dict) and return a plain callable. The
    wrapper captures name + frozen params into the Predicate so hashing
    and coverage have everything they need.
    """
    def decorator(factory):
        @functools.wraps(factory)
        def wrapped(**params):
            fn = factory(**params)
            return Predicate(primitive_name=name, params=_freeze(params), fn=fn)
        if name in _REGISTRY:
            raise RuntimeError(f"duplicate primitive registration: {name!r}")
        _REGISTRY[name] = wrapped
        return wrapped
    return decorator
```

`list_primitives()` returns a `dict[str, factory]` for the coverage
report's denominator (Stage 10) and v0.2's reuse-ratio metric.

Workflow-side primitives self-register at import. The chain that triggers
registration:

```
policies/send_invoice.py
  └── imports workflows.send_invoice.primitives
       └── @primitive("require_amount_source") fires → _REGISTRY updated
```

Importing `policies/send_invoice.py` once at worker startup populates the
registry; everything downstream (hashing, coverage) sees the same
registry contents.

## Engine — `evaluate`

```python
async def evaluate(
    rules: Sequence[Rule],
    phase: Phase,
    context: Mapping[str, Any],
    *,
    sink: Sink,
) -> Decision:
    """Run every rule whose ``phase`` matches against ``context``.

    Emits a rule_fired or rule_skipped event per evaluated rule to
    ``sink``; that is how audit_log gets populated and how the coverage
    report later counts rule activations. The function is async because
    a primitive may invoke an LLM-judge sub-agent (via Runner.run, which
    Temporal's OpenAIAgentsPlugin auto-wraps as activities).

    Itself pure: no DB, no HTTP, no datetime.now(). Anything time-
    sensitive (a freshness deadline, a sanctions hit, a customer's
    current KYC status) MUST be loaded into ``context`` by a pre-loop
    activity. The engine's purity is what makes the evaluate_policy
    Temporal activity replay-safe.
    """
```

Loop semantics, deliberate choices pinned:

1. **Declaration order is preserved.** `audit_log.sequence_no` is
   monotonic, so reordering rules across versions would shuffle audit
   reads. Iteration order is `rules` itself; no sorting.
2. **Rules whose `phase != phase` are silently skipped** (not emitted to
   sink). Coverage counts only same-phase emissions.
3. **Predicate exception → `PolicyEngineError`** (retryable; see error
   taxonomy). `evaluate()` does NOT raise `PolicyDecisionError` — that's
   constructed by the activity wrapper from a returned `Decision`.
4. **None return → `rule_skipped` event** (named "skipped" not "passed"
   to match build-plan §Database event_kind taxonomy; semantically it
   means "evaluated and did not fire").
5. **Violation return → `rule_fired` event**, evidence + regulatory_basis
   embedded in the audit payload; bucket by severity.
6. **`permit = len(block_violations) == 0`.** Escalations don't block.

Phase-specific wrappers exist as syntactic sugar for workflow-level
phases:

```python
evaluate_pre_action_proposal(rules, context, *, sink) -> Decision
evaluate_pre_execute       (rules, context, *, sink) -> Decision
evaluate_audit_validation  (rules, context, *, sink) -> Decision
```

The two agent-bound phases (`input_validation`, `output_validation`)
don't get wrappers — they're called via `attach_to_agent`, not by
workflow code.

## Determinism: when can a predicate be async / call an LLM?

The workflow body (`@workflow.run`) must be replay-deterministic; the
activity body has no such constraint. `evaluate()` runs inside the
`evaluate_policy` Temporal activity, so:

- Pure dict-math predicates (`numeric_threshold`, `require_amount_source`):
  fine in any context.
- LLM-judge predicates that call `await Runner.run(judge_agent, ...)`:
  fine, because the `OpenAIAgentsPlugin` auto-wraps every LLM call inside
  Runner.run as an inner Temporal activity. The activity boundary records
  the LLM output; replay reads the cached output rather than re-sampling.

The wrong thing is raw `openai.responses.create(...)` directly inside a
predicate — bypasses the activity boundary, no replay record. The right
thing is `await Runner.run(judge_agent, ...)`. Stage 5 ships zero
LLM-judge primitives, but the engine is async-throughout so adding one
in Stage 8 is mechanical.

## Sink

The engine's trace-event substrate. Decouples "the engine knows that a
rule fired" from "audit_log holds a row for that firing":

```python
class Sink(Protocol):
    async def emit(self, event: dict[str, Any]) -> None: ...
```

Events:

```python
{"event_kind": "rule_fired",
 "rule_id": "require_amount_source",
 "phase": "pre_action_proposal",
 "decision": "block",
 "evidence": {...},
 "regulatory_basis": ["internal SOP-BILL-02"]}

{"event_kind": "rule_skipped",
 "rule_id": "currency_consistency",
 "phase": "pre_action_proposal"}
```

Three sinks ship at Stage 5:

| Sink | Use |
|---|---|
| `AuditLogSink(conn, workflow_run_id, seq, policy_hash)` | Production: writes one `audit_log` row per emit inside the activity's open transaction. |
| `InMemorySink()` | Unit tests: collects events in `self.events`. |
| `NullSink()` | Default when no sink registered; discards. |

A module-level `register_sink()` plus `MultiSink` lets a process register
process-wide sinks (e.g., a future Langfuse sink) without changing
callers. `evaluate(... sink=sink)` is always explicit; when not given, a
multiplex of registered sinks is used. Stage 5 wires the workflow to
pass `AuditLogSink` explicitly.

The sequence-counter discipline: the workflow owns the monotonic
`sequence_no` counter. It allocates a starting value, passes it into
the activity input; the `AuditLogSink` increments locally for each
emit; the activity returns the next-free value back to the workflow
so the workflow's counter stays consistent across activity calls.
Without this discipline, sink emits would collide with the workflow's
own `_audit()` writes.

## Hashing — `hash_rules` and snapshot reconstruction

```python
def hash_rules(rules: Sequence[Rule]) -> str:
    """Canonical sha256 of the full rule set.

    Captures (id, phase, primitive_name, primitive_params, severity,
    regulatory_basis, tags, must_be_covered, surface_to_user). Two rules
    with identical id/phase/severity but different
    ``numeric_threshold(max=...)`` values MUST hash differently — that's
    why params are serialized, not just (id, phase). The serialized
    rules_json is byte-identical between hash and snapshot persistence
    so the hash is reproducible from the snapshot.
    """
```

Canonicalization rules:

- Rules serialized in declaration order (matches iteration order in
  `evaluate()` so a hashed rule set is also an ordered rule set).
- Each rule → dict with keys `{id, phase, primitive, params, severity,
  regulatory_basis, tags, must_be_covered, surface_to_user}`.
- Tuples → JSON-native lists; `params` keys sorted alphabetically;
  nested mappings recursively sorted.
- `json.dumps(..., sort_keys=False, separators=(",",":"))`, sha256.

### Reconstructing a policy from its hash

The hash is one-way; reconstruction goes through `policy_snapshots`:

```sql
SELECT rules_json FROM policy_snapshots WHERE policy_hash = $1;
```

`rules_json` is the same canonical serialization used to compute the
hash, persisted on the first invocation that sees the hash. Every
`audit_log` row stores its `policy_hash`, so years later an auditor can:

1. Read the audit row → `policy_hash` + `rule_id` + `payload`.
2. Join `policy_snapshots` on `policy_hash` → `rules_json`.
3. Find the rule with matching `id` → primitive name + frozen params
   + regulatory_basis.
4. The audit row's `payload` has the violation evidence at fire time.

What is NOT recoverable: the predicate's Python source. If
`numeric_threshold` is later deleted from the repo, the snapshot still
tells you the rule was a `numeric_threshold(field=…, max=10000000)` and
why it fired, without needing to re-execute. Deliberate trade-off:
banking audit needs the reasoning, not a reproducible run.

`write_policy_snapshot(conn, workflow, rules)` runs inside the
`evaluate_policy` activity, in the same DB transaction as the audit
writes. Idempotent via `ON CONFLICT DO NOTHING` on the PK. Almost always
a no-op no-op after the first call per (worker × policy version).

## Errors — positive `retryable` flag

```python
# compass/policy/errors.py

@dataclass
class PolicyDecisionError(Exception):
    """A rule decided to block or escalate. Decision is deterministic,
    so this error is never retryable — retrying the same predicate
    against the same context yields the same answer."""
    decision: Decision
    retryable: ClassVar[bool] = False

@dataclass
class PolicyEngineError(Exception):
    """The engine itself failed to evaluate (predicate raised, primitive
    not registered, malformed context). Transient causes are plausible
    (LLM-judge sub-agent timeout, transient registry contention) so this
    error is retryable."""
    rule_id: str | None
    cause: BaseException | None
    retryable: ClassVar[bool] = True

@dataclass
class PolicyInfraError(Exception):
    """A pre-loop fact-loading activity or snapshot write failed
    (Postgres outage, etc.). Retryable, but typed separately from
    PolicyEngineError so on-call can tell 'policy decided no' from
    'database is down'."""
    cause: BaseException
    retryable: ClassVar[bool] = True
```

Positive flag everywhere. The negation to Temporal's
`ApplicationError(non_retryable=...)` happens at exactly one place — the
activity boundary in `evaluate_policy`:

```python
raise ApplicationError(
    str(exc),
    type=type(exc).__name__,
    non_retryable=not exc.retryable,    # the only double-negative in the codebase
) from exc
```

## Context schemas

The policy context is a `Mapping[str, Any]` populated by the workflow
before calling `evaluate()`. Dict not Pydantic, because predicates do
dotted-path lookups (`"proposal.total_cents"`, `"resolved_entities.contract.monthly_hour_cap"`)
that work uniformly against dicts.

### `pre_action_proposal`

```python
{
    "proposal": invoice_proposal.model_dump(),          # InvoiceProposal as dict
    "resolved_entities": {                                # extracted from RunResult
        "customer": {...} | None,
        "contract": {...} | None,
        "rate_card_entries": [...],
        "time_entries": [...],
    },
    "tool_calls": [                                       # for evidence verification
        {"tool_name": "list_customers", "args": {...}, "result_ids": [...]},
        ...,
    ],
    "reasoning_text": str,                                # concatenated assistant messages
    "workflow_run_id": str,
}
```

The non-trivial piece: `resolved_entities` and `tool_calls` are
extracted from `Runner.run`'s `RunResult` rather than re-fetched from
the bank MCP. Rationale: the policy should evaluate *what the agent
saw*, not a fresh view of the world that may have shifted between
agent run and policy evaluation. Extraction lives in
`workflows/send_invoice/context.py`:

```python
def extract_tool_calls(run_result: RunResult) -> list[dict]: ...
def project_resolved_entities(tool_calls: list[dict]) -> dict: ...
def extract_reasoning_text(run_result: RunResult) -> str: ...
```

All three are pure functions over `RunResult` — no I/O, no clock — so
they run safely inside workflow code without the sandbox complaining.
Unit-tested independently in `tests/workflows/send_invoice/test_context.py`.

When an entity wasn't queried (e.g., the agent took the user-specified
path and never called `get_active_contract`),
`resolved_entities.contract` is `None`. Rules that depend on the
optional entity short-circuit to "skipped" (predicate returns `None`)
rather than failing on a missing key. This is a documentation
convention, not typed enforcement at Stage 5.

### `pre_execute`

```python
{
    **pre_action_proposal_context,
    "approval": approval.model_dump(),                  # ApprovalDecision
    "proposal_hash_at_proposal": str,                    # for prohibit_silent_modification
    "policy_hash_at_proposal": str,                      # for prohibit_policy_drift
}
```

`proposal_hash_at_proposal` is `sha256(canonical_json(proposal))`
captured by the workflow right after the agent returns. The workflow
holds it in `self._proposal_hash` across the approval wait.

`policy_hash_at_proposal` is what `evaluate_policy` returned at the
pre_action_proposal phase. The workflow stores it in `self._policy_hash`
and threads it into pre_execute context.

**`pre_execute` does NOT re-load world state.** If the customer's KYC
was revoked while the human deliberated, the workflow still executes
under the KYC status that existed at proposal time. Re-checking the
world would create unpredictable execution paths and surfaces a
separate question (what to do on mid-workflow revocation) that belongs
to a dedicated withdraw-approval signal mechanism, not silent policy
re-evaluation. Build-plan §pre_execute context is explicit about what's
included; the resolved-entities snapshot is not refreshed.

### `audit_validation`

```python
{
    "audit_entry_candidate": {                         # the about-to-be-written row
        "phase": ..., "event_kind": ..., "payload": ...,
    },
    "policy_hash": str,                                 # for log_policy_version
    "tool_calls": [...],                                # for log_data_sources_consulted
    "reasoning_text": str,
}
```

Fires only for the terminal audit row of the workflow (`executed`,
`declined`, `timeout`, `policy_rejected`). See "Audit validation" below.

## What `pre_execute` adds over `pre_action_proposal`

Pre_execute is not a re-run of pre_action_proposal. It exists for rules
whose preconditions only become true after the approval signal lands:

| Category | Precondition only true post-approval | Stage-5 rules |
|---|---|---|
| Proposal-drift detection | A confirmed proposal to compare against | `prohibit_silent_modification_after_confirmation` |
| Policy-drift detection | A `policy_hash_at_proposal` to compare against now | `prohibit_policy_drift_after_confirmation` |
| Approver-identity (v1+) | Approver's verified `user_id` / `role` / MFA from signal | deferred (`dual_control_above_threshold`) |
| Time-elapsed (v1+) | A measurable gap between approval and execute | deferred (`approval_within_window`) |

At Stage 5 the proposal cannot change in-flight (no UI yet), so
`prohibit_silent_modification_after_confirmation` is a forward-
compatibility hook: always passes today, gates correctly when Stage 12's
approval UI lets a reviewer edit before approving.

`prohibit_policy_drift_after_confirmation` catches the case where the
worker restarts with new RULES during an approval wait. Concretely:

```
T+0     Worker boots with RULES vA → policy_hash = "abc..."
T+1m    Workflow drafts; pre_action_proposal under "abc"
T+1m    audit_log row: policy_hash="abc"; workflow stores self._policy_hash="abc"
T+1m    Workflow awaits approval signal
T+30m   Operator deploys; worker restarts with RULES vB → policy_hash = "def..."
T+1h    Human approves
T+1h    pre_execute computes current hash = "def"
        prohibit_policy_drift sees "abc" != "def" and re-evaluates pre_action_proposal
        under "def"; if a new rule now blocks → ESCALATE, re-approval required
```

`ESCALATE` not `BLOCK`: the human should see "policy tightened since
your approval, re-approve" rather than a silent rejection. "Fail open"
(grandfather under old policy) is a deliberate non-choice — build-plan
§Hard Rules #3.

## `attach_to_agent` — wired empty at Stage 5

```python
def attach_to_agent(
    agent: Agent[T],
    rules: Sequence[Rule],
    *,
    sink_factory: Callable[[], Awaitable[Sink]] | None = None,
) -> Agent[T]:
    """Wire compass rules for input_validation / output_validation as
    OpenAI Agents SDK guardrails on this agent.

    Each phase becomes one callback that calls evaluate() with the
    phase's rules. Rules for other phases are ignored (workflow-level
    phases are wired by the workflow). Returns the agent for chaining;
    mutates it in place.

    Stage 5: policies/send_invoice.py has zero rules at input_validation
    or output_validation; this function installs no-op callbacks. The
    mechanism is wired so Stage 6's scope-gate input_validation rules
    drop in without further engine work.
    """
```

Output-shape consistency checks (e.g., `total_cents == sum(line_total_cents)`)
live at `pre_action_proposal`, not `output_validation` — Pydantic
already enforces structure, and at `pre_action_proposal` we have a
sink + sequence counter set up. Build-plan §Phases v0.1 weight: output
validation is "a light structural check — Pydantic models already
enforce structure."

When Stage 6 adds input_validation rules to the scope-gate agent, the
`sink_factory` argument becomes load-bearing: the callback runs inside
an auto-wrapped Temporal activity and needs to open its own DB
connection per invocation. Stage 5 ships the parameter but no caller
exercises the non-None path.

## Workflow integration

Three changes to existing files; one new file.

### `workflows/send_invoice/context.py` (new)

Pure functions over `RunResult` that produce policy context dicts. No
I/O. Tested independently in `tests/workflows/send_invoice/test_context.py`.

### `workflows/send_invoice/types.py` — extended

```python
class EvaluatePolicyInput(BaseModel):
    workflow_run_id: str
    starting_sequence_no: int
    phase: Phase                        # new: which phase to evaluate
    context: dict[str, Any]             # new: the phase's context dict

class PolicyDecisionPayload(BaseModel):
    permit: bool
    policy_hash: str                    # new: hash captured this evaluation
    rule_ids_fired: list[str]
    escalations: list[dict[str, Any]]   # new: surfaced to approval UI
    next_sequence_no: int               # new: workflow advances its counter
```

The Stage-4 `PolicyDecision` Pydantic model is renamed to
`PolicyDecisionPayload` to keep the name `PolicyDecision` aligned with
the `compass.policy.Decision` type.

### `workflows/send_invoice/activities.py` — `evaluate_policy` body

Switches on phase. One activity, not three. Body sketch:

```python
@activity.defn
async def evaluate_policy(args: EvaluatePolicyInput) -> PolicyDecisionPayload:
    async with await psycopg.AsyncConnection.connect(_dsn()) as conn:
        try:
            policy_hash = await write_policy_snapshot(conn, "send_invoice", RULES)
            seq = SequenceAllocator(args.starting_sequence_no)
            sink = AuditLogSink(conn, args.workflow_run_id, seq, policy_hash)
            try:
                decision = await evaluate(RULES, args.phase, args.context, sink=sink)
            except PolicyEngineError as e:
                raise ApplicationError(
                    str(e), type="PolicyEngineError", non_retryable=not e.retryable,
                ) from e
            await conn.commit()
        except psycopg.Error as e:
            raise ApplicationError(
                str(e), type="PolicyInfraError", non_retryable=False,
            ) from e

    if not decision.permit:
        # Wrap the deterministic block as non-retryable at the boundary.
        raise ApplicationError(
            "policy blocked",
            type="PolicyDecisionError",
            non_retryable=True,
            details=[{"rule_ids": list(decision.rule_ids_fired),
                      "violations": [v.__dict__ for v in decision.violations]}],
        )
    return PolicyDecisionPayload(
        permit=True,
        policy_hash=policy_hash,
        rule_ids_fired=list(decision.rule_ids_fired),
        escalations=[v.__dict__ for v in decision.escalations],
        next_sequence_no=seq.peek(),
    )
```

Why one activity (not three per phase): one registration surface, one
DSN/connection path, one retry-policy mapping. The phase is a runtime
arg.

### `workflows/send_invoice/activities.py` — `audit_log` extension

```python
@activity.defn
async def audit_log(event: AuditEvent, *, is_terminal_event: bool = False) -> None:
    """Append one row to audit_log. If is_terminal_event=True, runs
    audit_validation rules over the candidate first; rule_fired events
    and the candidate row are all written in one transaction."""
```

Audit-validation runs against the candidate dict in-memory; rule_fired
events get sequenced from `event.sequence_no + 1` upward. Recursion
risk is avoided because the validation rule_fired events flow through
the **same sink** (in the same activity invocation), not via a
recursive `audit_log` activity call.

Audit-validation failures (BLOCK severity) post-commit raise
`PolicyDecisionError` so the workflow can flag itself as defective.
Stage 5 ships rules that only fire on workflow bugs (missing
policy_hash, empty tool_calls list), so production behavior is
"row written, no defect raised."

### `workflows/send_invoice/workflow.py` — workflow body

Three additions to `SendInvoiceWorkflow.run`:

1. After `Runner.run` returns, build the pre_action_proposal context
   via `workflows.send_invoice.context.*`. Capture
   `proposal_hash = sha256(canonical(proposal))` and store in
   `self._proposal_hash`.
2. After `evaluate_policy(phase=pre_action_proposal)` returns, store
   `self._policy_hash = payload.policy_hash`. After the approval signal
   lands and before `execute_send`, call `evaluate_policy(phase=pre_execute)`
   with context including `proposal_hash_at_proposal` and
   `policy_hash_at_proposal`. Same retry-policy mapping; same
   block-→-audit-→-END short-circuit.
3. The final `audit_log` activity call (the `executed` row on success)
   passes `is_terminal_event=True`.

## Activity failure semantics

Temporal does NOT replay an activity from mid-execution. If the activity
dies, the retry runs from the top. The design relies on three
idempotency mechanisms, all already in place from Stage 4:

| Write | Idempotency |
|---|---|
| `audit_log` (incl. rule_fired events) | `UNIQUE (workflow_run_id, sequence_no)` + `ON CONFLICT DO NOTHING` |
| `policy_snapshots` | PK `policy_hash` + `ON CONFLICT DO NOTHING` |
| `invoices` / `invoice_line_items` | PK derived from `workflow_run_id` + `ON CONFLICT DO NOTHING` |

Three real failure cases for `evaluate_policy`:

1. **Crash before `conn.commit()`.** Postgres rolls back the open
   transaction. Retry writes everything fresh and commits.
2. **Crash after commit, before return to Temporal.** Rows are
   persisted. Retry attempts the same INSERTs; `ON CONFLICT DO NOTHING`
   makes them no-ops. Retry computes the same `Decision` (predicates
   deterministic) and returns it.
3. **Predicate raises mid-evaluation.** Wrapped as `PolicyEngineError`
   → `ApplicationError(retryable)`. Temporal retries; predicate raises
   again; retries exhaust; activity fails cleanly. Workflow surfaces the
   error.

Sequence numbers stay stable across retries because the workflow
allocates `starting_sequence_no` and passes it to the activity as
input; Temporal records inputs deterministically, so the retry sees the
same starting value and writes the same `sequence_no`.

## `policies/send_invoice.py` — RULES list

```python
"""Send-invoice policy at v0.1.

This module is the authoritative policy for SendInvoiceWorkflow. RULES
is hashed once per evaluate_policy invocation and snapshotted to
policy_snapshots; every audit_log row carries the hash.

Rule ids are stable identifiers — they appear in audit_log.rule_id and
in historic queries. Renaming an in-use id breaks audit reads; treat
ids as append-only.
"""

from compass.policy import Rule, Phase, Severity
from compass.policy.primitives.evidence import require_evidence_citation
from compass.policy.primitives.identity import entity_status_equals
from compass.policy.primitives.resolution import require_existing_entity
from compass.policy.primitives.value import numeric_threshold
from compass.policy.primitives.approval import (
    prohibit_silent_modification_after_confirmation,
    prohibit_policy_drift_after_confirmation,
)
from compass.policy.primitives.audit import (
    log_policy_version,
    log_data_sources_consulted,
)
from workflows.send_invoice.primitives import (   # @primitive side effect at import
    require_amount_source,
    contract_consistency_check,
    prohibit_exceed_contract_cap,
    currency_consistency_check,
)

RULES: list[Rule] = [
    # ---- pre_action_proposal — bulk of the policy load ----
    Rule(id="customer_must_exist", phase=Phase.pre_action_proposal,
         predicate=require_existing_entity(
             field="resolved_entities.customer", entity_type="customer"),
         regulatory_basis=("internal SOP-CUST-01",),
         tags=("resolution",), must_be_covered=True),
    Rule(id="customer_kyc_verified", phase=Phase.pre_action_proposal,
         predicate=entity_status_equals(
             field="resolved_entities.customer.kyc_status",
             expected_status="verified"),
         regulatory_basis=("BSA §326",),
         tags=("kyc", "BSA"), must_be_covered=True),
    Rule(id="invoice_amount_cap", phase=Phase.pre_action_proposal,
         predicate=numeric_threshold(field="proposal.total_cents", max=10_000_000),
         severity=Severity.ESCALATE,             # > $100k → human review
         regulatory_basis=("internal SOP-BILL-04",),
         tags=("amount_threshold",)),
    Rule(id="require_amount_source", phase=Phase.pre_action_proposal,
         predicate=require_amount_source(),
         regulatory_basis=("internal SOP-BILL-02",),
         tags=("billing_integrity",), must_be_covered=True),
    Rule(id="require_evidence_citation", phase=Phase.pre_action_proposal,
         predicate=require_evidence_citation(field="proposal.line_items[*].source_refs"),
         regulatory_basis=("internal SOP-BILL-02",),
         tags=("billing_integrity", "evidence"), must_be_covered=True),
    Rule(id="contract_consistency", phase=Phase.pre_action_proposal,
         predicate=contract_consistency_check(),
         regulatory_basis=("internal SOP-BILL-03",),
         tags=("billing_integrity",), must_be_covered=True),
    Rule(id="prohibit_exceed_contract_cap", phase=Phase.pre_action_proposal,
         predicate=prohibit_exceed_contract_cap(),
         regulatory_basis=("internal SOP-BILL-03",),
         tags=("billing_integrity",), must_be_covered=True),
    Rule(id="currency_consistency", phase=Phase.pre_action_proposal,
         predicate=currency_consistency_check(),
         regulatory_basis=("internal SOP-BILL-05",),
         tags=("billing_integrity",), must_be_covered=True),

    # ---- pre_execute — drift detection ----
    Rule(id="no_silent_modification_after_confirmation", phase=Phase.pre_execute,
         predicate=prohibit_silent_modification_after_confirmation(),
         regulatory_basis=("internal SOP-CTRL-01",),
         tags=("integrity",)),
    Rule(id="no_policy_drift_after_confirmation", phase=Phase.pre_execute,
         predicate=prohibit_policy_drift_after_confirmation(),
         severity=Severity.ESCALATE,             # tightened policy → re-approval
         regulatory_basis=("internal SOP-CTRL-02",),
         tags=("integrity",)),

    # ---- audit_validation — completeness of terminal audit row ----
    Rule(id="audit_has_policy_version", phase=Phase.audit_validation,
         predicate=log_policy_version(),
         regulatory_basis=("internal SOP-AUDIT-01",),
         tags=("audit_completeness",)),
    Rule(id="audit_has_data_sources", phase=Phase.audit_validation,
         predicate=log_data_sources_consulted(),
         regulatory_basis=("internal SOP-AUDIT-01",),
         tags=("audit_completeness",)),
]
```

12 rules total. Every Billing integrity rule carries
`must_be_covered=True` so Stage 10's CI gate catches dead-code
regressions in that family (build-plan §v0.1 success criteria #9).

## Tests

### `tests/compass/policy/` — engine + primitives

Pure unit tests. Use `InMemorySink` fixture; no Postgres dependency.

- `test_engine.py` — phase filtering, severity routing
  (BLOCK → permit=False, ESCALATE → permit=True + escalations), sink
  emit ordering matches declaration order, `PolicyEngineError` wraps
  predicate exceptions.
- `test_hash.py` — hash stability under reordering of `params` dict
  keys; hash sensitivity to primitive `params` (`max=10` ≠ `max=11`);
  hash insensitivity to predicate function identity.
- `test_registry.py` — `@primitive` duplicate-name rejection;
  `list_primitives()` shape.
- `test_rule_validation.py` — `Rule.__post_init__` raises on
  `Severity.ESCALATE` at `Phase.input_validation` or
  `Phase.output_validation`.
- `primitives/test_*.py` — one file per primitive: above/below/exact
  cases for `numeric_threshold`; missing/present/None cases for
  resolution; happy/missing-field cases for evidence; etc.

### `tests/policies/test_send_invoice_rules.py` — RULES-driven

Imports `RULES` from `policies.send_invoice`; drives `evaluate()`
directly with fixture context dicts. No Temporal, no workflow.

| Fixture | Expected outcome |
|---|---|
| Happy proposal, all source_refs present | `permit=True`, all eight pre_action_proposal rules emit `rule_skipped` |
| Line item with empty `source_refs` | `require_evidence_citation` fires, BLOCK |
| Line item with invalid `source_type` | `require_amount_source` fires, BLOCK |
| Customer with `kyc_status="pending"` | `customer_kyc_verified` fires, BLOCK |
| Customer absent from `resolved_entities` | `customer_must_exist` fires, BLOCK |
| `total_cents=15_000_000` (above cap) | `invoice_amount_cap` fires, ESCALATE (permit=True) |
| Mismatched currency proposal vs contract | `currency_consistency` fires, BLOCK |
| Line item hours > `monthly_hour_cap` | `prohibit_exceed_contract_cap` fires, BLOCK |
| Proposal billing structure ≠ contract billing structure | `contract_consistency` fires, BLOCK |
| `proposal_hash_at_proposal` matches current hash (pre_execute) | `no_silent_modification_after_confirmation` skipped |
| `policy_hash_at_proposal` ≠ current hash (pre_execute) | `no_policy_drift_after_confirmation` fires, ESCALATE |
| Audit candidate missing `policy_hash` | `audit_has_policy_version` fires, BLOCK |
| Audit candidate with empty `tool_calls` | `audit_has_data_sources` fires, BLOCK |

Each case asserts (a) `Decision.permit`, (b) `rule_ids_fired` matches
the expected set, (c) the `InMemorySink` recorded one event per same-
phase rule (no missed emits).

### `tests/workflows/send_invoice/test_context.py` — pure-function tests

`extract_tool_calls`, `project_resolved_entities`,
`extract_reasoning_text` against synthetic `RunResult`-shaped inputs.
No Temporal; no agent run.

### `tests/workflows/send_invoice/test_workflow_policy.py` — end-to-end

Extends the Stage-4 `WorkflowEnvironment.start_time_skipping` +
`AgentEnvironment` + `TestModel` machinery. Each test parameterizes the
`TestModel`'s canned proposal JSON to drive the gate through a specific
path:

| Test | TestModel proposal | Expected `WorkflowResult.outcome` | Audit assertions |
|---|---|---|---|
| `test_passing_proposal_executes` | happy proposal | `sent` | audit_log has 8 `rule_skipped` rows at pre_action_proposal phase, then `approval_signal`, `executed`; `policy_hash` non-null on every row; matching `policy_snapshots` row exists |
| `test_missing_source_refs_blocked` | line_item with `source_refs=[]` | `policy_rejected` | `rule_fired` row with `rule_id="require_evidence_citation"` |
| `test_currency_mismatch_blocked` | proposal currency ≠ contract currency | `policy_rejected` | `rule_id="currency_consistency"` |
| `test_amount_above_cap_escalates_but_executes` | `total_cents=15_000_000` | `sent` | `rule_fired` with `rule_id="invoice_amount_cap"` and `decision="escalate"`; workflow still completes |
| `test_pre_execute_drift_escalates` | happy proposal; manually corrupt `self._policy_hash` to simulate drift | `sent` | `rule_fired` at pre_execute with `rule_id="no_policy_drift_after_confirmation"` |
| `test_policy_snapshot_written_once` | run two workflows in sequence with same RULES | both `sent` | exactly one `policy_snapshots` row for the RULES hash |

The fail/escalate fixtures live in the test module — small,
self-contained dicts. The mocked `TestModel` machinery from Stage 4's
`conftest.py` doesn't need to change; only the canned proposals do.

## Open issues / future work

- **Stringly-typed context paths.** Documented above. A
  boot-time `validate_rules(RULES)` helper would catch path typos before
  serving traffic; deferred.
- **`attach_to_agent` sink threading.** When Stage 6 (scope gate) drops
  in real input_validation rules, the `sink_factory` argument starts
  doing work. Likely needs a connection-pool sink at that point.
- **World-state refresh at pre_execute.** Discussed and explicitly
  deferred to a withdraw-approval signal mechanism. Documented so future
  contributors don't add it as silent re-evaluation.
- **`POLICY.md` autogeneration.** Build-plan §Stage 5 mentions a
  POLICY.md drafted via `list_primitives()` + docstrings; deferred to
  Stage 13 documentation polish. The data source (`list_primitives()`)
  ships at Stage 5.
