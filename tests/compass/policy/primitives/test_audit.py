"""audit_validation primitives: log_policy_version, log_data_sources_consulted."""

from __future__ import annotations

from typing import Any

import pytest

from compass.policy.primitives.audit import (
    log_data_sources_consulted,
    log_policy_version,
)

# ---- log_policy_version ----


async def test_policy_version_present_skips() -> None:
    assert await log_policy_version()({"policy_hash": "abc123"}) is None


@pytest.mark.parametrize(
    "ctx",
    [
        pytest.param({}, id="missing"),
        pytest.param({"policy_hash": ""}, id="empty"),
    ],
)
async def test_policy_version_absent_fires(ctx: dict[str, Any]) -> None:
    v = await log_policy_version()(ctx)
    assert v is not None
    assert "policy_hash" in v.message


# ---- log_data_sources_consulted ----


async def test_tool_calls_present_skips() -> None:
    assert await log_data_sources_consulted()(
        {"tool_calls": [{"tool_name": "list_customers"}]},
    ) is None


@pytest.mark.parametrize(
    "ctx",
    [
        pytest.param({"tool_calls": []}, id="empty"),
        pytest.param({}, id="missing"),
    ],
)
async def test_tool_calls_absent_fires(ctx: dict[str, Any]) -> None:
    v = await log_data_sources_consulted()(ctx)
    assert v is not None
