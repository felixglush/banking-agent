-- Compass v0.1 — shared Postgres DDL.
--
-- This file is the single source of truth for the v0.1 schema. Three
-- writers share it:
--
--   * synthetic_account_1/load_to_postgres.py — populates the bank-data
--     tables (customers, accounts, transactions, invoices,
--     invoice_line_items, rate_cards, time_entries, projects, contracts,
--     disputes) by TRUNCATE + bulk reload from generated JSONL.
--   * compass.policy runtime (Stage 4/5) — appends to audit_log and
--     upserts into policy_snapshots.
--   * compass.eval runtime (Stage 7) — writes eval_runs and eval_results.
--
-- Bank-data tables are reload-safe; runtime-owned tables are NOT touched
-- by the loader and must survive across data reloads.
--
-- No migration framework at v0.1: the local Postgres sidecar is
-- regenerated from this file on each dev cycle. See docs/build-plan.md
-- §Database.

-- ---------------------------------------------------------------------
-- Bank-data tables (populated by synthetic_account_1/load_to_postgres.py)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customers (
    id                          TEXT PRIMARY KEY,
    name                        TEXT NOT NULL,
    email                       TEXT NOT NULL,
    address                     TEXT NOT NULL,
    kyc_status                  TEXT NOT NULL
        CHECK (kyc_status IN ('verified', 'pending', 'restricted', 'rejected')),
    default_payment_terms_days  INT  NOT NULL,
    cohort                      TEXT NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS customers_name_idx ON customers (name);

CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL
        CHECK (type IN ('operating', 'payroll', 'reserve', 'credit_card')),
    currency        TEXT NOT NULL,
    balance_cents   BIGINT NOT NULL,
    opened_at       TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id               TEXT PRIMARY KEY,
    account_id       TEXT NOT NULL REFERENCES accounts (id),
    amount_cents     BIGINT NOT NULL,
    direction        TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    counterparty     TEXT NOT NULL,
    memo             TEXT NOT NULL,
    category         TEXT NOT NULL,
    posted_at        TIMESTAMPTZ NOT NULL,
    related_invoice_id TEXT
);

CREATE INDEX IF NOT EXISTS transactions_account_idx ON transactions (account_id);
CREATE INDEX IF NOT EXISTS transactions_posted_idx ON transactions (posted_at);

CREATE TABLE IF NOT EXISTS rate_cards (
    id                  TEXT PRIMARY KEY,
    service             TEXT NOT NULL,
    role                TEXT,
    unit                TEXT NOT NULL CHECK (unit IN ('hour', 'flat', 'month')),
    list_amount_cents   BIGINT NOT NULL,
    currency            TEXT NOT NULL,
    effective_from      DATE NOT NULL,
    effective_to        DATE
);

CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers (id),
    name            TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('active', 'completed', 'on_hold'))
);

CREATE TABLE IF NOT EXISTS contracts (
    id                  TEXT PRIMARY KEY,
    customer_id         TEXT NOT NULL REFERENCES customers (id),
    kind                TEXT NOT NULL CHECK (kind IN ('msa', 'sow', 'retainer')),
    effective_from      DATE NOT NULL,
    expires_at          DATE,
    currency            TEXT NOT NULL,
    billing_structure   JSONB NOT NULL,
    rate_overrides      JSONB NOT NULL DEFAULT '[]'::jsonb,
    monthly_hour_cap    INT,
    scope_summary       TEXT NOT NULL,
    source_doc_ref      TEXT
);

CREATE INDEX IF NOT EXISTS contracts_customer_idx ON contracts (customer_id);

