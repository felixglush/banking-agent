"""Shared fixtures for policies tests."""

from __future__ import annotations

from typing import Any

import pytest


def happy_proposal() -> dict[str, Any]:
    return {
        "customer_id": "cust_alpha",
        "currency": "USD",
        "total_cents": 80000,
        "payment_terms_days": 30,
        "source_type": "time_tracking",
        "contract_id": "ct_alpha_current",
        "line_items": [
            {
                "description": "Solutions Architect time",
                "quantity_micros": 2_000_000,
                "unit_amount_cents": 40000,
                "line_total_cents": 80000,
                "source_type": "time_tracking",
                "source_refs": ["te_001"],
                "computation": "2h * $400/hr per contract ct_alpha_current",
            }
        ],
        "notes": None,
    }


def happy_resolved_entities() -> dict[str, Any]:
    return {
        "customer": {"id": "cust_alpha", "name": "Acme",
                     "kyc_status": "verified"},
        "contract": {"id": "ct_alpha_current", "currency": "USD",
                     "monthly_hour_cap": 40},
        "rate_card_entries": [],
        "time_entries": [],
    }


def happy_tool_calls() -> list[dict[str, Any]]:
    return [{"tool_name": "list_customers", "args": {}, "result": []}]


def happy_pre_action_proposal_ctx() -> dict[str, Any]:
    return {
        "proposal": happy_proposal(),
        "resolved_entities": happy_resolved_entities(),
        "tool_calls": happy_tool_calls(),
        "reasoning_text": "OK",
        "workflow_run_id": "wf-test",
    }


@pytest.fixture
def base_ctx() -> dict[str, Any]:
    return happy_pre_action_proposal_ctx()
