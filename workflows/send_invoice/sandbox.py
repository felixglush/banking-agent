"""Workflow sandbox configuration for ``SendInvoiceWorkflow``.

Temporal's workflow sandbox re-imports the workflow module in isolation
to enforce determinism. A few transitive dependencies of our stack (FastMCP
brings in ``beartype``, which has a documented circular-import issue when
re-imported in nested contexts) can't survive that re-import once they've
been loaded into ``sys.modules`` by another pytest fixture or earlier code.

The fix is the documented Temporal escape hatch: declare those modules as
"passthrough" so the sandbox uses the already-loaded copy instead of
re-executing them. This is purely structural — none of the passthrough
modules participate in workflow control flow.
"""

from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

_PASSTHROUGH_MODULES: tuple[str, ...] = (
    # FastMCP transitive deps that bite when re-imported in the sandbox.
    "beartype",
    "fastmcp",
    "mcp",
    # OpenAI Agents SDK + OTel + OpenInference are themselves heavy and
    # purely infrastructural; pass them through so the sandbox doesn't
    # try to re-execute their import-time side effects.
    "agents",
    "openai",
    "openinference",
    "opentelemetry",
    # Database driver is only used by activities, never workflow code,
    # but it sometimes shows up in sys.modules before the sandbox runs.
    "psycopg",
    "psycopg_pool",
    # Stage 5: compass.policy and policies.* import psycopg / openai / etc.
    # transitively. They're activity-only consumers but the modules
    # appear in sys.modules during worker import.
    "compass",
    "policies",
)


def build_sandboxed_runner() -> SandboxedWorkflowRunner:
    """Standard sandbox runner with our passthrough list applied."""
    restrictions = SandboxRestrictions.default
    for module in _PASSTHROUGH_MODULES:
        restrictions = restrictions.with_passthrough_modules(module)
    return SandboxedWorkflowRunner(restrictions=restrictions)
