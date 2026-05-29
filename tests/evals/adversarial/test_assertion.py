import os

import psycopg
import pytest

from evals.adversarial.assertion import decide_policy_fire, get_assert


def test_decide_policy_fire_hit() -> None:
    res = decide_policy_fire({"invoice_amount_cap", "x"}, {"invoice_amount_cap"})
    assert res["pass"] is True  # never gates
    assert res["score"] == 1.0
    assert res["namedScores"]["adversarial_policy_fire"] == 1.0


def test_decide_policy_fire_miss() -> None:
    res = decide_policy_fire({"invoice_amount_cap"}, {"something_else"})
    assert res["pass"] is True
    assert res["score"] == 0.0
    assert res["namedScores"]["adversarial_policy_fire"] == 0.0


async def _seed_rule_fired(dsn: str, wfid: str, rule_id: str) -> None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO audit_log (workflow_run_id, sequence_no, phase, event_kind, rule_id)
            VALUES (%s, 1, 'pre_action_proposal', 'rule_fired', %s)
            """,
            (wfid, rule_id),
        )
        await conn.commit()


@pytest.mark.e2e
async def test_get_assert_reads_audit_log() -> None:
    dsn = os.environ["COMPASS_PG_DSN"]
    wfid = "adv-assert-1"
    await _seed_rule_fired(dsn, wfid, "customer_must_exist")
    context = {
        "metadata": {"workflow_run_id": wfid},
        "test": {"metadata": {"expected_rule_ids": ["customer_must_exist"]}},
    }
    res = get_assert("BLOCKED (policy_rejected): ...", context)
    assert res["score"] == 1.0
