"""Default RuleFireSource impl: reads rule_fired rows from the
Stage 4-5 audit_log table."""

import psycopg


class PostgresAuditLogSource:
    """Default RuleFireSource impl. One connection per query."""

    def __init__(self, *, dsn: str) -> None:
        self._dsn = dsn

    async def rule_ids_fired(self, workflow_run_id: str) -> set[str]:
        async with (
            await psycopg.AsyncConnection.connect(self._dsn) as conn,
            conn.cursor() as cur,
        ):
            await cur.execute(
                """
                SELECT rule_id
                  FROM audit_log
                 WHERE workflow_run_id = %s
                   AND event_kind = 'rule_fired'
                   AND rule_id IS NOT NULL
                """,
                (workflow_run_id,),
            )
            rows = await cur.fetchall()
        return {row[0] for row in rows}
