"""Default EvalRunStore impl: writes/reads eval_runs.

Holdout-counter atomicity uses SERIALIZABLE; the UNIQUE constraint
catches any race that slips."""

import uuid

import psycopg


class HoldoutCapExceeded(Exception):
    """Raised when allocating a holdout run would exceed the 3-per-sha cap."""


class PostgresEvalRunStore:
    def __init__(self, *, dsn: str) -> None:
        self._dsn = dsn

    async def allocate_run(
        self,
        *,
        git_sha: str,
        mode: str,
        holdout_justification: str | None,
        policy_enabled: bool,
        suite_names: list[str],
        host_git_dirty: bool,
    ) -> str:
        """Allocate an eval_runs row.

        Holdout-mode allocation reads MAX(commit_holdout_run_no) under
        SERIALIZABLE and assigns next_no; the UNIQUE(git_sha, commit_holdout_run_no)
        constraint backstops any race the snapshot lets through. A SerializationFailure
        from the conflicting transaction is retried once, then surfaced as
        HoldoutCapExceeded if it still races.
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return await self._allocate_once(
                    git_sha=git_sha,
                    mode=mode,
                    holdout_justification=holdout_justification,
                    policy_enabled=policy_enabled,
                    suite_names=suite_names,
                    host_git_dirty=host_git_dirty,
                )
            except psycopg.errors.SerializationFailure:
                if attempt == max_retries - 1:
                    raise HoldoutCapExceeded(
                        f"could not serialize holdout allocation for {git_sha}"
                    ) from None
                continue
            except psycopg.errors.UniqueViolation as e:
                raise HoldoutCapExceeded(
                    f"concurrent holdout allocation for {git_sha}"
                ) from e
        raise HoldoutCapExceeded(f"exhausted retries for {git_sha}")

    async def _allocate_once(
        self,
        *,
        git_sha: str,
        mode: str,
        holdout_justification: str | None,
        policy_enabled: bool,
        suite_names: list[str],
        host_git_dirty: bool,
    ) -> str:
        run_id = f"ev_{uuid.uuid4().hex[:12]}"
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            await conn.set_isolation_level(psycopg.IsolationLevel.SERIALIZABLE)
            async with conn.cursor() as cur:
                next_no: int | None
                if mode == "holdout":
                    await cur.execute(
                        """
                        SELECT COALESCE(MAX(commit_holdout_run_no), 0) + 1
                          FROM eval_runs
                         WHERE git_sha = %s
                        """,
                        (git_sha,),
                    )
                    row = await cur.fetchone()
                    assert row is not None
                    # COALESCE(..., 0) + 1 never returns NULL, but pyright sees
                    # the column as Optional[int].
                    next_no_raw = row[0]
                    assert next_no_raw is not None
                    next_no = int(next_no_raw)
                    if next_no > 3:
                        raise HoldoutCapExceeded(
                            f"git_sha {git_sha} has 3 holdout runs already"
                        )
                else:
                    next_no = None
                await cur.execute(
                    """
                    INSERT INTO eval_runs
                      (run_id, git_sha, mode, holdout_justification,
                       commit_holdout_run_no, policy_enabled, suite_names,
                       host_git_dirty)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id, git_sha, mode, holdout_justification,
                        next_no, policy_enabled, suite_names, host_git_dirty,
                    ),
                )
            await conn.commit()
        return run_id

    async def link_pair(self, run_id: str, paired_with: str) -> None:
        async with (
            await psycopg.AsyncConnection.connect(self._dsn) as conn,
            conn.cursor() as cur,
        ):
            await cur.execute(
                "UPDATE eval_runs SET paired_run_id = %s WHERE run_id = %s",
                (paired_with, run_id),
            )
            await cur.execute(
                "UPDATE eval_runs SET paired_run_id = %s WHERE run_id = %s",
                (run_id, paired_with),
            )
            await conn.commit()

    async def finalize(self, run_id: str) -> None:
        async with (
            await psycopg.AsyncConnection.connect(self._dsn) as conn,
            conn.cursor() as cur,
        ):
            await cur.execute(
                "UPDATE eval_runs SET finished_at = now() WHERE run_id = %s",
                (run_id,),
            )
            await conn.commit()
