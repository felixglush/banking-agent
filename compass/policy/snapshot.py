"""Write a policy_snapshots row inside the evaluate_policy transaction.

Called once per evaluate_policy activity invocation; ``ON CONFLICT DO
NOTHING`` makes the second-and-later calls per worker × hash a no-op.
The serialized ``rules_json`` is byte-identical to what ``hash_rules``
hashed, so a 5-year-old audit row's policy_hash always resolves to a
reconstructable rule set.

Tested via tests/workflows/send_invoice/test_workflow_policy.py
(`test_policy_snapshot_written_once`).
"""

from __future__ import annotations

from collections.abc import Sequence

import psycopg
from psycopg.types.json import Jsonb

from compass.policy.hashing import hash_rules, serialize_rules
from compass.policy.types import Rule


async def write_policy_snapshot(
    conn: psycopg.AsyncConnection,
    workflow: str,
    rules: Sequence[Rule],
) -> str:
    """Idempotently INSERT a policy_snapshots row; return the policy_hash.

    Must be called inside an open transaction on ``conn`` — the caller
    (evaluate_policy activity) commits after the audit writes also
    succeed.
    """
    policy_hash = hash_rules(rules)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO policy_snapshots (policy_hash, workflow, rules_json)
            VALUES (%s, %s, %s)
            ON CONFLICT (policy_hash) DO NOTHING
            """,
            (policy_hash, workflow, Jsonb(serialize_rules(rules))),
        )
    return policy_hash
