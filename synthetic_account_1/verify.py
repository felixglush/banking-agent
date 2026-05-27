"""Sanity checks against the generated Synthetic Account 1 JSONL.

Each check is a small function that opens the JSONL and asserts
something meaningful. Exit code 0 on success with a summary line; non-
zero on the first failure with a precise message.

Run as a module so paths resolve relative to the package directory::

    uv run python -m synthetic_account_1.verify
"""

import json
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from synthetic_account_1.pydantic_models import Contract

PACKAGE_DIR = Path(__file__).resolve().parent
GENERATED = PACKAGE_DIR / "generated"
GROUND_TRUTH = PACKAGE_DIR / "ground_truth"

BANK = GENERATED / "bank"
INTERNAL = GENERATED / "account_internal"


class VerifyError(Exception):
    """Raised when a sanity check fails."""


@dataclass(frozen=True)
class Summary:
    customers: int
    invoices: int
    line_items: int
    transactions: int
    contracts: int
    time_entries: int
    disputes: int


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield cast(dict[str, Any], json.loads(line))
            except json.JSONDecodeError as exc:
                raise VerifyError(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise VerifyError(f"missing required file: {path}")
    return list(_iter_jsonl(path))


def _read_json(path: Path) -> Any:
    if not path.exists():
        raise VerifyError(f"missing required file: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------


def check_customer_count_in_band(customers: list[dict[str, Any]]) -> None:
    if not (100 <= len(customers) <= 140):
        raise VerifyError(f"customer count {len(customers)} outside [100, 140]")


def check_invoice_count_in_band(invoices: list[dict[str, Any]]) -> None:
    if not (200 <= len(invoices) <= 400):
        raise VerifyError(f"invoice count {len(invoices)} outside [200, 400]")


def check_contract_count_in_band(contracts: list[dict[str, Any]]) -> None:
    if not (40 <= len(contracts) <= 80):
        raise VerifyError(f"contract count {len(contracts)} outside [40, 80]")


def check_kyc_statuses(customers: list[dict[str, Any]]) -> None:
    allowed = {"verified", "pending", "restricted", "rejected"}
    for c in customers:
        if c["kyc_status"] not in allowed:
            raise VerifyError(f"customer {c['id']} has bad kyc_status {c['kyc_status']!r}")


def check_invoice_customer_fk(
    invoices: list[dict[str, Any]], customers: list[dict[str, Any]]
) -> None:
    ids = {c["id"] for c in customers}
    for inv in invoices:
        if inv["customer_id"] not in ids:
            raise VerifyError(
                f"invoice {inv['id']} references missing customer {inv['customer_id']}"
            )


def check_line_item_invoice_fk(
    line_items: list[dict[str, Any]], invoices: list[dict[str, Any]]
) -> None:
    ids = {i["id"] for i in invoices}
    for li in line_items:
        if li["invoice_id"] not in ids:
            raise VerifyError(f"line item {li['id']} references missing invoice {li['invoice_id']}")


def check_invoice_total_matches_lines(
    invoices: list[dict[str, Any]], line_items: list[dict[str, Any]]
) -> None:
    by_invoice: dict[str, int] = {}
    for li in line_items:
        # line_total = quantity_micros * unit_amount_cents / 1e6
        expected_line = (int(li["quantity_micros"]) * int(li["unit_amount_cents"])) // 1_000_000
        if expected_line != int(li["line_total_cents"]):
            raise VerifyError(
                f"line item {li['id']}: line_total_cents={li['line_total_cents']} "
                f"!= qty*unit/1e6={expected_line}"
            )
        by_invoice[cast(str, li["invoice_id"])] = by_invoice.get(
            cast(str, li["invoice_id"]), 0
        ) + int(li["line_total_cents"])
    for inv in invoices:
        expected_total = by_invoice.get(cast(str, inv["id"]), 0)
        if expected_total != int(inv["total_cents"]):
            raise VerifyError(
                f"invoice {inv['id']}: total_cents={inv['total_cents']} "
                f"!= sum(line_total_cents)={expected_total}"
            )


def check_contracts_validate(contracts: list[dict[str, Any]]) -> None:
    """Build-plan line 118: 'rejected at load time'. Verify the property holds."""
    for c in contracts:
        try:
            Contract.model_validate(c)
        except ValidationError as exc:
            raise VerifyError(f"contract {c['id']} failed Pydantic validation: {exc}") from exc


def check_transactions_account_fk(
    transactions: list[dict[str, Any]], accounts: list[dict[str, Any]]
) -> None:
    ids = {a["id"] for a in accounts}
    for t in transactions:
        if t["account_id"] not in ids:
            raise VerifyError(f"transaction {t['id']} references missing account {t['account_id']}")


def check_time_entry_fks(
    entries: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    projects: list[dict[str, Any]],
) -> None:
    cust_ids = {c["id"] for c in customers}
    proj_ids = {p["id"] for p in projects}
    for te in entries:
        if te["customer_id"] not in cust_ids:
            raise VerifyError(
                f"time_entry {te['id']} references missing customer {te['customer_id']}"
            )
        if te["project_id"] not in proj_ids:
            raise VerifyError(
                f"time_entry {te['id']} references missing project {te['project_id']}"
            )


def check_dispute_transaction_fk(
    disputes: list[dict[str, Any]], transactions: list[dict[str, Any]]
) -> None:
    ids = {t["id"] for t in transactions}
    for d in disputes:
        if d["transaction_id"] not in ids:
            raise VerifyError(
                f"dispute {d['id']} references missing transaction {d['transaction_id']}"
            )


def check_ambiguous_name_subset(customers: list[dict[str, Any]]) -> None:
    """At least one pair of customers should share a name prefix (case-insensitive first token)."""
    by_first_word: dict[str, list[str]] = {}
    for c in customers:
        name = cast(str, c["name"]).strip()
        first = name.split(" ", 1)[0].lower()
        by_first_word.setdefault(first, []).append(name)
    pairs = [(k, names) for k, names in by_first_word.items() if len(names) >= 2]
    if not pairs:
        raise VerifyError(
            "ambiguity-rich subset missing: no pair of customers shares a name prefix"
        )


def check_ground_truth_customer_refs(
    cases: list[dict[str, Any]], customers: list[dict[str, Any]], context: str
) -> None:
    """Every customer_id referenced in ground-truth labels must exist."""
    ids = {c["id"] for c in customers}
    for case in cases:
        expected = case.get("expected")
        if not isinstance(expected, dict):
            continue
        expected_dict = cast(dict[str, Any], expected)
        cid = expected_dict.get("customer_id")
        if cid is not None and cid not in ids:
            raise VerifyError(
                f"{context}: case {case.get('case_id')} references missing customer {cid}"
            )


def check_ground_truth_invoice_refs(
    cases: list[dict[str, Any]], invoices: list[dict[str, Any]], context: str
) -> None:
    inv_ids = {i["id"] for i in invoices}
    for case in cases:
        expected = case.get("expected")
        if not isinstance(expected, dict):
            continue
        expected_dict = cast(dict[str, Any], expected)
        iid = expected_dict.get("invoice_id")
        if iid is not None and iid not in inv_ids:
            raise VerifyError(
                f"{context}: case {case.get('case_id')} references missing invoice {iid}"
            )


# ---------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------


def verify_all() -> Summary:
    customers = _read_jsonl(BANK / "customers.jsonl")
    invoices = _read_jsonl(BANK / "invoices.jsonl")
    line_items = _read_jsonl(BANK / "invoice_line_items.jsonl")
    transactions = _read_jsonl(BANK / "transactions.jsonl")
    disputes = _read_jsonl(BANK / "disputes.jsonl")
    accounts = cast(list[dict[str, Any]], _read_json(BANK / "accounts.json"))

    contracts = _read_jsonl(INTERNAL / "contracts.jsonl")
    time_entries = _read_jsonl(INTERNAL / "time_tracking.jsonl")
    projects = _read_jsonl(INTERNAL / "projects.jsonl")

    checks: list[tuple[str, Callable[[], None]]] = [
        ("customer count in band", lambda: check_customer_count_in_band(customers)),
        ("invoice count in band", lambda: check_invoice_count_in_band(invoices)),
        ("contract count in band", lambda: check_contract_count_in_band(contracts)),
        ("kyc statuses valid", lambda: check_kyc_statuses(customers)),
        (
            "invoice→customer FK",
            lambda: check_invoice_customer_fk(invoices, customers),
        ),
        (
            "line item→invoice FK",
            lambda: check_line_item_invoice_fk(line_items, invoices),
        ),
        (
            "invoice total = sum(line items)",
            lambda: check_invoice_total_matches_lines(invoices, line_items),
        ),
        ("contracts validate against Pydantic schema", lambda: check_contracts_validate(contracts)),
        (
            "transactions→accounts FK",
            lambda: check_transactions_account_fk(transactions, accounts),
        ),
        (
            "time_entries→(customers, projects) FK",
            lambda: check_time_entry_fks(time_entries, customers, projects),
        ),
        (
            "disputes→transactions FK",
            lambda: check_dispute_transaction_fk(disputes, transactions),
        ),
        ("ambiguous-name subset present", lambda: check_ambiguous_name_subset(customers)),
    ]

    # Ground-truth ref checks for both splits.
    for split in ("train", "holdout"):
        for fname in (
            "invoice_resolution_labels",
            "scope_gate_labels",
            "policy_compliance_labels",
            "perturbation_stability_labels",
        ):
            path = GROUND_TRUTH / split / f"{fname}.jsonl"
            cases = _read_jsonl(path)
            ctx = f"{split}/{fname}"
            if fname == "invoice_resolution_labels":
                checks.append(
                    (
                        f"ground_truth {ctx}: customer FKs",
                        lambda cases=cases, ctx=ctx: check_ground_truth_customer_refs(
                            cases, customers, ctx
                        ),
                    )
                )
                checks.append(
                    (
                        f"ground_truth {ctx}: invoice FKs",
                        lambda cases=cases, ctx=ctx: check_ground_truth_invoice_refs(
                            cases, invoices, ctx
                        ),
                    )
                )

    for name, fn in checks:
        fn()
        print(f"  ok: {name}")

    return Summary(
        customers=len(customers),
        invoices=len(invoices),
        line_items=len(line_items),
        transactions=len(transactions),
        contracts=len(contracts),
        time_entries=len(time_entries),
        disputes=len(disputes),
    )


def _main() -> int:
    try:
        s = verify_all()
    except VerifyError as exc:
        print(f"verify: FAILED — {exc}", file=sys.stderr)
        return 1
    print(
        f"verify: OK — customers={s.customers} invoices={s.invoices} "
        f"line_items={s.line_items} transactions={s.transactions} "
        f"contracts={s.contracts} time_entries={s.time_entries} "
        f"disputes={s.disputes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
