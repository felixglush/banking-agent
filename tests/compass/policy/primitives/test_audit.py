"""audit_validation primitives: log_policy_version, log_data_sources_consulted."""

from __future__ import annotations

from compass.policy.primitives.audit import (
    log_data_sources_consulted,
    log_policy_version,
)

# ---- log_policy_version ----


async def test_policy_version_present_skips() -> None:
    pred = log_policy_version()
    assert await pred({"policy_hash": "abc123"}) is None


async def test_policy_version_missing_fires() -> None:
    pred = log_policy_version()
    v = await pred({})
    assert v is not None
    assert "policy_hash" in v.message


async def test_policy_version_empty_fires() -> None:
    pred = log_policy_version()
    v = await pred({"policy_hash": ""})
    assert v is not None


# ---- log_data_sources_consulted ----


async def test_tool_calls_present_skips() -> None:
    pred = log_data_sources_consulted()
    ctx = {"tool_calls": [{"tool_name": "list_customers"}]}
    assert await pred(ctx) is None


async def test_tool_calls_empty_fires() -> None:
    pred = log_data_sources_consulted()
    v = await pred({"tool_calls": []})
    assert v is not None


async def test_tool_calls_missing_fires() -> None:
    pred = log_data_sources_consulted()
    v = await pred({})
    assert v is not None