CREATE TABLE IF NOT EXISTS invoices (
    id                   TEXT PRIMARY KEY,
    customer_id          TEXT NOT NULL REFERENCES customers (id),
    issued_at            TIMESTAMPTZ NOT NULL,
    due_at               TIMESTAMPTZ NOT NULL,
    total_cents          BIGINT NOT NULL,
    currency             TEXT NOT NULL,
    status               TEXT NOT NULL
        CHECK (status IN ('draft', 'sent', 'paid', 'overdue', 'disputed')),
    payment_received_at  TIMESTAMPTZ,
    source_type          TEXT NOT NULL
        CHECK (source_type IN ('contract', 'rate_card', 'time_tracking', 'user_specified')),
    contract_id          TEXT REFERENCES contracts (id),
    dispute_flag         BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS invoices_customer_idx ON invoices (customer_id);
CREATE INDEX IF NOT EXISTS invoices_status_idx ON invoices (status);

CREATE TABLE IF NOT EXISTS invoice_line_items (
    id                  TEXT PRIMARY KEY,
    invoice_id          TEXT NOT NULL REFERENCES invoices (id),
    line_no             INT  NOT NULL,
    description         TEXT NOT NULL,
    quantity_micros     BIGINT NOT NULL,   -- quantity * 1e6, for fractional hours
    unit_amount_cents   BIGINT NOT NULL,
    line_total_cents    BIGINT NOT NULL,
    source_type         TEXT NOT NULL
        CHECK (source_type IN ('contract', 'rate_card', 'time_tracking', 'user_specified')),
    source_refs         JSONB NOT NULL,
    computation         TEXT NOT NULL,
    UNIQUE (invoice_id, line_no)
);

CREATE INDEX IF NOT EXISTS invoice_line_items_invoice_idx ON invoice_line_items (invoice_id);

CREATE TABLE IF NOT EXISTS time_entries (
    id              TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers (id),
    project_id      TEXT NOT NULL REFERENCES projects (id),
    role            TEXT NOT NULL,
    hours_micros    BIGINT NOT NULL,       -- hours * 1e6
    occurred_at     DATE NOT NULL,
    description     TEXT NOT NULL,
    invoiced        BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS time_entries_customer_idx ON time_entries (customer_id);
CREATE INDEX IF NOT EXISTS time_entries_project_idx ON time_entries (project_id);

-- Placeholder columns for v0.2 (Stage 14 ships the ~40-dispute corpus).
-- Table must exist so Stage 3's MCP server can issue SELECTs against it.
CREATE TABLE IF NOT EXISTS disputes (
    id                  TEXT PRIMARY KEY,
    transaction_id      TEXT NOT NULL REFERENCES transactions (id),
    opened_at           TIMESTAMPTZ NOT NULL,
    kind                TEXT NOT NULL,
    status              TEXT NOT NULL,
    resolution_outcome  TEXT
);

CREATE INDEX IF NOT EXISTS disputes_transaction_idx ON disputes (transaction_id);

-- ---------------------------------------------------------------------
-- Runtime-owned tables (NOT touched by load_to_postgres.py).
-- audit_log + policy_snapshots are specified verbatim in
-- docs/build-plan.md §Database. eval_runs + eval_results are designed
-- here for Stage 7's harness.
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    workflow_run_id TEXT NOT NULL,
    phase           TEXT NOT NULL,
    event_kind      TEXT NOT NULL,
    rule_id         TEXT,
    sequence_no     INT NOT NULL,
    policy_hash     TEXT NOT NULL,
    decision        TEXT,
    actor           JSONB,
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workflow_run_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS audit_log_run_phase_idx ON audit_log (workflow_run_id, phase);
CREATE INDEX IF NOT EXISTS audit_log_rule_idx ON audit_log (rule_id) WHERE rule_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS audit_log_policy_hash_idx ON audit_log (policy_hash);

CREATE TABLE IF NOT EXISTS policy_snapshots (
    policy_hash TEXT PRIMARY KEY,
    workflow    TEXT NOT NULL,
    rules_json  JSONB NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- eval_runs: one row per harness invocation. The 3-runs-per-commit
-- holdout counter (§Eval Framework §0) lives in commit_holdout_run_no.
CREATE TABLE IF NOT EXISTS eval_runs (
    run_id                      TEXT PRIMARY KEY,
    git_sha                     TEXT NOT NULL,
    mode                        TEXT NOT NULL CHECK (mode IN ('train', 'holdout')),
    holdout_justification       TEXT,
    commit_holdout_run_no       INT,
    started_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at                 TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS eval_runs_git_sha_idx ON eval_runs (git_sha);

-- eval_results: one row per (eval run, case, suite). workflow_run_id
-- links back to audit_log for trace-assertion suites.
CREATE TABLE IF NOT EXISTS eval_results (
    run_id          TEXT NOT NULL REFERENCES eval_runs (run_id),
    case_id         TEXT NOT NULL,
    suite           TEXT NOT NULL,
    workflow_run_id TEXT,
    passed          BOOLEAN NOT NULL,
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, case_id, suite)
);

CREATE INDEX IF NOT EXISTS eval_results_workflow_run_idx
    ON eval_results (workflow_run_id) WHERE workflow_run_id IS NOT NULL;
