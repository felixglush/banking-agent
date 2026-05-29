"""``main_agent`` for the SendInvoice workflow.

The main agent reads the ``bank`` MCP (``mcp_bank/``), drafts an
``InvoiceProposal``, and returns. Side effects live in workflow-step
activities outside ``Runner.run``. A separate scope-gate sub-agent
(``workflows.send_invoice.scope_gate``) runs first and decides
whether the request belongs to this workflow at all.

Model default is ``gpt-4.1-mini`` (overridable via ``OPENAI_MODEL``).
Credentials via ``OPENAI_API_KEY``.

Two ablation levers are configured at build time (driven by
``SendInvoiceRequest`` fields so they cross the workflow boundary
deterministically):

* ``prompt_variant`` — ``fixed`` (default) vs ``legacy``. The legacy
  prompt contains a garbled instruction that names the empty-proposal
  sentinel (``total_cents=1, line_items=[]``); the agent copies it and
  abstains. The fixed prompt forbids empty proposals. Kept toggleable so
  the abstention lift is measurable.
* ``use_invoice_tool`` — when True, the agent is given
  ``compute_invoice_total`` so it offloads the final summation.
"""

import os
from typing import Literal

from agents import Agent, Tool, function_tool
from agents.mcp.server import MCPServer

from workflows.send_invoice.types import InvoiceProposal

PromptVariant = Literal["fixed", "legacy"]

# gpt-5 is the v0.1 default: it reaches ~84% functional on the eval suite (vs
# ~44% for gpt-4.1-mini), it has a 500k-TPM tier (no eval throttling), and the
# compute_line_total tool keeps its arithmetic exact. It is a reasoning model,
# so it is slower/costlier per call and needs the higher max_turns budget in
# the workflow — override via OPENAI_MODEL for cheaper/faster runs.
DEFAULT_MODEL = "gpt-5"


@function_tool
def compute_line_total(quantity_micros: int, unit_amount_cents: int) -> int:
    """Return a line item's ``line_total_cents``, computed with correct units:
    ``quantity_micros * unit_amount_cents / 1_000_000``.

    Quantities are in MICROS (hours × 1_000_000; use 1_000_000 for a flat
    quantity of 1) and rates are in integer CENTS. Always call this for every
    line's total rather than multiplying by hand — getting the /1_000_000
    micros conversion wrong is the most common error and yields amounts off by
    10×–100×.
    """
    return quantity_micros * unit_amount_cents // 1_000_000


@function_tool
def compute_invoice_total(line_totals_cents: list[int]) -> int:
    """Return the invoice total in integer cents: the sum of the per-line
    ``line_total_cents`` values passed in.

    Call this to obtain ``total_cents`` rather than summing by hand. Pass the
    ``line_total_cents`` of every line item (each from ``compute_line_total``)
    and use the returned value verbatim as the proposal's ``total_cents``.
    """
    return sum(line_totals_cents)


# Steps + constraints shared by both prompt variants. The trailing
# "no information" guidance differs and is appended per variant.
_INSTRUCTIONS_BODY = """\
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
- Do not invent customers, amounts, contracts, or rate-card entries.
  Every line — and any `contract_id` — must trace back to MCP tool
  output. Only set `contract_id` if `get_active_contract` returned one;
  otherwise leave it null.
- The currency on the proposal must match the currency on the cited
  contract or rate card.
- `total_cents` must equal the sum of `line_total_cents` across line
  items.
"""

