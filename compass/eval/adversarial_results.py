"""Parse Promptfoo's EvaluateSummaryV3 results JSON. Pure; defensive about
optional keys (the schema carries many fields we don't use).

Stage 3 of the adversarial pipeline grades with an echo provider, so the only
signal to read back is per-case pass/fail keyed on the stable case_id — the
policy-fire signal is resolved in stage 2 (``adversarial_run.run_probes``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

_Dict = Mapping[str, Any]


def _iter_results(data: _Dict) -> list[_Dict]:
    """`promptfoo eval -o file.json` writes the EvaluateSummaryV3 envelope:
    {evalId, results: {results: [...], ...}}. The per-test list is nested one
    level under "results"; a raw list (older shapes) is handled too."""
    raw: Any = data.get("results")
    if isinstance(raw, Mapping):
        raw = cast(_Dict, raw).get("results")
    return cast("list[_Dict]", raw or [])


def parse_grade_results(data: _Dict) -> dict[str, bool]:
    """Stage-3 echo-grade results → {case_id: repelled}. The case_id is the
    stable join key stamped into each grade test's metadata; ``success`` is the
    generator's grader verdict (True = the attack was repelled)."""
    out: dict[str, bool] = {}
    for r in _iter_results(data):
        test_case = cast(_Dict, r.get("testCase") or {})
        test_md = cast(_Dict, test_case.get("metadata") or {})
        case_id = test_md.get("case_id")
        if case_id is not None:
            out[str(case_id)] = bool(r.get("success", False))
    return out
