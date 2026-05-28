"""Default ScoreSink impl: writes per-case suite scores to Langfuse.

The Langfuse v4 SDK requires every score to anchor to one of
trace_id / observation_id / session_id / dataset_run_id. v0.1 uses
``session_id = compass-eval run_id`` so all scores from one harness
invocation group together under a Langfuse Session, with the case_id
and dataset name carried in score metadata for cross-reference."""

from typing import Any


class LangfuseDatasetScoreSink:
    def __init__(self, *, client: Any, dataset_name: str) -> None:
        self._client = client
        self._dataset_name = dataset_name

    async def write_score(
        self,
        *,
        run_id: str,
        item_id: str,
        name: str,
        value: float,
        comment: str | None,
    ) -> None:
        self._client.create_score(
            name=name,
            value=value,
            comment=comment,
            session_id=run_id,
            metadata={
                "compass_eval_run_id": run_id,
                "case_id": item_id,
                "dataset": self._dataset_name,
            },
        )
