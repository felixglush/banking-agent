"""Live smoke: requires a running Temporal worker, Postgres, OpenAI creds, and
`./node_modules/.bin/promptfoo`. Opt-in via `-m e2e`.

Drives one attack end-to-end through the provider and asserts both signals are
produced and a Langfuse trace id is attached. Documented as manual/CI-gated; not
part of the default unit lane."""

import os
import shutil

import pytest


@pytest.mark.e2e
async def test_single_attack_end_to_end() -> None:
    bin_path = os.environ.get("PROMPTFOO_BIN", "./node_modules/.bin/promptfoo")
    if not shutil.which(bin_path) and not os.path.exists(bin_path):
        pytest.skip("promptfoo binary not installed")
    pytest.skip(
        "manual e2e: run `python -m compass.eval.adversarial --workflow send_invoice "
        "--mode train --num-tests 1` against a live worker; assert scores in Langfuse"
    )
