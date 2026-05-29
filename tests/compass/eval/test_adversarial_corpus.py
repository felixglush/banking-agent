from pathlib import Path

from compass.eval.adversarial_corpus import build_redteam_config, load_contexts

CONTEXTS = Path("evals/adversarial/contexts.yaml")


def test_load_contexts_parses_four_categories() -> None:
    ctx = load_contexts(CONTEXTS)
    tags = {c.tag for c in ctx.categories}
    assert tags == {
        "freeform_injection",
        "amount_manipulation",
        "wrong_recipient",
        "kyc_and_citation",
    }
    amt = next(c for c in ctx.categories if c.tag == "amount_manipulation")
    assert amt.expected_rule_ids == ["invoice_amount_cap", "prohibit_exceed_contract_cap"]


def test_build_redteam_config_for_one_category() -> None:
    ctx = load_contexts(CONTEXTS)
    cat = next(c for c in ctx.categories if c.tag == "wrong_recipient")
    cfg = build_redteam_config(
        ctx,
        cat,
        provider_path="evals/adversarial/provider.py",
        num_tests=3,
    )
    assert cfg["providers"] == ["file://evals/adversarial/provider.py"]
    assert cfg["redteam"]["purpose"].startswith("A banking back-office agent")
    assert cfg["redteam"]["plugins"][0]["id"] == "policy"
    assert cfg["redteam"]["plugins"][0]["numTests"] == 3
    assert cfg["redteam"]["strategies"] == [{"id": "jailbreak"}]
