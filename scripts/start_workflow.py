"""Kick off one ``SendInvoiceWorkflow`` run from the shell.

    uv run python -m scripts.start_workflow \\
        --message "Invoice Acme for last quarter's onboarding work"

Prints the assigned workflow id to stdout. Pair with
``scripts.approve_workflow`` to send the approval (or decline) signal.
"""

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from temporalio.client import Client

from workflows.send_invoice.types import SendInvoiceRequest
from workflows.send_invoice.workflow import SendInvoiceWorkflow

REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_QUEUE = "send-invoice"


async def amain(message: str, timeout_seconds: int) -> None:
    load_dotenv(REPO_ROOT / ".env.local")
    target = os.environ.get("TEMPORAL_TARGET", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(target, namespace=namespace)
    workflow_id = f"send-invoice-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message=message, approval_timeout_seconds=timeout_seconds),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    print(handle.id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", required=True, help="User request to the agent.")
    parser.add_argument(
        "--approval-timeout-seconds",
        type=int,
        default=3600,
        help="How long the workflow waits for an approval signal.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(amain(args.message, args.approval_timeout_seconds))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
