"""CLI argparser + mode gates."""

import pytest

from compass.eval.cli import build_parser, validate_args


def _parse(*args: str):
    parser = build_parser()
    return parser.parse_args(list(args))


def test_holdout_requires_justification():
    ns = _parse("--workflow", "send_invoice", "--mode", "holdout", "--suites", "functional")
    with pytest.raises(SystemExit) as exc:
        validate_args(ns)
    assert exc.value.code == 2


def test_holdout_with_empty_justification_rejected():
    ns = _parse(
        "--workflow",
        "send_invoice",
        "--mode",
        "holdout",
        "--holdout-justification",
        "   ",
        "--suites",
        "functional",
    )
    with pytest.raises(SystemExit) as exc:
        validate_args(ns)
    assert exc.value.code == 2


def test_unknown_suite_rejected():
    ns = _parse(
        "--workflow", "send_invoice", "--mode", "train", "--suites", "functional,unknown_suite"
    )
    with pytest.raises(SystemExit) as exc:
        validate_args(ns)
    assert exc.value.code == 2


def test_train_mode_default_suites_ok():
    ns = _parse(
        "--workflow",
        "send_invoice",
        "--mode",
        "train",
        "--suites",
        "functional,policy_compliance,cost_latency",
    )
    validate_args(ns)
