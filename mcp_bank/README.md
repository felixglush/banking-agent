# `bank` MCP

Read-only, structured tool surface over the v0.1 banking dataset
(Postgres, populated by `synthetic_account_1/load_to_postgres.py`).
Built with [FastMCP](https://github.com/jlowin/fastmcp); tool schemas
are auto-generated from Python type annotations and the Pydantic models
in `mcp_bank/models.py`.

See docs/build-plan.md §Stage 3 for the full design.

## Status

**Skeleton.** Tool signatures, I/O models, and the FastMCP server
instance are wired; tool bodies raise `NotImplementedError`. The
parameterized-SQL handlers and the `psycopg` async pool land in the
follow-up PR that closes Stage 3.

## Tools

| Tool                                       | Returns                |
| ------------------------------------------ | ---------------------- |
| `list_customers(name_contains?)`           | `list[Customer]`       |
| `get_customer(customer_id)`                | `Customer \| None`     |
| `list_invoices(customer_id?, status?)`     | `list[Invoice]`        |
| `get_invoice(invoice_id)`                  | `Invoice \| None`      |
| `list_transactions(account_id?, from_date?, to_date?)` | `list[Transaction]` |
| `get_rate_card(service?, role?)`           | `list[RateCardEntry]`  |
| `list_time_entries(customer_id, project_id?, from_date?, to_date?)` | `list[TimeEntry]` |
| `get_active_contract(customer_id, as_of_date)` | `Contract \| None` |
| `list_contracts(customer_id)`              | `list[Contract]`       |

## Idempotency contract

**Every tool exposed by this server is read-only and idempotent.**

The `OpenAIAgentsPlugin` wraps each MCP call as a Temporal activity
with default retries (docs/build-plan.md §Stage 4, interop rule 4), so
a malformed model response that triggers a retry can re-execute
already-completed tool calls. Read-only tools are safe under that
behavior; the v0.1 surface is entirely SELECTs.

Adding a writable tool later requires one of:

- the underlying operation is idempotent by design (e.g., dedupe by a
  client-supplied request id stored in a uniqueness constraint), or
- the tool is registered with `retry_policy=RetryPolicy(maximum_attempts=1)`
  on the Temporal side.

Re-verify this contract whenever the tool list changes.

## No raw SQL

No `execute_sql` / `run_query` / query-builder tool is exposed to the
LLM. The structured surface is required for (a) grading functional
accuracy against ground-truth tool invocations and (b) the tool-call
history that `pre_action_proposal` policy rules consume
(docs/build-plan.md §Stage 3, §Validation Criteria 2).

## Running

```bash
uv run python -m mcp_bank
```

Stdio transport; intended to be launched by the Temporal worker's
`StatefulMCPServerProvider`. Smoke-testable with the MCP inspector once
handlers are wired.
