"""Compass policy engine — the ``evaluate`` core.

Pure, async. Walks ``rules`` in declaration order, runs each whose
``phase`` matches, emits one event per evaluated rule to ``sink``,
buckets violations by severity, returns a Decision.

The function itself does no I/O — sinks do. Predicates may invoke
sub-agent ``Runner.run`` calls; those are activity-wrapped by the
OpenAIAgentsPlugin so the engine's purity isn't violated.

See spec §Engine — evaluate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from compass.policy.errors import PolicyEngineError
from compass.policy.sink import Sink
from compass.policy.types import Decision, Phase, Rule, Severity, Violation


async def evaluate(
    rules: Sequence[Rule],
    phase: Phase,
    context: Mapping[str, Any],
    *,
    sink: Sink,
) -> Decision:
    """Run every rule whose phase matches; return the aggregate Decision."""
    violations: list[Violation] = []
    escalations: list[Violation] = []
    rule_ids_fired: list[str] = []

    for rule in rules:
        if rule.phase != phase:
            continue
        try:
            outcome = await rule.predicate(context)
        except Exception as exc:  # noqa: BLE001 — wrap *any* predicate raise
            raise PolicyEngineError(rule_id=rule.id, cause=exc) from exc

        if outcome is None:
            await sink.emit(
                {
                    "event_kind": "rule_skipped",
                    "rule_id": rule.id,
                    "phase": phase.value,
                }
            )
            continue

        # Predicate constructed Violation with rule_id=""; fill in from rule.
        violation = Violation(
            rule_id=rule.id,
            message=outcome.message,
            evidence=outcome.evidence,
        )
        rule_ids_fired.append(rule.id)
        if rule.severity is Severity.ESCALATE:
            escalations.append(violation)
        else:
            violations.append(violation)

        await sink.emit(
            {
                "event_kind": "rule_fired",
                "rule_id": rule.id,
                "phase": phase.value,
                "decision": rule.severity.value,
                "evidence": violation.evidence,
                "message": violation.message,
                "regulatory_basis": list(rule.regulatory_basis),
            }
        )

    permit = len(violations) == 0  # escalations do not block
    return Decision(
        permit=permit,
        violations=tuple(violations),
        escalations=tuple(escalations),
        rule_ids_fired=tuple(rule_ids_fired),
    )


# ---- phase-specific wrappers --------------------------------------


async def evaluate_pre_action_proposal(
    rules: Sequence[Rule],
    context: Mapping[str, Any],
    *,
    sink: Sink,
) -> Decision:
    return await evaluate(rules, Phase.pre_action_proposal, context, sink=sink)


async def evaluate_pre_execute(
    rules: Sequence[Rule],
    context: Mapping[str, Any],
    *,
    sink: Sink,
) -> Decision:
    return await evaluate(rules, Phase.pre_execute, context, sink=sink)


async def evaluate_audit_validation(
    rules: Sequence[Rule],
    context: Mapping[str, Any],
    *,
    sink: Sink,
) -> Decision:
    return await evaluate(rules, Phase.audit_validation, context, sink=sink)
