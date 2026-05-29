"""Stage 7: typed shapes shared across compass.eval. case_id is the join
key everywhere; outcome strings come from ground-truth ``expected_outcome``."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

Outcome = Literal[
    "sent", "declined", "policy_rejected", "timeout", "unsupported", "needs_clarification"
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
