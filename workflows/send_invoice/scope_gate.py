"""Scope gate for the SendInvoice workflow.

A small sub-agent (no MCP, no tools) classifies the user message as
``send_invoice`` or ``out_of_scope``. The structured output flows
into the workflow's input_validation policy context; the
``intent_must_be_send_invoice`` rule in ``policies/send_invoice.py``
gates on it.

Multi-class at Stage 16: widen ``IntentLabel`` and update
``SCOPE_GATE_INSTRUCTIONS``; the workflow plumbing does not change.
"""

import os
from typing import Literal

from agents import Agent
from pydantic import BaseModel, ConfigDict, Field

from workflows.send_invoice.agents import DEFAULT_MODEL

IntentLabel = Literal["send_invoice", "out_of_scope"]


class IntentClassification(BaseModel):
    """Structured output of the scope-gate agent.

    Stored under ``context["classification"]`` so primitive predicates
    can read fields via dotted paths
    (e.g. ``classification.intent``).
    """

    model_config = ConfigDict(extra="forbid")

    intent: IntentLabel
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


SCOPE_GATE_INSTRUCTIONS = """\
You are the scope gate for a billing agent that can only do one
thing: draft and send invoices for a B2B SaaS company.

Classify the user's request as one of:

- "send_invoice": the user wants you to draft, prepare, or send an
  invoice to a customer for work performed or services rendered.
  Examples: "invoice Acme for last quarter's work", "bill Stark
  Industries $7,200 for the Q1 onboarding", "draft an invoice for
  the consulting we did in March".

- "out_of_scope": anything else. Wire transfers, account opens,
  refunds, dispute investigations, payment lookups, general
  questions, transfers between internal accounts, weather queries,
  small-talk. If you are unsure, classify as out_of_scope.

Always include:
  - intent: the classification
  - confidence: 0.0-1.0; how sure you are
  - rationale: one short sentence explaining the call

Do not invoke tools. Do not propose actions. You only classify.
"""


def build_scope_gate_agent() -> Agent[None]:
    """Construct the scope-gate sub-agent.

    Runs inside the workflow body as an auto-activity via
    ``OpenAIAgentsPlugin`` (same mechanism as the main agent). No
    MCP servers, no tools — the classifier never reads any data.
    """
    return Agent[None](
        name="send_invoice_scope_gate",
        instructions=SCOPE_GATE_INSTRUCTIONS,
        output_type=IntentClassification,
        model=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        mcp_servers=[],
    )
