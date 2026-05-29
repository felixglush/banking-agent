"""JSONL corpus loader. Reads invoice_resolution_labels.jsonl plus
the joined policy_compliance_labels.jsonl, materializes a list of
Case dataclasses.

Mode gate: train mode reads only from ground_truth/train/. The
``_force_split`` test hook exists to exercise the refusal path."""

import json
from pathlib import Path
from typing import Any, cast

from compass.eval.types import Case, Mode, Outcome


class HoldoutAccessError(Exception):
    """Raised if train mode is asked to read the holdout split."""


def load_corpus(
    *,
    workflow: str,
    mode: Mode,
    ground_truth_root: Path,
    _force_split: str | None = None,
) -> list[Case]:
    if workflow != "send_invoice":
        raise NotImplementedError(f"only send_invoice supported at v0.1, got {workflow}")
    split = _force_split or mode.value
    if mode == Mode.train and split != "train":
        raise HoldoutAccessError(
            "train mode cannot read holdout split — use --mode holdout"
        )

    ir_path = ground_truth_root / split / "invoice_resolution_labels.jsonl"
    pc_path = ground_truth_root / split / "policy_compliance_labels.jsonl"

    ir_rows: list[dict[str, Any]] = [
        json.loads(line) for line in ir_path.read_text().splitlines() if line.strip()
    ]
    pc_rows: list[dict[str, Any]] = [
        json.loads(line) for line in pc_path.read_text().splitlines() if line.strip()
    ]
    rules_by_case = {
        cast(str, r["invoice_case_id"]): cast(list[str], r["expected_fired_rules"])
        for r in pc_rows
    }

    cases: list[Case] = []
    for row in ir_rows:
        cases.append(Case(
            case_id=cast(str, row["case_id"]),
            request=cast(str, row["request"]),
            expected_outcome=cast(Outcome, row["expected_outcome"]),
            expected=cast(dict[str, Any], row.get("expected", {})),
            expected_fired_rules=rules_by_case.get(cast(str, row["case_id"]), []),
            expected_decline_reason=cast("str | None", row.get("expected_decline_reason")),
            clarify_answer=cast("str | None", row.get("clarify_answer")),
        ))
    return cases
