# `compass.policy` — module diagram

Mermaid view of the policy engine: how a rule is authored, how
`evaluate()` runs it, and where the result lands. For the end-to-end
workflow integration (Temporal activities, the five phases, audit row
lifecycle) see [`POLICY_ENGINE_DIAGRAM.md`](POLICY_ENGINE_DIAGRAM.md);
for design rationale see
[`superpowers/specs/2026-05-27-stage-5-policy-engine-design.md`](superpowers/specs/2026-05-27-stage-5-policy-engine-design.md).

## 1. The pieces and how they connect

Authoring (left) happens at import time; evaluation (right) happens once
per phase. The engine reads `RULES` + a `PolicyContext`, returns a
`Decision`, and emits one event per evaluated rule to a `Sink`.
`hash_rules` / `write_policy_snapshot` make the rule set reconstructable
from the `policy_hash` carried on every audit row.

```mermaid
flowchart TD
    subgraph Author["Authoring · import time"]
        direction TB
        Prim["@primitive(name)<br/>factory(kwargs) → PredicateFn"]:::author
        Reg[("_REGISTRY<br/>name → factory")]:::author
        Pred["Predicate<br/>primitive_name · params · fn"]:::author
        Rule["Rule<br/>id · phase · predicate<br/>severity · regulatory_basis"]:::author
        Policy["RULES — Sequence[Rule]<br/>the policy for a workflow"]:::author
        Prim -. registers .-> Reg
        Prim -->|"call with kwargs"| Pred
        Pred -->|bound into| Rule
        Rule -->|collected into| Policy
    end

    Ctx["PolicyContext<br/>dotted-path dict · varies by phase"]:::data
    Engine{{"evaluate(rules, phase, ctx, sink)"}}:::engine
    Decision["Decision<br/>permit · violations<br/>escalations · rule_ids_fired"]:::engine
    Sink{{"Sink.emit(event)"}}:::sink
    Anchor["hash_rules → policy_hash<br/>write_policy_snapshot → policy_snapshots"]:::anchor

    Policy --> Engine
    Ctx --> Engine
    Engine --> Decision
    Engine -. "emit() per evaluated rule" .-> Sink
    Policy -->|reconstructability anchor| Anchor

    classDef author fill:#fce7f3,stroke:#be185d,color:#111
    classDef engine fill:#dcfce7,stroke:#15803d,color:#111
    classDef sink fill:#ffedd5,stroke:#c2410c,color:#111
    classDef anchor fill:#ede9fe,stroke:#6d28d9,color:#111
    classDef data fill:#dbeafe,stroke:#1d4ed8,color:#111
```

## 2. Inside `evaluate()`

Walks rules in declaration order, runs each whose `phase` matches, emits
`rule_skipped` (predicate returned `None`) or `rule_fired` (returned a
`Violation`). `permit` is false only when a **BLOCK** rule fired;
ESCALATE surfaces a violation for human review but does not block. A
predicate that raises is wrapped as a retryable `PolicyEngineError`.

```mermaid
flowchart TD
    Start(["evaluate(rules, phase, ctx, sink)"]):::engine --> Loop{"more rules?<br/>declaration order"}
    Loop -->|no| Done["permit = no BLOCK violation fired"]:::engine
    Done --> Ret(["return Decision"]):::engine
    Loop -->|yes| Match{"rule.phase == phase?"}
    Match -->|no| Loop
    Match -->|yes| Run["await rule.predicate(ctx)"]:::engine
    Run -->|raises| Err["wrap as PolicyEngineError<br/>retryable"]:::error
    Run -->|"None"| Skip["sink.emit(rule_skipped)"]:::sink
    Skip --> Loop
    Run -->|"Violation"| Fill["fill rule_id from rule<br/>append to rule_ids_fired"]:::engine
    Fill --> Sev{"rule.severity"}
    Sev -->|BLOCK| Vio["violations += v<br/>sets permit = False"]:::engine
    Sev -->|ESCALATE| Esc["escalations += v<br/>does NOT block"]:::engine
    Vio --> Fire["sink.emit(rule_fired)"]:::sink
    Esc --> Fire
    Fire --> Loop

    classDef engine fill:#dcfce7,stroke:#15803d,color:#111
    classDef sink fill:#ffedd5,stroke:#c2410c,color:#111
    classDef error fill:#fee2e2,stroke:#b91c1c,color:#111
```

## 3. Sinks

One method — `async emit(event)`. The engine never imports psycopg; the
sink decides where events land. `AuditLogSink` turns each event into one
`audit_log` INSERT (sequence numbers from `SequenceAllocator`, idempotent
via `ON CONFLICT DO NOTHING`).

```mermaid
classDiagram
    class Sink {
        <<Protocol>>
        +emit(event)
    }
    class InMemorySink {
        +events
        +emit(event)
    }
    class NullSink {
        +emit(event)
    }
    class MultiSink {
        -sinks
        +emit(event)
    }
    class AuditLogSink {
        -conn
        -allocator
        -policy_hash
        +emit(event)
    }
    Sink <|.. InMemorySink : tests
    Sink <|.. NullSink : default no-op
    Sink <|.. MultiSink : fan-out
    Sink <|.. AuditLogSink : INSERT audit_log
    MultiSink o-- Sink : wraps many
```

## 4. Error taxonomy

`retryable` is positive ("True means retry"); the activity boundary in
`workflows/send_invoice/activities.py` is the one place it is negated to
Temporal's `non_retryable=`.

| Error | Raised when | `retryable` |
| --- | --- | --- |
| `PolicyDecisionError` | A BLOCK/ESCALATE rule decided no | `False` — deterministic |
| `PolicyEngineError` | Predicate raised, primitive unregistered, malformed context | `True` |
| `PolicyInfraError` | Snapshot write / fact-loading hit Postgres outage | `True` |

## Vocabulary

Phase · Rule · Policy · Predicate · Primitive · Sink · Engine · Snapshot —
one sentence each in [`POLICY_ENGINE_DIAGRAM.md` §7](POLICY_ENGINE_DIAGRAM.md).
