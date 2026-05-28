"""Sink protocol + in-memory/null/multi sinks + register_sink."""

from __future__ import annotations

import pytest

from compass.policy import RuleFiredEvent, RuleSkippedEvent
from compass.policy.sink import (
    InMemorySink,
    MultiSink,
    NullSink,
    clear_sinks,
    get_registered_sinks,
    register_sink,
)


def _fired(rule_id: str) -> RuleFiredEvent:
    return {
        "event_kind": "rule_fired",
        "rule_id": rule_id,
        "phase": "pre_action_proposal",
        "decision": "block",
        "evidence": {},
        "message": "",
        "regulatory_basis": [],
    }


def _skipped(rule_id: str) -> RuleSkippedEvent:
    return {
        "event_kind": "rule_skipped",
        "rule_id": rule_id,
        "phase": "pre_action_proposal",
    }


@pytest.fixture(autouse=True)
def _clear_global():  # pyright: ignore[reportUnusedFunction]
    clear_sinks()
    yield
    clear_sinks()


async def test_in_memory_sink_captures_events() -> None:
    sink = InMemorySink()
    await sink.emit(_fired("r1"))
    await sink.emit(_skipped("r2"))
    assert sink.events == [_fired("r1"), _skipped("r2")]


async def test_null_sink_is_silent() -> None:
    sink = NullSink()
    await sink.emit(_fired("r1"))


async def test_multi_sink_fans_out() -> None:
    a, b = InMemorySink(), InMemorySink()
    multi = MultiSink([a, b])
    event = _fired("r1")
    await multi.emit(event)
    assert a.events == [event]
    assert b.events == [event]


def test_register_sink_adds_to_global_list() -> None:
    sink = InMemorySink()
    register_sink(sink)
    assert get_registered_sinks() == [sink]


def test_clear_sinks_empties_registry() -> None:
    register_sink(InMemorySink())
    clear_sinks()
    assert get_registered_sinks() == []
