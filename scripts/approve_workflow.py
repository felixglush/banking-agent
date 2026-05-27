"""Send the approval (or decline) signal to a running ``SendInvoiceWorkflow``.

    uv run python -m scripts.approve_workflow <workflow_id> --approve \\
        --approver felixglush

    uv run python -m scripts.approve_workflow <workflow_id> --decline \\
        --approver felixglush --notes "scope mismatch"
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from temporalio.client import Client

from workflows.send_invoice.types import ApprovalDecision
from workflows.send_invoice.workflow import SendInvoiceWorkflow

REPO_ROOT = Path(__file__).resolve().parents[1]


async def amain(workflow_id: str, decision: ApprovalDecision) -> None:
    load_dotenv(REPO_ROOT / ".env.local")
    target = os.environ.get("TEMPORAL_TARGET", "localhost:7233")
    namespace = os.environ.get("TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(target, namespace=namespace)
    handle = client.get_workflow_handle_for(SendInvoiceWorkflow.run, workflow_id=workflow_id)
    await handle.signal(SendInvoiceWorkflow.approve, decision)
    verb = "approved" if decision.approved else "declined"
    print(f"signal sent: {verb} by {decision.approver_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workflow_id")
    side = parser.add_mutually_exclusive_group(required=True)
    side.add_argument("--approve", action="store_true")
    side.add_argument("--decline", action="store_true")
    parser.add_argument("--approver", required=True, help="approver_id recorded in actor JSONB")
    parser.add_argument("--notes", default=None)
    args = parser.parse_args()
    decision = ApprovalDecision(
        approved=args.approve,
        approver_id=args.approver,
        notes=args.notes,
    )
    try:
        asyncio.run(amain(args.workflow_id, decision))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
