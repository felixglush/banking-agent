"""Sink protocol + in-memory/null/multi sinks + register_sink."""

from __future__ import annotations

import pytest

from compass.policy.sink import (
    InMemorySink,
    MultiSink,
    NullSink,
    clear_sinks,
    get_registered_sinks,
    register_sink,
)


@pytest.fixture(autouse=True)
def _clear_global():  # pyright: ignore[reportUnusedFunction]
    clear_sinks()
    yield
    clear_sinks()


async def test_in_memory_sink_captures_events() -> None:
    sink = InMemorySink()
    await sink.emit({"event_kind": "rule_fired", "rule_id": "r1"})
    await sink.emit({"event_kind": "rule_skipped", "rule_id": "r2"})
    assert sink.events == [
        {"event_kind": "rule_fired", "rule_id": "r1"},
        {"event_kind": "rule_skipped", "rule_id": "r2"},
    ]


async def test_null_sink_is_silent() -> None:
    sink = NullSink()
    await sink.emit({"event_kind": "rule_fired", "rule_id": "r1"})


async def test_multi_sink_fans_out() -> None:
    a, b = InMemorySink(), InMemorySink()
    multi = MultiSink([a, b])
    await multi.emit({"event_kind": "rule_fired", "rule_id": "r1"})
    assert a.events == [{"event_kind": "rule_fired", "rule_id": "r1"}]
    assert b.events == [{"event_kind": "rule_fired", "rule_id": "r1"}]


def test_register_sink_adds_to_global_list() -> None:
    sink = InMemorySink()
    register_sink(sink)
    assert get_registered_sinks() == [sink]


def test_clear_sinks_empties_registry() -> None:
    register_sink(InMemorySink())
    clear_sinks()
    assert get_registered_sinks() == []
