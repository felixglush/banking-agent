"""End-to-end: seed audit_log rows, query through the protocol impl,
assert the returned set matches."""

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from compass.eval.sources.audit_log import PostgresAuditLogSource

pytestmark = pytest.mark.asyncio


async def _seed(dsn: str, workflow_run_id: str, rule_ids: list[str]) -> None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO policy_snapshots (policy_hash, workflow, rules_json)
                VALUES ('test_hash_audit_log', 'send_invoice', %s)
                ON CONFLICT (policy_hash) DO NOTHING
                """,
                (Jsonb({"rules": []}),),
            )
            for seq, rule_id in enumerate(rule_ids, start=1):
                await cur.execute(
                    """
                    INSERT INTO audit_log
                      (workflow_run_id, sequence_no, phase, event_kind, rule_id,
                       policy_hash, decision, payload)
                    VALUES (%s, %s, 'pre_action_proposal', 'rule_fired', %s,
                            'test_hash_audit_log', 'permit', %s)
                    """,
                    (workflow_run_id, seq, rule_id, Jsonb({})),
                )
        await conn.commit()


async def test_returns_fired_rule_ids(db_dsn: str) -> None:
    wfid = f"test-wf-{uuid.uuid4().hex[:8]}"
    await _seed(db_dsn, wfid, ["require_amount_source", "currency_consistency_check"])
    src = PostgresAuditLogSource(dsn=db_dsn)
    fired = await src.rule_ids_fired(wfid)
    assert fired == {"require_amount_source", "currency_consistency_check"}


async def test_returns_empty_set_for_unknown_workflow(db_dsn: str) -> None:
    src = PostgresAuditLogSource(dsn=db_dsn)
    fired = await src.rule_ids_fired(f"never-existed-{uuid.uuid4().hex}")
    assert fired == set()


async def test_excludes_non_rule_fired_events(db_dsn: str) -> None:
    wfid = f"test-wf-{uuid.uuid4().hex[:8]}"
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO policy_snapshots (policy_hash, workflow, rules_json)
                VALUES ('test_hash_audit_log', 'send_invoice', %s)
                ON CONFLICT (policy_hash) DO NOTHING
                """,
                (Jsonb({"rules": []}),),
            )
            await cur.execute(
                """
                INSERT INTO audit_log (workflow_run_id, sequence_no, phase, event_kind,
                                       rule_id, policy_hash, payload)
                VALUES (%s, 1, 'pre_action_proposal', 'rule_fired', 'A',
                        'test_hash_audit_log', %s),
                       (%s, 2, 'pre_action_proposal', 'rule_skipped', 'B',
                        'test_hash_audit_log', %s)
                """,
                (wfid, Jsonb({}), wfid, Jsonb({})),
            )
        await conn.commit()

    src = PostgresAuditLogSource(dsn=db_dsn)
    fired = await src.rule_ids_fired(wfid)
    assert fired == {"A"}
