"""Schema assertions for the Stage 7 eval_runs additions.

These are migration-shape tests, not behavior tests. They confirm the
columns, defaults, and constraints land as the spec requires.
"""

from typing import cast

import psycopg
import pytest

pytestmark = pytest.mark.asyncio


async def test_eval_runs_has_paired_run_id(db_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn, conn.cursor() as cur:
        await cur.execute(
            """
            SELECT column_name, data_type, is_nullable
              FROM information_schema.columns
             WHERE table_name = 'eval_runs'
               AND column_name IN
                   ('paired_run_id', 'policy_enabled', 'suite_names', 'host_git_dirty')
             ORDER BY column_name
            """,
        )
        rows = await cur.fetchall()
    by_name = {cast(str, r[0]): r for r in rows}
    assert by_name["paired_run_id"][1] == "text"
    assert by_name["paired_run_id"][2] == "YES"
    assert by_name["policy_enabled"][1] == "boolean"
    assert by_name["policy_enabled"][2] == "NO"
    assert by_name["suite_names"][1] == "ARRAY"
    assert by_name["host_git_dirty"][1] == "boolean"


async def test_holdout_counter_unique_constraint_blocks_fourth_run(db_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM eval_runs WHERE git_sha = 'TEST_SHA_1'")
            for n in (1, 2, 3):
                await cur.execute(
                    """
                    INSERT INTO eval_runs (run_id, git_sha, mode, holdout_justification,
                                           commit_holdout_run_no)
                    VALUES (%s, 'TEST_SHA_1', 'holdout', 'test', %s)
                    """,
                    (f"ev_test_{n}", n),
                )
            await conn.commit()
        # Re-inserting an already-used (git_sha, commit_holdout_run_no) pair
        # must hit the UNIQUE constraint — this is the atomic gate that makes
        # the 3-runs-per-commit counter race-free.
        with pytest.raises(psycopg.errors.UniqueViolation):
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO eval_runs (run_id, git_sha, mode, holdout_justification,
                                           commit_holdout_run_no)
                    VALUES ('ev_test_4', 'TEST_SHA_1', 'holdout', 'test', 3)
                    """,
                )


async def test_empty_holdout_justification_rejected(db_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO eval_runs (run_id, git_sha, mode, holdout_justification)
                    VALUES ('ev_test_empty', 'TEST_SHA_2', 'holdout', '   ')
                    """,
                )


async def test_suite_names_check_rejects_unknown_suite(db_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(db_dsn) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO eval_runs (run_id, git_sha, mode, suite_names)
                    VALUES ('ev_test_bad_suite', 'TEST_SHA_3', 'train',
                            ARRAY['functional','not_a_real_suite']::text[])
                    """,
                )