_FIXED_TAIL = """\
- The request names the SPECIFIC work to bill (a service, a role and
  month, a milestone, or a retainer month). Resolve exactly that work
  from the MCP tools and bill it — there is one correct invoice.
- AMOUNT, by source_type (resolve the exact figures from tools; do not
  estimate):
    - `rate_card`: the service's `list_amount_cents` from `get_rate_card`
      is the line total (quantity 1).
    - `time_tracking`: call `list_time_entries` for the named role+period,
      SUM the hours across ALL matching entries, multiply by the hourly
      rate (contract rate override if present, else the role's rate-card
      rate). Include every matching entry — a partial sum is wrong.
    - `contract` retainer: the contract's `monthly_amount_cents`; SOW
      milestone: that milestone's `amount_cents`.
- UNITS: `quantity_micros` is the quantity × 1_000_000 (so 24 hours =
  24_000_000; a flat quantity of 1 = 1_000_000). `unit_amount_cents` is the
  rate in integer cents. Do NOT do this multiplication yourself — for every
  line call `compute_line_total(quantity_micros, unit_amount_cents)` to get
  `line_total_cents`, then call `compute_invoice_total([...])` for
  `total_cents`. (Hand arithmetic here is wrong ~half the time, off by
  10×–100× from the micros/cents conversion.)
- ALWAYS emit at least one line item, each grounded in MCP tool output.
  NEVER return an empty `line_items` or a placeholder `total_cents`.
- `contract_id` by source_type:
    - `source_type="contract"` and `source_type="time_tracking"`: REQUIRED —
      call `get_active_contract(customer_id, as_of_date)` and set
      `contract_id` to that contract's id (time entries bill under the
      active contract's rates).
    - `source_type="rate_card"` and `source_type="user_specified"`:
      `contract_id` MUST be null.
  Resolve the active contract for contract/time invoices; never invent a
  contract id.

CLARIFICATION — rarely needed; DRAFT by default:
- If the request names the SPECIFIC work — a service ("Onboarding
  Package"), a role and period ("Senior Engineer services (2025-03)"), a
  milestone, a retainer month, or an explicit amount — it is COMPLETE and
  unambiguous. DRAFT it. Bill ALL entries matching the named role+period;
  do NOT ask which project, or how to split, or to confirm — those are
  fully determined by the named work. NEVER set `needs_clarification` for
  a request of the form "the invoice for: <X>".
- Ask ONLY when the request gives NO specific work to bill — a bare
  request like "send Acme an invoice" or "a time-tracking invoice" (only a
  category, no service/role/period) AND the customer has more than one
  billable item. Only then set `needs_clarification = true` with a
  specific question, and leave `line_items` empty.

You CANNOT send the invoice. After you return the proposal, the
workflow gates on human approval, then a separate activity (not
exposed to you) persists the invoice.
"""

# Original (buggy) tail — a double-negative that names the abstention
# sentinel; retained only as the ablation baseline.
_LEGACY_TAIL = """\
- If you cannot find enough information to draft an invoice, return a
  proposal with `total_cents = 1` (cents), `line_items = []` and the
  reason in `notes` is NOT acceptable — instead, raise the issue in
  `notes` AND draft your best guess. The human reviewer will correct.

You CANNOT send the invoice. After you return the proposal, the
workflow gates on human approval, then a separate activity (not
exposed to you) persists the invoice.
"""

_TOOL_HINT = """\

When `compute_invoice_total` is available, call it with every line item's
`line_total_cents` and use its result as `total_cents` — do not add the
totals yourself.
"""


def _instructions(prompt_variant: PromptVariant, use_invoice_tool: bool) -> str:
    tail = _LEGACY_TAIL if prompt_variant == "legacy" else _FIXED_TAIL
    text = _INSTRUCTIONS_BODY + tail
    if use_invoice_tool:
        text += _TOOL_HINT
    return text


def build_main_agent(
    mcp_server: MCPServer,
    *,
    prompt_variant: PromptVariant = "fixed",
    use_invoice_tool: bool = False,
) -> Agent[None]:
    """Construct the main agent bound to a workflow's MCP server reference.

    The ``mcp_server`` comes from
    ``temporalio.contrib.openai_agents.workflow.stateful_mcp_server("bank")``
    inside ``workflow.run``; calling that outside a workflow raises.
    """
    tools: list[Tool] = (
        [compute_line_total, compute_invoice_total] if use_invoice_tool else []
    )
    return Agent[None](
        name="send_invoice_main",
        instructions=_instructions(prompt_variant, use_invoice_tool),
        output_type=InvoiceProposal,
        model=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        mcp_servers=[mcp_server],
        tools=tools,
    )
