"""Behavior tests for PostgresEvalRunStore."""

import asyncio
import uuid

import psycopg
import pytest

from compass.eval.sources.eval_runs import HoldoutCapExceeded, PostgresEvalRunStore

pytestmark = pytest.mark.asyncio


async def test_allocate_train_run(db_dsn: str) -> None:
    store = PostgresEvalRunStore(dsn=db_dsn)
    run_id = await store.allocate_run(
        git_sha=f"sha_{uuid.uuid4().hex}",
        mode="train",
        holdout_justification=None,
        policy_enabled=True,
        suite_names=["functional"],
        host_git_dirty=False,
    )
    assert run_id.startswith("ev_")


async def test_holdout_cap_at_3(db_dsn: str) -> None:
    sha = f"sha_holdout_{uuid.uuid4().hex}"
    store = PostgresEvalRunStore(dsn=db_dsn)
    for _ in range(3):
        await store.allocate_run(
            git_sha=sha,
            mode="holdout",
            holdout_justification="release smoke",
            policy_enabled=True,
            suite_names=["functional"],
            host_git_dirty=False,
        )
    with pytest.raises(HoldoutCapExceeded):
        await store.allocate_run(
            git_sha=sha,
            mode="holdout",
            holdout_justification="release smoke",
            policy_enabled=True,
            suite_names=["functional"],
            host_git_dirty=False,
        )


async def test_concurrent_inserts_hit_unique_constraint(db_dsn: str) -> None:
    """4 parallel allocate_run calls for the same git_sha; exactly 3 succeed."""
    sha = f"sha_race_{uuid.uuid4().hex}"
    store = PostgresEvalRunStore(dsn=db_dsn)
    coros = [
        store.allocate_run(
            git_sha=sha,
            mode="holdout",
            holdout_justification="race",
            policy_enabled=True,
            suite_names=["functional"],
            host_git_dirty=False,
        )
        for _ in range(4)
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)
    successes = [r for r in results if isinstance(r, str)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 3
    assert len(failures) == 1
    assert isinstance(failures[0], HoldoutCapExceeded)


async def test_link_pair_round_trip(db_dsn: str) -> None:
    store = PostgresEvalRunStore(dsn=db_dsn)
    sha = f"sha_pair_{uuid.uuid4().hex}"
    a = await store.allocate_run(
        git_sha=sha,
        mode="train",
        holdout_justification=None,
        policy_enabled=True,
        suite_names=["functional"],
        host_git_dirty=False,
    )
    b = await store.allocate_run(
        git_sha=sha,
        mode="train",
        holdout_justification=None,
        policy_enabled=False,
        suite_names=["functional"],
        host_git_dirty=False,
    )
    await store.link_pair(a, b)

    async with (
        await psycopg.AsyncConnection.connect(db_dsn) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute(
            "SELECT run_id, paired_run_id FROM eval_runs WHERE run_id IN (%s, %s)",
            (a, b),
        )
        rows = {r[0]: r[1] for r in await cur.fetchall()}
    assert rows[a] == b
    assert rows[b] == a


async def test_finalize_sets_finished_at(db_dsn: str) -> None:
    store = PostgresEvalRunStore(dsn=db_dsn)
    run_id = await store.allocate_run(
        git_sha=f"sha_{uuid.uuid4().hex}",
        mode="train",
        holdout_justification=None,
        policy_enabled=True,
        suite_names=["functional"],
        host_git_dirty=False,
    )
    await store.finalize(run_id)
    async with (
        await psycopg.AsyncConnection.connect(db_dsn) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute("SELECT finished_at FROM eval_runs WHERE run_id = %s", (run_id,))
        row = await cur.fetchone()
    assert row is not None and row[0] is not None
