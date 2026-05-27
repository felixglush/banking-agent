"""End-to-end tests for the Stage-5 policy gate.

These exercise the ``evaluate_policy`` activity against the real
``compass_test`` Postgres (same DB the workflow uses in production),
not the in-process WorkflowEnvironment. Rationale: the workflow's
``TestModel`` cannot call MCP tools so ``resolved_entities`` would
always be empty there; covering the policy gate at activity level
both produces real DB rows we can introspect and matches what the
manual workflow exercise does end-to-end.

The full Temporal workflow (with the live OpenAI model + bank MCP)
is verified via the manual smoke described in
``workflows/send_invoice/README.md``.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row
from temporalio.exceptions import ApplicationError

from compass.policy import Phase, hash_rules
from policies.send_invoice import RULES
from tests.policies.conftest import (
    happy_pre_action_proposal_ctx,
    happy_proposal,
)
from workflows.send_invoice.activities import (
    EvaluatePolicyInput,
    evaluate_policy,
)
from workflows.send_invoice.context import hash_proposal


def _dsn() -> str:
    return os.environ["COMPASS_PG_DSN"]


def _new_workflow_id() -> str:
    return f"test-stage5-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def _truncate_runtime_tables() -> None:
    """Wipe runtime tables before each test.

    Mirrors tests/workflows/send_invoice/conftest.py — required for
    isolated assertions on audit_log / policy_snapshots rows.
    """
    dsn = os.environ.get(
        "COMPASS_TEST_PG_DSN",
        "postgresql://compass:compass@localhost:5432/compass_test",
    )
    os.environ["COMPASS_PG_DSN"] = dsn
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.cursor() as cur:
        await cur.execute(
            "TRUNCATE TABLE invoice_line_items, invoices, audit_log "
            "RESTART IDENTITY"
        )
        await conn.commit()


async def _fetch_audit(workflow_run_id: str) -> list[dict[str, Any]]:
    async with (
        await psycopg.AsyncConnection.connect(_dsn()) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            "SELECT * FROM audit_log WHERE workflow_run_id=%s "
            "ORDER BY sequence_no",
            (workflow_run_id,),
        )
        return await cur.fetchall()


async def _fetch_snapshot_count(policy_hash: str) -> int:
    async with (
        await psycopg.AsyncConnection.connect(_dsn()) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute(
            "SELECT count(*) FROM policy_snapshots WHERE policy_hash=%s",
            (policy_hash,),
        )
        row = await cur.fetchone()
        return row[0]


# ---------------------------------------------------------------------
# pre_action_proposal phase
# ---------------------------------------------------------------------


async def test_happy_proposal_permits_and_writes_snapshot() -> None:
    """Happy path: every rule skips, snapshot row appears, policy_hash
    matches hash_rules(RULES)."""
    run_id = _new_workflow_id()
    out = await evaluate_policy(EvaluatePolicyInput(
        workflow_run_id=run_id,
        starting_sequence_no=1,
        phase=Phase.pre_action_proposal.value,
        context=happy_pre_action_proposal_ctx(),
    ))
    assert out.permit is True
    assert out.rule_ids_fired == []
    assert out.escalations == []
    assert out.policy_hash == hash_rules(RULES)
    assert out.next_sequence_no == 9  # 8 pre_action_proposal rule_skipped events + 1

    rows = await _fetch_audit(run_id)
    assert all(r["event_kind"] == "rule_skipped" for r in rows)
    assert {r["rule_id"] for r in rows} == {
        "customer_must_exist", "customer_kyc_verified", "invoice_amount_cap",
        "require_amount_source", "require_evidence_citation",
        "contract_consistency", "prohibit_exceed_contract_cap",
        "currency_consistency",
    }
    assert all(r["policy_hash"] == out.policy_hash for r in rows)
    assert await _fetch_snapshot_count(out.policy_hash) == 1


async def test_missing_customer_blocks_and_raises() -> None:
    """customer_must_exist fires when resolved_entities.customer is None."""
    run_id = _new_workflow_id()
    ctx = happy_pre_action_proposal_ctx()
    ctx["resolved_entities"]["customer"] = None

    with pytest.raises(ApplicationError) as exc:
        await evaluate_policy(EvaluatePolicyInput(
            workflow_run_id=run_id,
            starting_sequence_no=1,
            phase=Phase.pre_action_proposal.value,
            context=ctx,
        ))
    assert exc.value.type == "PolicyDecisionError"
    assert exc.value.non_retryable is True

    rows = await _fetch_audit(run_id)
    fired = [r for r in rows if r["event_kind"] == "rule_fired"]
    fired_ids = {r["rule_id"] for r in fired}
    assert "customer_must_exist" in fired_ids


async def test_invalid_source_type_blocks() -> None:
    """require_amount_source fires for invalid source_type."""
    run_id = _new_workflow_id()
    ctx = happy_pre_action_proposal_ctx()
    ctx["proposal"]["line_items"][0]["source_type"] = "made_up"

    with pytest.raises(ApplicationError):
        await evaluate_policy(EvaluatePolicyInput(
            workflow_run_id=run_id,
            starting_sequence_no=1,
            phase=Phase.pre_action_proposal.value,
            context=ctx,
        ))
    rows = await _fetch_audit(run_id)
    fired_ids = {r["rule_id"] for r in rows if r["event_kind"] == "rule_fired"}
    assert "require_amount_source" in fired_ids


async def test_amount_above_cap_escalates_but_permits() -> None:
    """invoice_amount_cap is ESCALATE, not BLOCK — workflow proceeds."""
    run_id = _new_workflow_id()
    ctx = happy_pre_action_proposal_ctx()
    ctx["proposal"]["total_cents"] = 15_000_000  # > $100k

    out = await evaluate_policy(EvaluatePolicyInput(
        workflow_run_id=run_id,
        starting_sequence_no=1,
        phase=Phase.pre_action_proposal.value,
        context=ctx,
    ))
    assert out.permit is True
    assert "invoice_amount_cap" in out.rule_ids_fired
    assert len(out.escalations) == 1

    rows = await _fetch_audit(run_id)
    fired = [r for r in rows if r["event_kind"] == "rule_fired"]
    assert any(
        r["rule_id"] == "invoice_amount_cap" and r["decision"] == "escalate"
        for r in fired
    )


# ---------------------------------------------------------------------
# pre_execute phase
# ---------------------------------------------------------------------


async def test_pre_execute_happy_path_permits() -> None:
    """pre_execute happy path: hashes match → both rules skip."""
    run_id = _new_workflow_id()
    # First populate snapshot via pre_action_proposal so policy_hash exists
    proposal_out = await evaluate_policy(EvaluatePolicyInput(
        workflow_run_id=run_id,
        starting_sequence_no=1,
        phase=Phase.pre_action_proposal.value,
        context=happy_pre_action_proposal_ctx(),
    ))
    # Now run pre_execute with matching hashes
    p = happy_proposal()
    proposal_h = hash_proposal(p)

    out = await evaluate_policy(EvaluatePolicyInput(
        workflow_run_id=run_id,
        starting_sequence_no=proposal_out.next_sequence_no,
        phase=Phase.pre_execute.value,
        context={
            "proposal": p,
            "proposal_hash_at_proposal": proposal_h,
            "policy_hash_at_proposal": proposal_out.policy_hash,
        },
    ))
    assert out.permit is True
    assert out.rule_ids_fired == []


async def test_pre_execute_silent_modification_blocks() -> None:
    """proposal hash drift between approval and execute → BLOCK."""
    run_id = _new_workflow_id()
    p = happy_proposal()

    with pytest.raises(ApplicationError) as exc:
        await evaluate_policy(EvaluatePolicyInput(
            workflow_run_id=run_id,
            starting_sequence_no=1,
            phase=Phase.pre_execute.value,
            context={
                "proposal": p,
                "proposal_hash_at_proposal": "stale-hash",
                "policy_hash_at_proposal": hash_rules(RULES),
            },
        ))
    assert exc.value.type == "PolicyDecisionError"
    rows = await _fetch_audit(run_id)
    fired_ids = {r["rule_id"] for r in rows if r["event_kind"] == "rule_fired"}
    assert "no_silent_modification_after_confirmation" in fired_ids


async def test_pre_execute_policy_drift_escalates() -> None:
    """policy hash drift between approval and execute → ESCALATE (still permits)."""
    run_id = _new_workflow_id()
    p = happy_proposal()

    out = await evaluate_policy(EvaluatePolicyInput(
        workflow_run_id=run_id,
        starting_sequence_no=1,
        phase=Phase.pre_execute.value,
        context={
            "proposal": p,
            "proposal_hash_at_proposal": hash_proposal(p),
            "policy_hash_at_proposal": "old-policy-hash",  # ≠ current
        },
    ))
    assert out.permit is True
    assert "no_policy_drift_after_confirmation" in out.rule_ids_fired
    assert len(out.escalations) == 1


# ---------------------------------------------------------------------
# snapshot idempotency
# ---------------------------------------------------------------------


async def test_policy_snapshot_idempotent_across_runs() -> None:
    """Two pre_action_proposal evaluations → exactly one snapshot row."""
    for _ in range(2):
        await evaluate_policy(EvaluatePolicyInput(
            workflow_run_id=_new_workflow_id(),
            starting_sequence_no=1,
            phase=Phase.pre_action_proposal.value,
            context=happy_pre_action_proposal_ctx(),
        ))
    assert await _fetch_snapshot_count(hash_rules(RULES)) == 1


# ---------------------------------------------------------------------
# ablation hatch
# ---------------------------------------------------------------------


async def test_ablation_hatch_bypasses_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMPASS_POLICY_DISABLE=1 short-circuits the activity to permit."""
    monkeypatch.setenv("COMPASS_POLICY_DISABLE", "1")
    run_id = _new_workflow_id()
    ctx = happy_pre_action_proposal_ctx()
    ctx["resolved_entities"]["customer"] = None  # would normally BLOCK

    out = await evaluate_policy(EvaluatePolicyInput(
        workflow_run_id=run_id,
        starting_sequence_no=1,
        phase=Phase.pre_action_proposal.value,
        context=ctx,
    ))
    assert out.permit is True
    assert out.policy_hash == "disabled-for-eval"

    rows = await _fetch_audit(run_id)
    assert rows == []  # no audit rows written when policy disabled
