"""require_evidence_citation — fires when source_refs are missing/empty."""

from __future__ import annotations

from compass.policy.primitives.evidence import require_evidence_citation


async def test_all_lines_cited_skips() -> None:
    pred = require_evidence_citation(field="proposal.line_items[*].source_refs")
    ctx = {"proposal": {"line_items": [
        {"source_refs": ["te_1"]},
        {"source_refs": ["te_2", "te_3"]},
    ]}}
    assert await pred(ctx) is None


async def test_one_line_empty_refs_fires() -> None:
    pred = require_evidence_citation(field="proposal.line_items[*].source_refs")
    ctx = {"proposal": {"line_items": [
        {"source_refs": ["te_1"]},
        {"source_refs": []},
    ]}}
    v = await pred(ctx)
    assert v is not None
    assert v.evidence["empty_line_indices"] == [1]


async def test_missing_path_fires_loudly() -> None:
    pred = require_evidence_citation(field="proposal.line_items[*].source_refs")
    v = await pred({"proposal": {}})
    assert v is not None
    assert "missing" in v.message.lower()
