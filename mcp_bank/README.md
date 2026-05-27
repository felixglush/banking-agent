# `bank` MCP

Read-only, structured tool surface over the v0.1 banking dataset
(Postgres, populated by `synthetic_account_1/load_to_postgres.py`).
Built with [FastMCP](https://github.com/jlowin/fastmcp); tool schemas
are auto-generated from Python type annotations and the Pydantic models
in `mcp_bank/models.py`.

See docs/build-plan.md §Stage 3 for the full design.

## Status

All nine tools implemented as parameterized `SELECT`s against the
bank-data tables. The server lifespan opens a single `psycopg`
`AsyncConnectionPool` from `COMPASS_PG_DSN` and tears it down on
shutdown; handlers acquire connections via `mcp_bank.db.get_pool()`.

Functional tests live at `tests/mcp_bank/test_server.py`; fixtures in
the package conftest spin up `compass_test`, apply `db/schema.sql`,
load a small hand-rolled corpus, and inject a pool into
`mcp_bank.db`. Run with `uv run pytest`.

## Tools

| Tool                                       | Returns                       |
| ------------------------------------------ | ----------------------------- |
| `list_customers(name_contains?)`           | `BoundedList[Customer]`       |
| `get_customer(customer_id)`                | `Customer \| None`            |
| `list_invoices(customer_id?, status?)`     | `BoundedList[Invoice]`        |
| `get_invoice(invoice_id)`                  | `Invoice \| None`             |
| `list_transactions(account_id?, from_date?, to_date?)` | `BoundedList[Transaction]` |
| `get_rate_card(service?, role?)`           | `BoundedList[RateCardEntry]`  |
| `list_time_entries(customer_id, project_id?, from_date?, to_date?)` | `BoundedList[TimeEntry]` |
| `get_active_contract(customer_id, as_of_date)` | `Contract \| None`        |
| `list_contracts(customer_id)`              | `BoundedList[Contract]`       |

## Result cap

Every list-returning tool wraps its result in `BoundedList[T]`:

```python
class BoundedList[T](BaseModel):
    items: list[T]
    truncated: bool
```

The server applies a hard `LIMIT MAX_ROWS` (currently 500) to every
underlying query. If more rows matched than the cap, `truncated=True`
and the agent is expected to narrow its filters and re-query — the
server does not paginate the missing rows. This shape was chosen over
cursor/offset pagination because the agent reasons better over a
single bounded result than over a loop-and-merge across pages; the
flag prevents silent data loss when the cap fires.

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
