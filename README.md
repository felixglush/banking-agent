# Overview

A reusable evaluation and policy framework for agentic financial workflows.
See [docs/build-plan.md](./docs/build-plan.md) for the full thesis, scope,
architecture, and stage-by-stage build plan.

These are some policy examples provided by the package. Clients can extend with their own policies to enforce custom behavior.
- Hallucination — agent invoices a customer that doesn't exist.
- Regulatory compliance — billing customers whose KYC isn't done.
- Authorization thresholds — large invoices need a human eye, not just an approval click.
- Provenance / fabrication — every dollar billed must trace to evidence.
- Contract violations — the invoice contradicts the customer's contract.
- Time-of-check vs time-of-use — the world changed between human approval and execution.
- Audit completeness — the trail itself must be defensible.


## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
# install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# install dependencies into a managed venv
uv sync
```

## Generate synthetic data

A procedural, deterministic-from-seed synthetic bank
("Synthetic Account 1") with banking + invoicing + customers + rate cards +
contracts, plus ground-truth labels for v0.1 evals.

```sh
# generate JSONL into synthetic_account_1/generated/
uv run python -m synthetic_account_1.simulate

# sanity-check the generated JSONL
uv run python -m synthetic_account_1.verify

# (later, once Postgres is wired up) bulk-load into Postgres
uv run python -m synthetic_account_1.load_to_postgres
```

JSONL under `synthetic_account_1/generated/` is the canonical artifact —
deterministic from the seed, version-controlled, diffable. Postgres is just
the queryable runtime surface the MCP server reads from.

## Repo layout

```
banking-agent/
├── CLAUDE.md                # agent rules (referenced by AGENTS.md)
├── AGENTS.md                # pointer to CLAUDE.md
├── README.md                # this file
├── docs/
│   └── build-plan.md        # full project plan
├── db/
│   └── schema.sql           # shared Postgres DDL
├── synthetic_account_1/     # data generator
├── mcp_bank/                # read-only MCP over the bank dataset
├── workflows/
│   └── send_invoice/        # Temporal + Agents SDK workflow
├── scripts/                 # workflow start / approve CLIs
└── tests/                   # workflow + MCP functional tests
```

Subsequent stages add `compass/` (policy + eval framework) and the rest of
`workflows/` — see the build plan.
