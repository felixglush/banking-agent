"""Public types for the Compass policy engine.

Every type here is part of the public API and re-exported from
``compass.policy``. Renaming or removing one is a breaking change.

See docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md
§Types for design rationale (why Predicate is a dataclass not a bare
callable, why ESCALATE is rejected at OpenAI Agents SDK-bound phases).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, TypedDict

# ---------------------------------------------------------------------
# Semantic aliases over open dict shapes
# ---------------------------------------------------------------------
#
# These are not new types — they are *names* for shapes the codebase
# already uses. Pyright treats them as the underlying ``Mapping`` /
# ``dict``, so substitutability is unchanged. The benefit is at the
# call site: a function signature that takes ``PolicyContext`` reads
# very differently from one that takes ``Mapping[str, Any]``, even
# though the runtime contract is identical.
#
# The shapes themselves are intentionally open. ``PolicyContext`` keys
# are dotted-path-looked-up by primitive predicates (see
# ``compass/policy/paths.py``) and vary by phase (see the per-phase
# context blocks in the Stage-5 and Stage-6 design docs). Tightening
# them to per-phase TypedDicts is a documented follow-up; the engine
# API would have to thread phase identity through the type system,
# which is a separate refactor.

PolicyContext = Mapping[str, Any]
"""What a predicate receives at evaluation time.

Shape varies by phase. Predicates read fields via
``compass.policy.paths.resolve_dotted`` against the dotted-path
schemas documented in the design specs.
"""

ViolationEvidence = dict[str, Any]
"""Rule-specific structured evidence carried on a Violation.

JSON-serializable. Keys are rule-defined; readers (audit log,
approval UI) treat them as opaque payloads.
"""

AuditPayload = dict[str, Any]
"""JSON-serializable payload landing in ``audit_log.payload``.

Free-form per event_kind. Stable conventions per event_kind are
documented in build-plan §Database and the Stage-6 design.
"""

Actor = dict[str, Any]
"""Verified human identity on an approval / executed / declined row.

Keys: ``user_id``, optional ``role``, ``auth_method``, optional
``mfa_verified``. Always None on rule_fired / rule_skipped events.
"""


class ToolCallRecord(TypedDict):
    """One MCP / agent tool invocation as it appears in a policy context.

    Produced by the workflow from the OpenAI Agents SDK's RunResult;
    consumed by Evidence / Billing-integrity primitives and by the
    audit_validation ``log_data_sources_consulted`` rule. The tool name
    is the registered MCP tool id (e.g. ``"list_customers"``); args is
    the JSON-decoded argument dict the agent passed; result is the
    JSON-decoded tool output (shape varies per tool).
    """

    tool_name: str
    args: dict[str, Any]
    result: Any


class RuleSkippedEvent(TypedDict):
    """Engine emitted this event when a rule's predicate returned None.

    Discriminator: ``event_kind == "rule_skipped"``.
    """

    event_kind: Literal["rule_skipped"]
    rule_id: str
    phase: str


class RuleFiredEvent(TypedDict):
    """Engine emitted this event when a rule's predicate returned a Violation.

    Discriminator: ``event_kind == "rule_fired"``. Carries the full
    violation context so audit and trace assertions don't need to join
    back to source.
    """

    event_kind: Literal["rule_fired"]
    rule_id: str
    phase: str
    decision: str
    evidence: ViolationEvidence
    message: str
    regulatory_basis: list[str]


SinkEvent = RuleFiredEvent | RuleSkippedEvent
"""Discriminated union over the two event_kind values the engine emits.

Narrow with ``if event["event_kind"] == "rule_fired":`` to access
``evidence``, ``decision``, ``message``, ``regulatory_basis``.
"""


class Phase(StrEnum):
    """Where in the workflow lifecycle a rule fires.

    StrEnum so equality to ``audit_log.phase`` (TEXT column) is direct.
    """

    input_validation = "input_validation"
    output_validation = "output_validation"
    pre_action_proposal = "pre_action_proposal"
    pre_execute = "pre_execute"
    audit_validation = "audit_validation"


class Severity(StrEnum):
    """What happens when a rule fires.

    BLOCK short-circuits the workflow to audit-and-reject; ESCALATE
    routes to human review with the violation surfaced. ESCALATE is only
    realizable at workflow-level phases — OpenAI Agents SDK guardrails
    are tripwire-or-nothing by contract.
    """

    BLOCK = "block"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class Violation:
    """A predicate's report that its rule fired.

    Predicates construct this with ``rule_id=""``; the engine fills the
    real ``rule_id`` from the surrounding Rule. ``evidence`` is rule-
    specific structured data that lands in ``audit_log.payload`` — keep
    it small, keep it JSON-serializable, name keys so a future reader
    knows what they mean without source code.
    """

    rule_id: str
    message: str
    evidence: ViolationEvidence


@dataclass(frozen=True)
class Decision:
    """The engine's verdict for one ``(phase, context)`` evaluation.

    ``permit=False`` only when at least one BLOCK rule fired.
    Escalations route to human review but do not flip ``permit`` — the
    workflow already gates on a human signal, so an escalation surfaces
    in the approval UI with the violation visible.
    """

    permit: bool
    violations: tuple[Violation, ...]
    escalations: tuple[Violation, ...]
    rule_ids_fired: tuple[str, ...]


PredicateFn = Callable[
    [PolicyContext],
    Awaitable[Violation | None] | (Violation | None),
]


@dataclass(frozen=True)
class Predicate:
    """The check a Rule actually runs. Returned by a primitive factory.

    Constructed by primitive factories — not directly. The factory
    decorated with ``@primitive("foo")`` returns a Predicate whose
    ``primitive_name="foo"`` and ``params={...kwargs passed to factory}``
    so the rule's identity and configuration are introspectable for
    hashing, coverage, and audit reconstruction.

    Sync and async predicate bodies are both supported; the wrapper
    awaits as needed. Use async when the body calls ``Runner.run`` for
    an LLM-judge — Temporal's OpenAIAgentsPlugin wraps those calls as
    activities, which is what keeps the eval boundary replay-safe.
    """

    primitive_name: str
    params: Mapping[str, Any]
    fn: PredicateFn

    async def __call__(self, ctx: PolicyContext) -> Violation | None:
        result = self.fn(ctx)
        if inspect.isawaitable(result):
            return await result
        return result


@dataclass(frozen=True)
class Rule:
    """One constraint inside a policy.

    The ``id`` is referenced by ``audit_log.rule_id``, by trace
    assertions in the eval framework, and by the coverage report — it
    is the stable handle on this rule across audit retention windows
    (7+ years for banking). Renaming an id in use breaks historic
    queries; treat ids as append-only.

    The ``phase`` implicitly determines what's in the context dict the
    predicate receives — see spec §Context schemas.

    ``regulatory_basis`` is denormalized into every ``rule_fired``
    event's ``payload`` so 5-year-old audit rows are interpretable
    without joining back to source.

    ``must_be_covered=True`` flags the rule for the Stage-10 CI gate
    that fails the build if the holdout corpus doesn't exercise it.

    ``surface_to_user=True`` lets the approval UI display the violation
    message; set False for internal-only rules (none ship at Stage 5).
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
            Phase.input_validation,
            Phase.output_validation,
        }:
            raise ValueError(
                f"Rule {self.id!r}: ESCALATE is not realizable at phase "
                f"{self.phase.value} — OpenAI Agents SDK guardrails are "
                "tripwire-only. Use BLOCK, or move the rule to a "
                "workflow-level phase (pre_action_proposal, pre_execute, "
                "audit_validation)."
            )
