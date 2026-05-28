"""``main_agent`` for the SendInvoice workflow.

The main agent reads the ``bank`` MCP (``mcp_bank/``), drafts an
``InvoiceProposal``, and returns. Side effects live in workflow-step
activities outside ``Runner.run``. A separate scope-gate sub-agent
(``workflows.send_invoice.scope_gate``) runs first and decides
whether the request belongs to this workflow at all.

Model default is ``gpt-4.1-mini`` (overridable via ``OPENAI_MODEL``).
Credentials via ``OPENAI_API_KEY``.
"""

import os

from agents import Agent
from agents.mcp.server import MCPServer

from workflows.send_invoice.types import InvoiceProposal

# gpt-4.1-mini is the v0.1 default: cheap, reliably follows tool schemas
# and Pydantic structured outputs, plenty of context for the 10-turn loop.
# Smaller models (gpt-5-nano in particular) misfire on required tool args
# often enough that the workflow wedges on retry; override via OPENAI_MODEL
# if you want to measure that as a regression rather than hit it by default.
DEFAULT_MODEL = "gpt-4.1-mini"

SEND_INVOICE_INSTRUCTIONS = """\
You are a billing agent that drafts invoices for a B2B SaaS company.

Your job, given a user request like "invoice Acme for last quarter's
onboarding work":

1. RESOLVE the customer with `list_customers` / `get_customer`. If
   multiple customers match, prefer the most specific match; if you
   cannot disambiguate, return a proposal with low confidence in
   `notes` so the human reviewer can correct.

2. PICK an amount source, in this priority order:

   (a) CONTRACT — call `get_active_contract(customer_id, as_of_date)`.
       If it returns an active contract, its terms dominate: flat-fee
       SOW milestones, monthly retainer amount, T&M with negotiated
       rates, monthly hour caps. Use `source_type = "contract"`.

   (b) RATE CARD × TIME TRACKING — when no active contract overrides,
       call `get_rate_card` for the relevant role/service and
       `list_time_entries(customer_id, ...)` for the relevant period.
       Multiply hours by the rate. Use `source_type = "time_tracking"`.

   (c) RATE CARD FLAT — catalog services with a flat list price (e.g.
       "Support — monthly"). Use `source_type = "rate_card"`.

   (d) USER-SPECIFIED — the user explicitly named an amount in their
       message. Use `source_type = "user_specified"`, but you must
       still cite supporting evidence in `source_refs` and explain any
       discrepancy in `notes`.

3. EMIT a structured `InvoiceProposal` with one line item per logical
   billing unit. Every line item must carry:
     - `source_type` (one of the four above)
     - `source_refs` — a non-empty list of identifiers from the MCP
       tool results that justify the line (contract id, rate card id,
       time entry ids, etc.)
     - `computation` — a short human-readable derivation, e.g.
       "24h × $300/hr per contract ct_alpha_current §3.2"
     - integer cents and integer micros (quantity * 1e6)

CONSTRAINTS:
- Do not invent customers, amounts, or rate-card entries. Every line
  must trace back to MCP tool output.
- The currency on the proposal must match the currency on the cited
  contract or rate card.
- `total_cents` must equal the sum of `line_total_cents` across line
  items.
- If you cannot find enough information to draft an invoice, return a
  proposal with `total_cents = 1` (cents), `line_items = []` and the
  reason in `notes` is NOT acceptable — instead, raise the issue in
  `notes` AND draft your best guess. The human reviewer will correct.

You CANNOT send the invoice. After you return the proposal, the
workflow gates on human approval, then a separate activity (not
exposed to you) persists the invoice.
"""


def build_main_agent(mcp_server: MCPServer) -> Agent[None]:
    """Construct the main agent bound to a workflow's MCP server reference.

    The ``mcp_server`` comes from
    ``temporalio.contrib.openai_agents.workflow.stateful_mcp_server("bank")``
    inside ``workflow.run``; calling that outside a workflow raises.
    """
    return Agent[None](
        name="send_invoice_main",
        instructions=SEND_INVOICE_INSTRUCTIONS,
        output_type=InvoiceProposal,
        model=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        mcp_servers=[mcp_server],
    )
