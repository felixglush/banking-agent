"""Unit test using a mocked Langfuse client."""

from unittest.mock import MagicMock

import pytest

from compass.eval.sources.langfuse_scores import LangfuseDatasetScoreSink

pytestmark = pytest.mark.asyncio


async def test_write_score_calls_create_score():
    mock_client = MagicMock()
    mock_client.create_score = MagicMock(return_value=None)
    sink = LangfuseDatasetScoreSink(client=mock_client, dataset_name="send_invoice_v0_1")
    await sink.write_score(
        run_id="ev_abc", item_id="ir_0001",
        name="functional", value=1.0, comment=None,
    )
    mock_client.create_score.assert_called_once()
    kwargs = mock_client.create_score.call_args.kwargs
    assert kwargs["name"] == "functional"
    assert kwargs["value"] == 1.0
    assert kwargs["dataset_run_name"] == "ev_abc"
