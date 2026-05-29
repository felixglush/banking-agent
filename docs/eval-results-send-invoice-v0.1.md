# Eval results — `send_invoice` agent (v0.1)

LLM evaluation of the SendInvoiceWorkflow agent. About **agent performance**,
**failure modes (each with its fix)**, and **what the policy caught**.

## Setup

| | |
| --- | --- |
| Dataset | `send_invoice_v0_1` (Langfuse), 119 train / 51 holdout |
| Agent model | **gpt-5** (default; reasoning, 500k-TPM tier) |
| Suites | functional (exact field match), policy_compliance (exact fired-rule set), cost_latency (passthrough) |
| Run | `ev_124ddae33549` — 119 train cases, default config |

## Headline

| Suite | Pass | |
| --- | --- | --- |
| **functional** | **100/119 (84.0%)** | from 18.5% on the original corpus |
| policy_compliance | **111/119 (93.3%)** | |
| cost_latency | 119/119 (100%) | passthrough |

### How it got from 18.5% → 84% (each lever, measured)

| Step | functional | policy_compliance |
| --- | --- | --- |
| original (under-specified corpus, gpt-4.1-mini) | 18.5% | — |
| well-posed requests + prompt fix | 42.9% | 75.6% |
| + clarification round-trip (stricter) | 37.8% | 77.3% |
| + deterministic `contract_id` | 43.7% | 94.1% |
| + **gpt-5** (replaces gpt-4.1-mini) | 73.9% | 91.6% |
| + `compute_line_total` tool (unit-correct arithmetic) | — | — |
| + tighter clarification prompt | 74.8% | 90.8% |
| + **user_specified states its amount** (corpus) | **84.0%** | **93.3%** |

The big movers: **gpt-5** (better grounding/classification, and a 500k-TPM tier
so the eval isn't throttled like gpt-4.1's 30k), the **`compute_line_total`
tool** (the failing amounts were off by clean 10×–100× — a `quantity_micros ×
unit_amount_cents / 1e6` units bug, not resolution), and **putting the amount
in `user_specified` requests** (an ad-hoc amount isn't derivable from any
rate/contract/time data, so the case was ill-posed without it).

### Remaining 19 functional misses

- **8** — over-clarification on `policy_rejected` cases: the request is "Acme
  Corp" (one of nine "Acme*" customers), so gpt-5 asks which rather than taking
  the exact-name match and getting KYC-blocked.
- **10** — `field_mismatch` on `sent` cases (residual `contract_id` / `total_cents`
  / `source_type` grounding).
- **1** — over-clarification on a `sent` case.

## Failure modes (each → its fix)

Simple list, in priority order. Each points to the lever that addresses it.

| # | Failure mode | Evidence (current run) | Status / fix |
| --- | --- | --- | --- |
| 1 | **Over-clarification on `policy_rejected` "Acme" cases** — request says "Acme Corp" (one of 9 "Acme*" customers); gpt-5 asks which instead of taking the exact-name match and getting KYC-blocked. | 8 cases | **Open.** Either name the customer unambiguously in those requests, or prompt "an exact name match is not ambiguous." |
| 2 | **Residual field grounding** — `sent` case completes but a field (`total_cents` / `contract_id` / `source_type`) is off. | 10 cases | **Open** (mostly model). Down sharply from earlier (the unit-arithmetic and contract-derivation fixes removed the systematic part). |
| 3 | **Over-clarification on a `sent` case** | 1 case | **Open** (largely tamed by the prompt). |
| — | **Wrong amount / unit bug** (resolved) — totals off by 10×–100×. | was the #1 driver | `compute_line_total` tool does `quantity_micros × unit_amount_cents / 1e6`; agent mandated to use it. |
| — | **Contract mis-attachment / hallucination** (resolved) | 26 blocks → **0** | `contract_id` derived deterministically from `source_type` + resolved contract; `contract_must_exist` kept as a guard. |
| — | **Abstention / under-clarification / KYC-mislabelled corpus** (resolved) | — | prompt fix + empty-line guard; clarification round-trip; KYC-verified corpus filter. |
| — | **Ill-posed `user_specified`** (resolved) — ad-hoc amount not derivable. | — | request now states the amount ("the agreed $X invoice for: …"). |

## Policy saves — issues the policy caught before they happened

Concrete scenarios where a BLOCK rule stopped a bad invoice (counts from a
representative run; stable across runs). Without the policy these would have
been real defects.

| Rule | Cases | What it prevented |
| --- | --- | --- |
| `customer_kyc_verified` | 10 | Invoice to a restricted/pending/rejected-KYC customer → billing an unverified entity (BSA §326). |
| `require_amount_source` | 4 | Empty / degenerate proposal (no line items) → would have sent a $0.01 placeholder invoice. |
| `require_evidence_citation` | 4 | Line items with no `source_refs` → an unsubstantiated invoice with no audit trail. |
| `contract_must_exist` | 0 | Now a defensive guard: `contract_id` is derived deterministically, so a hallucinated/wrong contract can't reach the gate. (Caught 26 before that fix.) |

18 live rule-firings this run; each is a case the agent would have gotten
wrong and the policy gate caught before it reached `execute_send`. The earlier
run, before deterministic `contract_id`, also caught 26 contract
mis-attachments that would have FK-crashed `execute_send`.

## How the corpus was made winnable

The earlier 18.5% functional was an eval-design flaw, not the model: requests
said only `(customer, source_type)`, but customers have several invoices of a
type, so the exact total wasn't determinable. Fixes shipped:

* `sent` requests now name the **specific** basis (service / role+month /
  milestone / retainer month) → exactly one correct invoice. `verify.py`
  enforces every `sent` total is a real seed invoice, emitted only when
  `(customer, description)` is unique.
* Genuinely-generic requests carry a `clarify_answer`: the agent must **ask**,
  the caller answers (a `clarify` signal — unbounded wait by default, so a
  human gets unlimited time; the eval bounds it only to fail an over-asking
  agent fast), and the agent then drafts the one specific invoice
  (`expected=sent`). Shipped and exercised live.
* `contract_id` is **derived deterministically** in the workflow from
  `source_type` + the resolved active contract (contract/time-tracking → the
  active contract; rate_card/user_specified → null), with the prompt corrected
  to match. This removed contract hallucination entirely (policy_compliance
  75.6% → 94.1%) and made `contract_id` a function of `source_type` rather than
  an independent error.

## Winning config (reaches 84%)

`gpt-5` (default) + `compute_line_total`/`compute_invoice_total` tools
(default on) + a 7-tool MCP filter + `max_turns=20` (reasoning models need the
headroom) + deterministic `contract_id` + the well-posed corpus (specific
requests, clarification round-trip, `user_specified` states its amount).
gpt-5's 500k-TPM tier means the eval runs unthrottled; `gpt-4.1` (30k TPM) was
the throughput bottleneck. Override the model with `OPENAI_MODEL` for
cheaper/faster runs (expect lower functional).

## Recommendations (to push past 84%)

1. **The "Acme" over-clarification** (8 cases) — make the `policy_rejected`
   requests name an unambiguous customer, or teach the agent that an exact
   name match isn't ambiguous. Biggest single remaining bucket.
2. **Residual amount/source grounding** (10 cases) — mostly model; a
   higher-tier model or a per-source pricing tool would help.

## Caveats

- Single train run; case verdicts are single-sample (model is
  nondeterministic). Re-run for Wilson bands.
- cost_latency is passthrough, not an accuracy metric.
