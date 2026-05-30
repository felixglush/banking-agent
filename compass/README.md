# `compass`

The reusable framework core, independent of any one workflow.

| Subpackage | What it owns |
| --- | --- |
| [`policy/`](policy/) | The policy engine — `evaluate()`, the `@primitive` registry, `Rule`/`Predicate`/`Decision` types, sinks, rule-set hashing, and `policy_snapshots` writes. The framework-core primitives live in [`policy/primitives/`](policy/primitives/). |
| [`eval/`](eval/) | The eval harness (Stage 7+) — corpus loading, orchestration, scoring, and the adversarial Promptfoo entry point. |

## Diagrams

- [`docs/COMPASS_POLICY_DIAGRAM.md`](../docs/COMPASS_POLICY_DIAGRAM.md) — mermaid view of `compass.policy`: authoring a rule, the `evaluate()` loop, sinks, error taxonomy.
- [`docs/POLICY_ENGINE_DIAGRAM.md`](../docs/POLICY_ENGINE_DIAGRAM.md) — ASCII view of the end-to-end workflow integration: the five phases, the `evaluate_policy` activity, and the audit-row lifecycle.
