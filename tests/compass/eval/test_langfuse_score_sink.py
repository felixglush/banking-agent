"""Unit tests for LangfuseDatasetScoreSink with a mocked Langfuse client."""

from unittest.mock import MagicMock

import pytest

from compass.eval.sources.langfuse_scores import LangfuseDatasetScoreSink
from compass.eval.types import Case

pytestmark = pytest.mark.asyncio


def _client() -> MagicMock:
    client = MagicMock()
    client.create_dataset = MagicMock(return_value=None)
    client.create_dataset_item = MagicMock(return_value=None)
    client.create_score = MagicMock(return_value=None)
    client.api.dataset_run_items.create = MagicMock(
        return_value=MagicMock(dataset_run_id="lf_run_123")
    )
    return client


def _case(case_id: str = "ir_0001") -> Case:
    return Case(
        case_id=case_id,
        request="Send invoice for Acme Corp",
        expected_outcome="sent",
        expected={"customer_id": "c1", "total_cents": 100},
        expected_fired_rules=["require_amount_source"],
        expected_decline_reason=None,
    )


async def test_ensure_dataset_uploads_dataset_and_items() -> None:
    client = _client()
    sink = LangfuseDatasetScoreSink(client=client, dataset_name="send_invoice_v0_1")
    await sink.ensure_dataset([_case("ir_0001"), _case("ir_0002")])

    client.create_dataset.assert_called_once_with(name="send_invoice_v0_1")
    assert client.create_dataset_item.call_count == 2
    kwargs = client.create_dataset_item.call_args_list[0].kwargs
    assert kwargs["dataset_name"] == "send_invoice_v0_1"
    assert kwargs["id"] == "ir_0001"
    assert kwargs["input"] == "Send invoice for Acme Corp"
    assert kwargs["expected_output"] == {"customer_id": "c1", "total_cents": 100}
    assert kwargs["metadata"]["expected_outcome"] == "sent"


async def test_write_score_links_run_item_then_scores_on_trace() -> None:
    client = _client()
    sink = LangfuseDatasetScoreSink(client=client, dataset_name="send_invoice_v0_1")
    await sink.write_score(
        run_id="ev_abc",
        item_id="ir_0001",
        name="functional",
        value=1.0,
        comment=None,
        trace_id="t_deadbeef",
    )

    client.api.dataset_run_items.create.assert_called_once_with(
        run_name="ev_abc",
        dataset_item_id="ir_0001",
        trace_id="t_deadbeef",
    )
    sc = client.create_score.call_args.kwargs
    assert sc["name"] == "functional"
    assert sc["value"] == 1.0
    # Exactly one anchor: the trace (the run item links it into the run).
    assert sc["trace_id"] == "t_deadbeef"
    assert "session_id" not in sc
    assert "dataset_run_id" not in sc
    assert sc["metadata"]["case_id"] == "ir_0001"


async def test_run_item_created_once_per_case() -> None:
    client = _client()
    sink = LangfuseDatasetScoreSink(client=client, dataset_name="send_invoice_v0_1")
    for suite in ("functional", "policy_compliance", "cost_latency"):
        await sink.write_score(
            run_id="ev_abc",
            item_id="ir_0001",
            name=suite,
            value=1.0,
            comment=None,
            trace_id="t_1",
        )
    assert client.api.dataset_run_items.create.call_count == 1
    assert client.create_score.call_count == 3


async def test_write_run_score_anchors_to_dataset_run() -> None:
    client = _client()
    sink = LangfuseDatasetScoreSink(client=client, dataset_name="send_invoice_v0_1")
    # First link a case so the dataset_run_id is captured.
    await sink.write_score(
        run_id="ev_abc",
        item_id="ir_0001",
        name="functional",
        value=1.0,
        comment=None,
        trace_id="t_1",
    )
    client.create_score.reset_mock()
    await sink.write_run_score(
        run_id="ev_abc",
        name="functional",
        value=0.667,
        comment="2/3 passed",
    )
    sc = client.create_score.call_args.kwargs
    assert sc["dataset_run_id"] == "lf_run_123"
    assert sc["value"] == 0.667
    assert sc["data_type"] == "NUMERIC"
    assert "trace_id" not in sc and "session_id" not in sc


async def test_write_run_score_no_run_id_is_noop() -> None:
    client = _client()
    sink = LangfuseDatasetScoreSink(client=client, dataset_name="send_invoice_v0_1")
    # No case linked → no dataset_run_id known → no-op (no score written).
    await sink.write_run_score(run_id="ev_x", name="functional", value=1.0, comment=None)
    client.create_score.assert_not_called()


async def test_no_trace_id_skips_run_item_but_still_scores() -> None:
    client = _client()
    sink = LangfuseDatasetScoreSink(client=client, dataset_name="send_invoice_v0_1")
    await sink.write_score(
        run_id="ev_abc",
        item_id="ir_0001",
        name="functional",
        value=0.0,
        comment="workflow_error:RuntimeError",
        trace_id=None,
    )
    client.api.dataset_run_items.create.assert_not_called()
    sc = client.create_score.call_args.kwargs
    # No trace → anchored to the run as a Session (the only anchor).
    assert sc["session_id"] == "ev_abc"
    assert "trace_id" not in sc
    assert "dataset_run_id" not in sc
