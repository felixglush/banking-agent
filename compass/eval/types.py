"""Stage 7: typed shapes shared across compass.eval. case_id is the join
key everywhere; outcome strings come from ground-truth ``expected_outcome``."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, cast

Outcome = Literal[
    "sent", "declined", "policy_rejected", "timeout", "unsupported", "needs_clarification"
]

AdversarialBucket = Literal[
    "repelled_by_policy",
    "repelled_by_prompt",
    "leaked_rule_fired",
    "leaked_no_rule",
]


class Mode(StrEnum):
    train = "train"
    holdout = "holdout"


@dataclass(frozen=True)
class Case:
    """One row from the JSONL corpus."""

    case_id: str
    request: str
    expected_outcome: Outcome
    expected: dict[str, Any]
    expected_fired_rules: list[str]
    expected_decline_reason: str | None
    # When set, the request is deliberately ambiguous: the runner sends this as
    # the answer to the agent's clarification question (a `clarify` signal), and
    # the agent is expected to then draft the specific invoice in `expected`.
    clarify_answer: str | None = None


@dataclass(frozen=True)
class CaseResult:
    """Returned by WorkflowRunner.run_case."""

    case_id: str
    workflow_run_id: str
    outcome: Outcome
    invoice_id: str | None
    detail: str | None
    # Langfuse trace id for this case's workflow execution. The runner
    # seeds it deterministically from workflow_run_id so the harness can
    # link the trace to its Dataset Run item without a tag lookup. None
    # when tracing is disabled (no Langfuse env vars).
    trace_id: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of driving one adversarial attack to the pre_action_proposal gate.

    ``gate_decision`` is the gate verdict ("permitted" means a bad proposal got
    PAST the gate — an attack success), not the post-decline workflow outcome.
    """

    workflow_run_id: str
    trace_id: str | None
    gate_decision: str
    proposal: dict[str, Any] | None
    detail: str | None


# One generated adversarial attack (a Promptfoo assertion list, open shape).
GraderAssert = list[dict[str, Any]]
# Test-level metadata the grader needs (e.g. promptfoo:redteam:policy requires
# ``purpose`` + ``policy``). Carried verbatim from generate to grade.
GraderMetadata = dict[str, Any]


@dataclass(frozen=True)
class Attack:
    """One generated adversarial attack, extracted from the Promptfoo red-team
    corpus. ``category`` is a grouping label (recovered from the source policy /
    pluginId). ``grader_assert`` is the generator's own grader (e.g. the native
    ``promptfoo:redteam:policy`` grader or an llm-rubric), and ``grader_metadata``
    is the test metadata that grader needs (purpose, policy, pluginConfig). Both
    are carried through stage 2 and re-applied verbatim by the echo grade stage
    so the pass/fail criterion is the one Promptfoo authored."""

    case_id: str
    category: str
    prompt: str
    grader_assert: GraderAssert
    grader_metadata: GraderMetadata = field(default_factory=lambda: cast(GraderMetadata, {}))


@dataclass(frozen=True)
class ProbeOutput:
    """Stage-2 result: one attack driven to the gate, plus the policy-fire signal
    read from the audit log. ``any_rule_fired`` records whether *any* gate rule
    fired (not a specific expected one) — it splits the failure-pattern buckets
    into control-worked vs. got-lucky and gate-bug vs. coverage-gap. Feeds the
    echo grade config (stage 3) and scoring."""

    case_id: str
    category: str
    attack: str
    grader_assert: GraderAssert
    rendered_output: str
    gate_decision: str
    workflow_run_id: str | None
    trace_id: str | None
    any_rule_fired: bool
    grader_metadata: GraderMetadata = field(default_factory=lambda: cast(GraderMetadata, {}))
