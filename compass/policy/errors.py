"""Compass policy exception taxonomy.

Three exception types with different retry semantics. The ``retryable``
attribute is positive ("True means retry"); the one place we negate it
to Temporal's ``non_retryable=`` is the activity boundary in
``workflows/send_invoice/activities.py``.

See spec §Errors for the full taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from compass.policy.types import Decision


@dataclass
class PolicyDecisionError(Exception):
    """A rule decided to block or escalate.

    Deterministic — retrying the same predicate against the same context
    yields the same answer. Never retryable.
    """

    decision: Decision
    retryable: ClassVar[bool] = False

    def __str__(self) -> str:
        rule_ids = ", ".join(self.decision.rule_ids_fired)
        return f"policy blocked: rule_ids_fired=[{rule_ids}]"


@dataclass
class PolicyEngineError(Exception):
    """The engine itself failed to evaluate.

    Causes: predicate raised, primitive not registered, malformed
    context. Transient causes are plausible (an LLM-judge sub-agent
    timing out, transient registry contention) so this is retryable.
    """

    rule_id: str | None
    cause: BaseException | None
    retryable: ClassVar[bool] = True

    def __str__(self) -> str:
        head = f"engine error in rule {self.rule_id!r}" if self.rule_id else "engine error"
        return f"{head}: {self.cause}" if self.cause else head


@dataclass
class PolicyInfraError(Exception):
    """A pre-loop fact-loading activity or snapshot write failed.

    Postgres outage, network blip, etc. Retryable, but typed separately
    from PolicyEngineError so on-call can tell "policy decided no" from
    "database is down".
    """

    cause: BaseException
    retryable: ClassVar[bool] = True

    def __str__(self) -> str:
        return f"policy infra error: {self.cause}"
