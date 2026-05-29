import json
from pathlib import Path

from compass.eval.adversarial_results import parse_results

FIXTURE = Path("tests/compass/eval/fixtures/promptfoo_results_sample.json")


def test_parse_results_extracts_two_signals() -> None:
    data = json.loads(FIXTURE.read_text())
    results = parse_results(data)
    assert len(results) == 2

    leaked = next(r for r in results if r.category == "amount_manipulation")
    assert leaked.repelled is False
    assert leaked.expected_rule_fired is True
    assert leaked.workflow_run_id == "adv-00001-aaaa"
    assert leaked.trace_id == "tr-1"

    repelled = next(r for r in results if r.category == "wrong_recipient")
    assert repelled.repelled is True
    assert repelled.expected_rule_fired is True
    assert repelled.trace_id == "tr-2"
