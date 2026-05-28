"""Compass policy engine — public API.

See docs/build-plan.md §Policy Engine + Primitive Library and
docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md.
"""

from compass.policy.agent import attach_to_agent
from compass.policy.audit_sink import AuditLogSink, SequenceAllocator
from compass.policy.engine import (
    evaluate,
    evaluate_audit_validation,
    evaluate_pre_action_proposal,
    evaluate_pre_execute,
)
from compass.policy.errors import (
    PolicyDecisionError,
    PolicyEngineError,
    PolicyInfraError,
)
from compass.policy.hashing import canonicalize_rule, hash_rules, serialize_rules
from compass.policy.registry import list_primitives, primitive
from compass.policy.sink import (
    InMemorySink,
    MultiSink,
    NullSink,
    Sink,
    clear_sinks,
    register_sink,
)
from compass.policy.snapshot import write_policy_snapshot
from compass.policy.types import (
    Actor,
    AuditPayload,
    Decision,
    Phase,
    PolicyContext,
    Predicate,
    Rule,
    RuleFiredEvent,
    RuleSkippedEvent,
    Severity,
    SinkEvent,
    ToolCallRecord,
    Violation,
    ViolationEvidence,
)

__all__ = [
    "Actor",
    "AuditPayload",
    "Decision",
    "Phase",
    "PolicyContext",
    "PolicyDecisionError",
    "PolicyEngineError",
    "PolicyInfraError",
    "Predicate",
    "Rule",
    "RuleFiredEvent",
    "RuleSkippedEvent",
    "Severity",
    "SinkEvent",
    "ToolCallRecord",
    "Violation",
    "ViolationEvidence",
    "canonicalize_rule",
    "hash_rules",
    "serialize_rules",
    "InMemorySink",
    "MultiSink",
    "NullSink",
    "Sink",
    "clear_sinks",
    "evaluate",
    "evaluate_audit_validation",
    "evaluate_pre_action_proposal",
    "evaluate_pre_execute",
    "list_primitives",
    "primitive",
    "register_sink",
    "AuditLogSink",
    "SequenceAllocator",
    "attach_to_agent",
    "write_policy_snapshot",
]
