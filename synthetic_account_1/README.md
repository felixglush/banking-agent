# Synthetic Account 1

Procedural, deterministic-from-seed dataset for a representative
small-business banking customer. JSONL under `generated/` is the
canonical artifact (version-controlled, diffable); Postgres is the
queryable runtime surface the `bank` MCP reads from (Stage 3+).

This README is the source of truth for the company profile. Every YAML
file under `config/` derives its numbers from the sections below.

## Company profile — Bramble AI

- Series A AI-native B2B SaaS startup
- 22 employees today
- ~$4M ARR; ~120 paying customers; ~$33k average ACV (log-normal)
- 24 months of operating history (seed at month 0, Series A at month 18)
- Operating + payroll + reserve + credit-card accounts
- ~60 customer contracts: MSA / SOW / monthly retainer mix
- ~280 historical invoices with line items and payment status
- Ambiguity-rich subset: similar-name customer pairs, restricted-KYC
  customers, multi-billing-contact customers, edge-case rate-card
  entries

The dataset is the load-time target for the policy engine — every
`contract` row is validated against the `Contract` Pydantic schema in
`pydantic_models.py`, and anything that fails validation never reaches
the policy engine (build-plan line 118).

## Files

```
synthetic_account_1/
├── README.md            ← you are here
├── pydantic_models.py   ← Contract schema + other typed models
├── simulate.py          ← writes generated/ from config/
├── verify.py            ← sanity-checks generated/
├── load_to_postgres.py  ← TRUNCATE + bulk reload into bank-data tables
├── config/
│   ├── company.yaml       ← company-level constants + accounts + seed
│   ├── vendors.yaml       ← recurring vendor expenses (cadence + range)
│   ├── customers.yaml     ← cohort weights, KYC mix, name tokens, geos
│   ├── rate_cards.yaml    ← published list rates
│   ├── contracts.yaml     ← billing-mix per cohort, term/cap/discount knobs
│   └── adversarial.yaml   ← ambiguity-rich edge cases
├── generated/            ← deterministic from the seed; commit
│   ├── bank/
│   │   ├── accounts.json
│   │   ├── customers.jsonl
│   │   ├── transactions.jsonl
│   │   ├── invoices.jsonl
│   │   ├── invoice_line_items.jsonl
│   │   └── disputes.jsonl   ← sparse at v0.1; Stage 14 ships the full ~40
│   └── account_internal/
│       ├── projects.jsonl
│       ├── contracts.jsonl
│       ├── time_tracking.jsonl
│       └── rate_card_lookup.jsonl
└── ground_truth/
    ├── train/   ← used during prompt/rule iteration (Stage 11)
    └── holdout/ ← locked; only read for final reported numbers
```

## Running

```sh
# generate (deterministic from the default seed in config/company.yaml)
uv run python -m synthetic_account_1.simulate

# override the seed
uv run python -m synthetic_account_1.simulate --seed 42

# sanity-check
uv run python -m synthetic_account_1.verify

# bulk-load (once a Postgres is up; runs db/schema.sql first).
# Local DSN matches docker-compose.yml; override POSTGRES_PASSWORD via
# env to use a different password, or replace the whole DSN to point at
# a managed Postgres (Neon: ?sslmode=require).
COMPASS_PG_DSN=postgres://compass:compass@localhost:5432/compass \
    uv run python -m synthetic_account_1.load_to_postgres
```

`simulate.py` is pure procedural — no LLM. Re-running with the same
seed produces byte-identical JSONL. Counterfactual perturbations DO use
an LLM but that's Stage 9, applied on top of the frozen seed dataset.

`load_to_postgres.py` touches bank-data tables only. `audit_log`,
`policy_snapshots`, and `eval_runs` are runtime-owned and survive
across data reloads.
