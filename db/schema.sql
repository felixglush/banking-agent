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
--   * compass.eval runtime (Stage 7) — writes eval_runs. Per-case
--     scores live in Langfuse (Dataset Run scores), not Postgres.
--
-- Bank-data tables are reload-safe; runtime-owned tables are NOT touched
-- by the loader and must survive across data reloads.
--
-- No migration framework at v0.1: the local Postgres sidecar is
-- regenerated from this file on each dev cycle. See docs/build-plan.md
-- §Database.
--
-- Every bank-data table carries a ``tenant_id`` column with
-- DEFAULT 'default'. The MCP server does not filter on it yet — see
-- mcp_bank/README.md §Authorization (deferred). The column is present
-- now so the WHERE-clause change is purely additive when authz lands.

-- ---------------------------------------------------------------------
-- Bank-data tables (populated by synthetic_account_1/load_to_postgres.py)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customers (
    id                          TEXT PRIMARY KEY,
    tenant_id                   TEXT NOT NULL DEFAULT 'default',
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
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    name            TEXT NOT NULL,
    type            TEXT NOT NULL
        CHECK (type IN ('operating', 'payroll', 'reserve', 'credit_card')),
    currency        TEXT NOT NULL,
    balance_cents   BIGINT NOT NULL,
    opened_at       TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id               TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
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
    tenant_id           TEXT NOT NULL DEFAULT 'default',
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
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    customer_id     TEXT NOT NULL REFERENCES customers (id),
    name            TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('active', 'completed', 'on_hold'))
);

CREATE TABLE IF NOT EXISTS contracts (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL DEFAULT 'default',
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
    tenant_id            TEXT NOT NULL DEFAULT 'default',
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
    tenant_id           TEXT NOT NULL DEFAULT 'default',
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
    tenant_id       TEXT NOT NULL DEFAULT 'default',
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
    tenant_id           TEXT NOT NULL DEFAULT 'default',
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
-- docs/build-plan.md §Database. eval_runs is designed here for Stage
-- 7's harness — it holds harness-control state (holdout-run counter,
-- mode, justification, git SHA) that needs SQL enforcement. Per-case
-- pass/fail and details live in Langfuse Dataset Run scores; the
-- trace_id Langfuse stores per item == workflow_run_id, so audit_log
-- joins back via that column directly.
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

-- ---------------------------------------------------------------------
-- Stage 7 additions: ablation pairing, suite tracking, mode-gate
-- atomicity. See docs/superpowers/specs/2026-05-28-stage-7-eval-harness-design.md.
-- ---------------------------------------------------------------------

ALTER TABLE eval_runs
  ADD COLUMN IF NOT EXISTS paired_run_id  TEXT NULL REFERENCES eval_runs(run_id),
  ADD COLUMN IF NOT EXISTS policy_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  ADD COLUMN IF NOT EXISTS suite_names    TEXT[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS host_git_dirty BOOLEAN NOT NULL DEFAULT FALSE;

-- UNIQUE constraints create an underlying index; re-running raises
-- ``duplicate_table`` for the index even when ``duplicate_object`` would
-- catch the constraint itself. Catch both.
DO $$ BEGIN
  ALTER TABLE eval_runs
    ADD CONSTRAINT eval_runs_holdout_counter_unique
      UNIQUE (git_sha, commit_holdout_run_no);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN END $$;

DO $$ BEGIN
  ALTER TABLE eval_runs
    ADD CONSTRAINT eval_runs_justification_required
      CHECK (mode = 'train' OR length(trim(holdout_justification)) > 0);
EXCEPTION WHEN duplicate_object THEN END $$;

-- Drop-and-recreate (not a guarded ADD) so the allowed-suite list stays in
-- sync when a new suite is introduced — Stage 8 added 'adversarial'.
ALTER TABLE eval_runs DROP CONSTRAINT IF EXISTS eval_runs_suite_names_valid;
ALTER TABLE eval_runs
  ADD CONSTRAINT eval_runs_suite_names_valid
    CHECK (suite_names <@ ARRAY['functional','policy_compliance','cost_latency','adversarial']::text[]);

COMMENT ON COLUMN eval_runs.run_id IS
  'Stable identifier (uuid4 hex). Used as the Langfuse Dataset Run name; the join key from Postgres to Langfuse for the run.';
COMMENT ON COLUMN eval_runs.git_sha IS
  'HEAD commit at run start. Required for the per-commit holdout-run counter (build-plan §0).';
COMMENT ON COLUMN eval_runs.mode IS
  'train | holdout. Holdout mode requires holdout_justification and increments commit_holdout_run_no.';
COMMENT ON COLUMN eval_runs.holdout_justification IS
  'Free-text reason a holdout run was invoked. Required when mode=holdout; refused otherwise.';
COMMENT ON COLUMN eval_runs.commit_holdout_run_no IS
  '1..3 — ordinal of this holdout run for this git_sha. UNIQUE(git_sha, commit_holdout_run_no) enforces the cap. NULL when mode=train.';
COMMENT ON COLUMN eval_runs.paired_run_id IS
  'Self-FK to the paired ablation run (policy-on ↔ policy-off). NULL when standalone.';
COMMENT ON COLUMN eval_runs.policy_enabled IS
  'FALSE when COMPASS_POLICY_DISABLE=1 during the run. Determines which side of an ablation pair this row represents.';
COMMENT ON COLUMN eval_runs.suite_names IS
  'Suite list executed in this run. A paired-run report asserts both sides ran the same suite set before computing lift.';
COMMENT ON COLUMN eval_runs.host_git_dirty IS
  'TRUE if the working tree had uncommitted changes when the run started. Soft warning surfaced in reports.';
COMMENT ON COLUMN eval_runs.started_at IS
  'Wall-clock start of the harness invocation. Used for cost/latency rollups.';
COMMENT ON COLUMN eval_runs.finished_at IS
  'NULL while in flight or if crashed; set on clean completion. Rows are never deleted.';
