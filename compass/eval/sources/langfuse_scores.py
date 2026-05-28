"""Default ScoreSink impl: writes per-case suite scores as Langfuse
Dataset Run scores."""

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
            dataset_run_name=run_id,
            data_set_item_id=item_id,
        )
