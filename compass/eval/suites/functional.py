"""Functional accuracy suite.

Outcome-class match is the first gate. For sent cases, fields are
exact-match (total_cents is integer cents so tolerance bands aren't
needed at v0.1)."""

from dataclasses import dataclass
from typing import Any

from compass.eval.types import Case, CaseResult

_FIELDS = ("customer_id", "contract_id", "currency", "source_type", "total_cents")


@dataclass(frozen=True)
class SuiteScore:
    passed: bool
    comment: str  # empty on pass, failure reason on fail


async def score_functional(
    *,
    case: Case,
    result: CaseResult,
    persisted_invoice: dict[str, Any] | None,
) -> SuiteScore:
    if result.outcome != case.expected_outcome:
        return SuiteScore(
            passed=False,
            comment=f"outcome_class_mismatch:got={result.outcome},expected={case.expected_outcome}",
        )
    if case.expected_outcome != "sent":
        return SuiteScore(passed=True, comment="")
    if persisted_invoice is None:
        return SuiteScore(
            passed=False,
            comment="invoice_missing_for_sent_case",
        )
    diffs = [f for f in _FIELDS if persisted_invoice.get(f) != case.expected.get(f)]
    if diffs:
        return SuiteScore(passed=False, comment=f"field_mismatch:{diffs}")
    return SuiteScore(passed=True, comment="")
