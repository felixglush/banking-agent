"""Send-invoice policy at v0.1.

This module is the authoritative policy for SendInvoiceWorkflow.
``RULES`` is hashed once per evaluate_policy invocation and
snapshotted to policy_snapshots; every audit_log row carries the hash.

Rule ids are stable identifiers — they appear in audit_log.rule_id
and in historic queries. Renaming an in-use id breaks audit reads;
treat ids as append-only.

Fourteen rules total: nine framework-core primitives (including the
Stage-6 scope-gate ``intent_in_allowlist``) plus five app-specific
Billing integrity primitives. Every Billing integrity rule and the
scope-gate rule carry ``must_be_covered=True`` so Stage 10's CI gate
catches dead-code regressions in those families.
"""

from compass.policy import Phase, Rule, Severity
from compass.policy.primitives.approval import (
    prohibit_policy_drift_after_confirmation,
    prohibit_silent_modification_after_confirmation,
)
from compass.policy.primitives.audit import (
    log_data_sources_consulted,
    log_policy_version,
)
from compass.policy.primitives.evidence import require_evidence_citation
from compass.policy.primitives.identity import entity_status_equals
from compass.policy.primitives.intent import intent_in_allowlist
from compass.policy.primitives.resolution import require_existing_entity
from compass.policy.primitives.value import numeric_threshold

# Importing this module triggers @primitive registration of the four
# Billing integrity primitives. Must come before RULES so the rule
# constructors below can call the factories.
from workflows.send_invoice.primitives import (
    contract_consistency_check,
    currency_consistency_check,
    prohibit_exceed_contract_cap,
    require_amount_source,
    require_contract_exists,
)

RULES: list[Rule] = [
    # ---- input_validation — scope gate ----
    Rule(
        id="intent_must_be_send_invoice",
        phase=Phase.input_validation,
        predicate=intent_in_allowlist(
            field="classification.intent",
            allowed=frozenset({"send_invoice"}),
        ),
        regulatory_basis=("internal SOP-SCOPE-01",),
        tags=("scope_gate",),
        must_be_covered=True,
    ),
    # ---- pre_action_proposal — bulk of policy load ----
    Rule(
        id="customer_must_exist",
        phase=Phase.pre_action_proposal,
        predicate=require_existing_entity(
            field="resolved_entities.customer",
            entity_type="customer",
        ),
        regulatory_basis=("internal SOP-CUST-01",),
        tags=("resolution",),
        must_be_covered=True,
    ),
    Rule(
        id="customer_kyc_verified",
        phase=Phase.pre_action_proposal,
        predicate=entity_status_equals(
            field="resolved_entities.customer.kyc_status",
            expected_status="verified",
        ),
        regulatory_basis=("BSA §326",),
        tags=("kyc", "BSA"),
        must_be_covered=True,
    ),
    Rule(
        id="invoice_amount_cap",
        phase=Phase.pre_action_proposal,
        predicate=numeric_threshold(field="proposal.total_cents", max=10_000_000),
        severity=Severity.ESCALATE,  # > $100k → human review
        regulatory_basis=("internal SOP-BILL-04",),
        tags=("amount_threshold",),
    ),
    Rule(
        id="require_amount_source",
        phase=Phase.pre_action_proposal,
        predicate=require_amount_source(),
        regulatory_basis=("internal SOP-BILL-02",),
        tags=("billing_integrity",),
        must_be_covered=True,
    ),
    Rule(
        id="require_evidence_citation",
        phase=Phase.pre_action_proposal,
        predicate=require_evidence_citation(
            field="proposal.line_items[*].source_refs",
        ),
        regulatory_basis=("internal SOP-BILL-02",),
        tags=("billing_integrity", "evidence"),
        must_be_covered=True,
    ),
    Rule(
        id="contract_must_exist",
        phase=Phase.pre_action_proposal,
        predicate=require_contract_exists(),
        regulatory_basis=("internal SOP-BILL-03",),
        tags=("billing_integrity", "resolution"),
        must_be_covered=True,
    ),
    Rule(
        id="contract_consistency",
        phase=Phase.pre_action_proposal,
        predicate=contract_consistency_check(),
        regulatory_basis=("internal SOP-BILL-03",),
        tags=("billing_integrity",),
        must_be_covered=True,
    ),
    Rule(
        id="prohibit_exceed_contract_cap",
        phase=Phase.pre_action_proposal,
        predicate=prohibit_exceed_contract_cap(),
        regulatory_basis=("internal SOP-BILL-03",),
        tags=("billing_integrity",),
        must_be_covered=True,
    ),
    Rule(
        id="currency_consistency",
        phase=Phase.pre_action_proposal,
        predicate=currency_consistency_check(),
        regulatory_basis=("internal SOP-BILL-05",),
        tags=("billing_integrity",),
        must_be_covered=True,
    ),
    # ---- pre_execute — drift detection ----
    Rule(
        id="no_silent_modification_after_confirmation",
        phase=Phase.pre_execute,
        predicate=prohibit_silent_modification_after_confirmation(),
        regulatory_basis=("internal SOP-CTRL-01",),
        tags=("integrity",),
    ),
    Rule(
        id="no_policy_drift_after_confirmation",
        phase=Phase.pre_execute,
        predicate=prohibit_policy_drift_after_confirmation(),
        severity=Severity.ESCALATE,  # tightened policy → re-approval
        regulatory_basis=("internal SOP-CTRL-02",),
        tags=("integrity",),
    ),
    # ---- audit_validation — terminal-row completeness ----
    Rule(
        id="audit_has_policy_version",
        phase=Phase.audit_validation,
        predicate=log_policy_version(),
        regulatory_basis=("internal SOP-AUDIT-01",),
        tags=("audit_completeness",),
    ),
    Rule(
        id="audit_has_data_sources",
        phase=Phase.audit_validation,
        predicate=log_data_sources_consulted(),
        regulatory_basis=("internal SOP-AUDIT-01",),
        tags=("audit_completeness",),
    ),
]
