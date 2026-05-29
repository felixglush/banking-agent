"""Asserts the Stage 7 corpus has the expected_outcome distribution.

Tests run after simulate.py has been re-executed (see Task 2 step 4).
"""

import json
from pathlib import Path
from typing import Any, cast

GROUND_TRUTH = Path(__file__).resolve().parents[2] / "synthetic_account_1" / "ground_truth"


def _load(path: Path) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def test_train_has_expected_outcome_field() -> None:
    cases = _load(GROUND_TRUTH / "train" / "invoice_resolution_labels.jsonl")
    assert all("expected_outcome" in c for c in cases)


def test_train_outcome_counts() -> None:
    """Pinned counts from the deterministic ``random.Random(0xC0FFEE)`` shuffle.

    Cases: uniquely-answerable ``sent`` (request names the billable basis) +
    ``needs_clarification`` (generic request, multiple invoices of that
    source_type) + cloned ``declined`` + ``policy_rejected``. Flipping any case
    type fails this check (and ``verify.py``).
    """
    cases = _load(GROUND_TRUTH / "train" / "invoice_resolution_labels.jsonl")
    by_outcome: dict[str, int] = {}
    for c in cases:
        by_outcome[c["expected_outcome"]] = by_outcome.get(c["expected_outcome"], 0) + 1
    assert by_outcome["sent"] == 96  # includes 11 clarification round-trip cases
    assert by_outcome["declined"] == 10
    assert by_outcome["policy_rejected"] == 13
    assert sum(by_outcome.values()) == 119
    assert sum(1 for c in cases if c.get("clarify_answer")) == 11


def test_holdout_outcome_counts() -> None:
    """See ``test_train_outcome_counts`` for the shuffle derivation."""
    cases = _load(GROUND_TRUTH / "holdout" / "invoice_resolution_labels.jsonl")
    by_outcome: dict[str, int] = {}
    for c in cases:
        by_outcome[c["expected_outcome"]] = by_outcome.get(c["expected_outcome"], 0) + 1
    assert by_outcome["sent"] == 40  # includes 5 clarification round-trip cases
    assert by_outcome["declined"] == 8
    assert by_outcome["policy_rejected"] == 3
    assert sum(by_outcome.values()) == 51
    assert sum(1 for c in cases if c.get("clarify_answer")) == 5


def test_declined_cases_have_decline_reason() -> None:
    for split in ("train", "holdout"):
        cases = _load(GROUND_TRUTH / split / "invoice_resolution_labels.jsonl")
        for c in cases:
            if c["expected_outcome"] == "declined":
                assert c["expected_decline_reason"] in {
                    "amount_too_high_for_approver",
                    "customer_on_hold",
                    "requested_clarification",
                }, f"case {c['case_id']} has bad reason {c.get('expected_decline_reason')}"


def test_policy_rejected_cases_have_compliance_label() -> None:
    """policy_rejected cases must each have a matching policy_compliance row
    naming the rule(s) that should fire."""
    pc_train = _load(GROUND_TRUTH / "train" / "policy_compliance_labels.jsonl")
    pc_holdout = _load(GROUND_TRUTH / "holdout" / "policy_compliance_labels.jsonl")
    pc_by_case = {c["invoice_case_id"]: c for c in pc_train + pc_holdout}
    for split in ("train", "holdout"):
        cases = _load(GROUND_TRUTH / split / "invoice_resolution_labels.jsonl")
        for c in cases:
            if c["expected_outcome"] == "policy_rejected":
                pc = pc_by_case.get(c["case_id"])
                assert pc is not None, f"missing policy_compliance for {c['case_id']}"
                assert len(pc["expected_fired_rules"]) >= 1


def test_sent_cases_keep_existing_schema() -> None:
    """Backward-compat: every sent case still has the `expected` block with
    customer_id, contract_id, currency, source_type, total_cents."""
    for split in ("train", "holdout"):
        cases = _load(GROUND_TRUTH / split / "invoice_resolution_labels.jsonl")
        for c in cases:
            if c["expected_outcome"] != "sent":
                continue
            exp = c["expected"]
            assert {
                "customer_id",
                "contract_id",
                "currency",
                "source_type",
                "total_cents",
            }.issubset(exp.keys())
