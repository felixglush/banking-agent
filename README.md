# Compass

A reusable evaluation and policy framework for agentic financial workflows.

This repo uses a Send Invoice agentic action as an example workflow to showcase policy-as-code.
See the [excalidraw diagram](https://app.excalidraw.com/s/AfsNGrQkY99/55Mn7raUZsW) for a visual review.

The policy-as-code guardrails are written up here: [policies.md](./docs/policies.md). This covers the functional requirements that I came up with and the implementation that resulted from it. Policies are evaluated at explicit workflow phases and determine whether the workflow can proceed, block, or escalate for human confirmation or clarification. They encode rules that the workflow author requires to pass to prevent hallucination and adverserial attacks. Solutions are proposed for long-running workflows where rules may change over time. 

Evals are being run right now. See [v0.1](docs/eval-results-send-invoice-v0.1.md) for initial results. Upcoming: adversarial attacks with Promptfoo, and running the evals with and without the policy gates to measure their effectiveness.

Additionally, the project was scoped by brainstorming with Claude. This artifact is in [docs/build-plan.md](./docs/build-plan.md). I use the [Superpowers](https://github.com/obra/superpowers) plugin targetted at specific stages listed within the overrall build plan to spec out and implemment the details.

## Core Capabilities

1. Synthetic Data Generation
2. Running SendInvoice workflow: [README](./workflows/send_invoice/README.md)
3. SendInvoice Workflow Evaluation and Adversarial Evaluation: [running_evals.md](docs/running_evals.md).

So far this project is primarily for policy-as-code, evals, adversarial attacks, and learning Temporal -- no UI is implemented. It is only locally runnable as Temporal Cloud doesn't have a free tier. I may someday deploy Temporal to my
own cloud service, but for now this uses the local Temporal dev server.

### Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
# install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# install dependencies into a managed venv
uv sync
```

### Generate synthetic data

A procedural, deterministic synthetic bank
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

`synthetic_account_1/generated/` contains JSONL files which are loaded into postgres.

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
