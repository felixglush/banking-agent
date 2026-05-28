"""Stage 7: typed shapes shared across compass.eval. case_id is the join
key everywhere; outcome strings come from ground-truth ``expected_outcome``."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

Outcome = Literal["sent", "declined", "policy_rejected", "timeout", "unsupported"]


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


@dataclass(frozen=True)
class CaseResult:
    """Returned by WorkflowRunner.run_case."""

    case_id: str
    workflow_run_id: str
    outcome: Outcome
    invoice_id: str | None
    detail: str | None
