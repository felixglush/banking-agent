# Eval results — `send_invoice` agent (v0.1)

LLM evaluation of the SendInvoiceWorkflow agent. About **agent performance**,
**failure modes (each with its fix)**, and **what the policy caught**.

## Setup

| | |
| --- | --- |
| Dataset | `send_invoice_v0_1` (Langfuse), 119 train / 51 holdout |
| Agent model | `gpt-4.1-mini` |
| Suites | functional (exact field match), policy_compliance (exact fired-rule set), cost_latency (passthrough) |
| Run | `ev_0d5801f3a4b9` — 119 train cases, fixed prompt + clarification round-trip + deterministic contract_id |

## Headline

| Suite | Pass | Note |
| --- | --- | --- |
| **functional** | **52/119 (43.7%)** | up from 18.5% (under-specified corpus) → 37.8% → 43.7% |
| policy_compliance | **112/119 (94.1%)** | up from 77.3% — deterministic contract_id removed the `contract_must_exist` blocks |
| cost_latency | 119/119 (100%) | passthrough |

**Where the gap is now (functional fail breakdown):** of 67 failures, **61
reached `sent` but a field didn't exact-match** — `total_cents` ×57,
`contract_id` ×38, `source_type` ×34 (overlapping). Only **6** are
outcome-class misses (5 over-clarify, 1 block). So the agent almost always
produces a *policy-clean, completed* invoice; it just doesn't get every field
exactly right. The gap is **field grounding**: the amount (`total_cents`), the
`source_type`, and the `contract_id` that now follows from `source_type`.

Progression of the two suites as fixes landed:

| | functional | policy_compliance |
| --- | --- | --- |
| under-specified corpus | 18.5% | — |
| well-posed requests + prompt fix | 42.9% | 75.6% |
| + clarification round-trip (stricter) | 37.8% | 77.3% |
| **+ deterministic contract_id (current)** | **43.7%** | **94.1%** |

## Failure modes (each → its fix)

Simple list, in priority order. Each points to the lever that addresses it.

| # | Failure mode | Evidence | Fix |
| --- | --- | --- | --- |
| 1 | **Wrong amount** — total doesn't match the named basis. | `field_mismatch:[total_cents]` ×57 (the single biggest). | Grounding on the resolved rate/contract/time data; a `compute_invoice_total` tool helps the summation but the agent must first pull the right numbers. **Open — #1 driver.** |
| 2 | **Wrong `source_type`** — agent mis-classifies the billing basis (e.g. calls an Onboarding-Package rate-card invoice "contract"). | `field_mismatch:[source_type]` ×34; cascades to `contract_id` (see #3). | Prompt/grounding on classifying the named basis. **Open.** |
| 3 | **Wrong `contract_id`** — now downstream of #2: contract_id is derived from `source_type`, so a wrong source_type yields a wrong contract_id; plus wrong/no contract resolution on genuine contract cases. | `field_mismatch:[contract_id]` ×38. | Fix #2 + ensure the agent resolves the active contract for contract/time invoices. **Open.** |
| 4 | **Over-clarification** — agent asks on a request that *was* specific. | 5 sent cases ended `needs_clarification`. | Tighten the clarification trigger in the prompt (only ask when >1 invoice truly matches). **Open.** |
| — | **Contract mis-attachment / hallucination** (resolved) — agent attached a non-existent / wrong-for-source contract → FK crash or policy block. | 26 `contract_must_exist` blocks on the prior run → **0 now**. | `contract_id` derived deterministically in the workflow from `source_type` + the resolved contract; prompt corrected per source_type. `contract_must_exist` retained as a defensive guard. |
| — | **Under-clarification** (resolved) — agent guessed instead of asking. | failure on the old corpus. | Clarification round-trip shipped (ask → caller answers → complete). |
| — | **Abstention** (resolved) — agent returned an empty `total_cents=1` proposal. | dominant on the old corpus; gone. | Prompt fix + `require_amount_source` rejecting empty line items (shipped). |

## Policy saves — issues the policy caught before they happened

Concrete scenarios from run `ev_0d5801f3a4b9` where a BLOCK rule stopped a bad
invoice. Without the policy these would have been real defects.

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

## Recommendations

1. **Improve amount grounding** (failure 1) — `total_cents` is the single
   biggest field miss (×57). The agent reaches `sent` but computes/pulls the
   wrong amount; a deterministic `compute_invoice_total` tool plus tighter
   grounding on the resolved rate/contract/time data.
2. **Improve `source_type` classification** (failure 2) — wrong source_type
   (×34) also cascades into `contract_id`. Likely needs a stronger model.
3. **Tune the clarification trigger** (failure 4) — the agent over-asks on
   specific requests; ask only when >1 invoice truly matches.

## Caveats

- Single train run; case verdicts are single-sample (model is
  nondeterministic). Re-run for Wilson bands.
- cost_latency is passthrough, not an accuracy metric.
