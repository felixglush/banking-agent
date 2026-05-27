"""Determinism is the load-bearing property of the generator.

Per build-plan §Data Simulation: 'Pure procedural generation, no LLM
in the loop … every field drawn from templates + seeded RNG. This is
what lets a single seed regenerate the world byte-identically.'

We exercise this by running the generator twice into temporary output
dirs (monkey-patched onto the module) and asserting every file
byte-matches.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from synthetic_account_1 import simulate as sim


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _all_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


@pytest.fixture
def tmp_output_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect simulate's output dirs onto a tmp tree.

    simulate.py reads PACKAGE_DIR-relative constants at import time, so
    we monkey-patch them per test.
    """
    gen = tmp_path / "generated"
    gt = tmp_path / "ground_truth"
    bank = gen / "bank"
    internal = gen / "account_internal"
    for d in (bank, internal, gt / "train", gt / "holdout"):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sim, "GENERATED_DIR", gen)
    monkeypatch.setattr(sim, "GROUND_TRUTH_DIR", gt)
    monkeypatch.setattr(sim, "BANK_DIR", bank)
    monkeypatch.setattr(sim, "INTERNAL_DIR", internal)
    yield tmp_path


def test_simulate_is_byte_identical_across_two_runs(tmp_output_dirs: Path) -> None:
    sim.simulate(seed=42)
    snapshot_a = tmp_output_dirs / "_run_a"
    snapshot_a.mkdir()
    for src in _all_files(tmp_output_dirs):
        if "_run_a" in src.parts or "_run_b" in src.parts:
            continue
        rel = src.relative_to(tmp_output_dirs)
        dst = snapshot_a / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    sim.simulate(seed=42)
    snapshot_b = tmp_output_dirs / "_run_b"
    snapshot_b.mkdir()
    for src in _all_files(tmp_output_dirs):
        if "_run_a" in src.parts or "_run_b" in src.parts:
            continue
        rel = src.relative_to(tmp_output_dirs)
        dst = snapshot_b / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    files_a = _all_files(snapshot_a)
    files_b = _all_files(snapshot_b)
    rels_a = sorted(p.relative_to(snapshot_a) for p in files_a)
    rels_b = sorted(p.relative_to(snapshot_b) for p in files_b)
    assert rels_a == rels_b, "file set differs across runs"
    for rel in rels_a:
        sa = _sha256(snapshot_a / rel)
        sb = _sha256(snapshot_b / rel)
        assert sa == sb, f"file {rel} differs across runs: {sa} != {sb}"


def test_simulate_different_seeds_produce_different_output(tmp_output_dirs: Path) -> None:
    sim.simulate(seed=42)
    customers_a = (tmp_output_dirs / "generated" / "bank" / "customers.jsonl").read_bytes()
    sim.simulate(seed=43)
    customers_b = (tmp_output_dirs / "generated" / "bank" / "customers.jsonl").read_bytes()
    assert customers_a != customers_b, "seed 42 and 43 produced identical customers.jsonl"
