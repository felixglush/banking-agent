"""Corpus loader behavior."""

from pathlib import Path

import pytest

from compass.eval.corpus import HoldoutAccessError, load_corpus
from compass.eval.types import Mode

REPO_ROOT = Path(__file__).resolve().parents[3]
GROUND_TRUTH = REPO_ROOT / "synthetic_account_1" / "ground_truth"


def test_load_train_corpus():
    cases = load_corpus(workflow="send_invoice", mode=Mode.train,
                        ground_truth_root=GROUND_TRUTH)
    assert len(cases) == 108
    assert {c.expected_outcome for c in cases} == {"sent", "declined", "policy_rejected"}


def test_load_holdout_corpus():
    cases = load_corpus(workflow="send_invoice", mode=Mode.holdout,
                        ground_truth_root=GROUND_TRUTH)
    assert len(cases) == 46


def test_holdout_chroot_train_mode_refuses_holdout_path(tmp_path: Path):
    """If a caller tries to pass the holdout directory as the train root,
    the loader refuses."""
    fake_holdout = tmp_path / "ground_truth" / "holdout"
    fake_holdout.mkdir(parents=True)
    (fake_holdout / "invoice_resolution_labels.jsonl").write_text("")
    with pytest.raises(HoldoutAccessError):
        load_corpus(workflow="send_invoice", mode=Mode.train,
                    ground_truth_root=tmp_path / "ground_truth",
                    _force_split="holdout")
