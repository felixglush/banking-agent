"""AuditLogSink — Sink implementation that writes audit_log rows.

Each emit becomes one INSERT inside the activity's open transaction.
Idempotent via UNIQUE (workflow_run_id, sequence_no) + ON CONFLICT DO
NOTHING from db/schema.sql; activity retries that re-emit the same
events collide harmlessly with the previous attempt's writes.

SequenceAllocator wraps a monotonic counter the activity uses to assign
sequence_no values. The workflow allocates the starting value and the
activity returns peek() so the workflow's own counter stays in sync.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


class SequenceAllocator:
    """Monotonic counter the sink draws sequence_no values from.

    Starts at the value the workflow passes in. Each call to ``next()``
    increments and returns. ``peek()`` returns the next-free value
    without advancing — the activity returns peek() to the workflow so
    the workflow's _next_seq can resume from there.
    """

    def __init__(self, starting_sequence_no: int) -> None:
        if starting_sequence_no < 1:
            raise ValueError("starting_sequence_no must be >= 1")
        self._next: int = starting_sequence_no

    def __iter__(self) -> Iterator[int]:
        return self

    def __next__(self) -> int:
        value = self._next
        self._next += 1
        return value

    def peek(self) -> int:
        return self._next


class AuditLogSink:
    """Writes each event to audit_log as one row.

    Caller must keep ``conn`` open across all emits (one transaction).
    The sink does not commit — that's the activity's job once all writes
    (snapshot + rule events) succeed.
    """

    def __init__(
        self,
        conn: psycopg.AsyncConnection,
        workflow_run_id: str,
        allocator: SequenceAllocator,
        policy_hash: str,
    ) -> None:
        self._conn = conn
        self._workflow_run_id = workflow_run_id
        self._allocator = allocator
        self._policy_hash = policy_hash

    async def emit(self, event: dict[str, Any]) -> None:
        sequence_no = next(self._allocator)
        # decision column: 'block' / 'escalate' for fired; NULL for skipped.
        decision = event.get("decision")
        phase = event["phase"]
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO audit_log (
                    workflow_run_id, sequence_no, phase, event_kind,
                    rule_id, policy_hash, decision, actor, payload
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workflow_run_id, sequence_no) DO NOTHING
                """,
                (
                    self._workflow_run_id,
                    sequence_no,
                    phase,
                    event["event_kind"],
                    event.get("rule_id"),
                    self._policy_hash,
                    decision,
                    None,  # actor: NULL for rule_* events
                    Jsonb(_payload_from_event(event)),
                ),
            )


def _payload_from_event(event: dict[str, Any]) -> dict[str, Any]:
    """Build the JSONB payload that lands in audit_log.payload."""
    payload: dict[str, Any] = {}
    # regulatory_basis is denormalized into payload for 7-year audit
    # interpretability without joining policy_snapshots.
    if "regulatory_basis" in event:
        payload["regulatory_basis"] = event["regulatory_basis"]
    if "message" in event:
        payload["message"] = event["message"]
    if "evidence" in event:
        payload["evidence"] = event["evidence"]
    return payload
