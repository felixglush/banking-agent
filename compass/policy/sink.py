"""Sink — where the engine sends rule_fired / rule_skipped events.

The engine emits one event per evaluated rule. Sinks decide where the
event lands: an in-memory list for unit tests, an audit_log row for
production, fan-out for both at once. Decoupling means the engine
doesn't know about Postgres, and tests don't need a database.

Three sinks ship at Stage 5:

* InMemorySink — for tests
* NullSink — default when no sink registered; discards
* MultiSink — fan-out
* AuditLogSink — in compass/policy/audit_sink.py (DB-backed)

See spec §Sink for the architectural rationale.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from compass.policy.types import SinkEvent


@runtime_checkable
class Sink(Protocol):
    """One method: emit an event dict."""

    async def emit(self, event: SinkEvent) -> None: ...


class InMemorySink:
    """Collect events in a list. Use in unit tests."""

    def __init__(self) -> None:
        self.events: list[SinkEvent] = []

    async def emit(self, event: SinkEvent) -> None:
        self.events.append(event)


class NullSink:
    """Drop events on the floor. Default when nothing is registered."""

    async def emit(self, event: SinkEvent) -> None:
        return None


class MultiSink:
    """Fan an event out to every wrapped sink."""

    def __init__(self, sinks: Iterable[Sink]) -> None:
        self._sinks: list[Sink] = list(sinks)

    async def emit(self, event: SinkEvent) -> None:
        for sink in self._sinks:
            await sink.emit(event)


# ---- module-level registry (process-wide additive sinks) ----

_REGISTERED: list[Sink] = []


def register_sink(sink: Sink) -> None:
    """Add ``sink`` to the process-wide list.

    The workflow's evaluate_policy activity passes an explicit
    AuditLogSink alongside whatever is registered — registered sinks
    are for cross-cutting concerns (a future Langfuse exporter, etc.).
    """
    _REGISTERED.append(sink)


def get_registered_sinks() -> list[Sink]:
    return list(_REGISTERED)


def clear_sinks() -> None:
    """For tests. Not part of production behavior."""
    _REGISTERED.clear()
