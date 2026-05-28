"""Procedural, deterministic-from-seed Synthetic Account 1 generator.

Run as a module so config + generated paths resolve relative to the
package directory regardless of CWD::

    uv run python -m synthetic_account_1.simulate
    uv run python -m synthetic_account_1.simulate --seed 42

The whole world is derived from a single ``random.Random`` instance.
Every JSONL line is JSON-serialized with sorted keys and a fixed
separator so two runs at the same seed produce byte-identical files.

No LLM is in the loop. Memos, descriptions, contract scope sentences —
all template-driven. Counterfactual perturbations use an LLM, but
that's Stage 9, not here.
"""

import argparse
import json
import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml

from synthetic_account_1.pydantic_models import (
    Contract,
    FlatFeeSOW,
    Milestone,
    MonthlyRetainer,
    RateOverride,
    TimeAndMaterials,
)

PACKAGE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = PACKAGE_DIR / "config"
GENERATED_DIR = PACKAGE_DIR / "generated"
GROUND_TRUTH_DIR = PACKAGE_DIR / "ground_truth"

BANK_DIR = GENERATED_DIR / "bank"
INTERNAL_DIR = GENERATED_DIR / "account_internal"

JSON_KW: dict[str, Any] = {"sort_keys": True, "separators": (",", ":"), "ensure_ascii": False}


# ---------------------------------------------------------------------
# Determinism helpers
# ---------------------------------------------------------------------


def _iso(d: date | datetime) -> str:
    """Stable ISO-8601 string for date/datetime values."""
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        return d.isoformat()
    return d.isoformat()


def _to_jsonable(obj: Any) -> Any:
    """Recursive coercion of dates/datetimes for canonical JSON output."""
    if isinstance(obj, datetime | date):
        return _iso(obj)
    if isinstance(obj, dict):
        d = cast(dict[Any, Any], obj)
        return {str(k): _to_jsonable(v) for k, v in d.items()}
    if isinstance(obj, list | tuple):
        seq = cast(Sequence[Any], obj)
        return [_to_jsonable(v) for v in seq]
    return obj


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(_to_jsonable(row), **JSON_KW))
            f.write("\n")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(_to_jsonable(payload), f, **JSON_KW)
        f.write("\n")


def _weighted_choice(rng: random.Random, items: Sequence[Any], weights: Sequence[float]) -> Any:
    return rng.choices(items, weights=list(weights), k=1)[0]


# ---------------------------------------------------------------------
# Loaded config (plain dicts; YAML is the source of truth)
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    company: dict[str, Any]
    vendors: dict[str, Any]
    customers: dict[str, Any]
    rate_cards: dict[str, Any]
    contracts: dict[str, Any]
    adversarial: dict[str, Any]


def _load_config() -> Config:
    def _load(name: str) -> dict[str, Any]:
        with (CONFIG_DIR / f"{name}.yaml").open("r", encoding="utf-8") as f:
            return cast(dict[str, Any], yaml.safe_load(f))

    return Config(
        company=_load("company"),
        vendors=_load("vendors"),
        customers=_load("customers"),
        rate_cards=_load("rate_cards"),
        contracts=_load("contracts"),
        adversarial=_load("adversarial"),
    )


# ---------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------


def _generate_accounts(cfg: Config) -> list[dict[str, Any]]:
    as_of = date.fromisoformat(cast(str, cfg.company["as_of_date"]))
    opened = datetime.combine(
        as_of - timedelta(days=30 * int(cast(int, cfg.company["months_of_history"]))),
        datetime.min.time(),
        tzinfo=UTC,
    )
    rows: list[dict[str, Any]] = []
    for a in cast(list[dict[str, Any]], cfg.company["accounts"]):
        rows.append(
            {
                "id": a["id"],
                "name": a["name"],
                "type": a["type"],
                "currency": cfg.company["default_currency"],
                "balance_cents": a["initial_balance_cents"],
                "opened_at": opened,
            }
        )
    return rows


def _generate_customers(rng: random.Random, cfg: Config) -> list[dict[str, Any]]:
    target = int(cast(int, cfg.customers["target_count"]))
    cohorts = cast(list[dict[str, Any]], cfg.customers["cohorts"])
    kyc_w = cast(dict[str, float], cfg.customers["kyc_status_weights"])
    terms_opts = cast(list[int], cfg.customers["default_payment_terms_options"])
    terms_w = cast(list[float], cfg.customers["default_payment_terms_weights"])
    left = cast(list[str], cfg.customers["name_tokens_left"])
    right = cast(list[str], cfg.customers["name_tokens_right"])
    streets = cast(list[str], cfg.customers["street_names"])
    cities = cast(list[list[str]], cfg.customers["cities"])

    as_of = date.fromisoformat(cast(str, cfg.company["as_of_date"]))
    earliest = as_of - timedelta(days=30 * int(cast(int, cfg.company["months_of_history"])))

    # Pre-build a deterministic, unique pool of names by walking
    # (left, right) pairs in a shuffled but seeded order.
    pairs: list[tuple[str, str]] = [(a, b) for a in left for b in right]
    rng.shuffle(pairs)
    base_names: list[str] = [f"{a} {b}" for a, b in pairs]
    if len(base_names) < target:
        raise RuntimeError(
            f"name pool too small: have {len(base_names)} unique base names, need {target}"
        )

    # Inject similar-name pairs from adversarial.yaml at the front so
    # they always make it into the cohort.
    similar_pairs = cast(list[list[str]], cfg.adversarial["similar_name_pairs"])
    injected: list[str] = []
    for pair in similar_pairs:
        for name in pair:
            if name not in injected:
                injected.append(name)

    # Place injected names first; fill the rest from the base pool,
    # skipping any base name that collides with an injected one.
    used: set[str] = set(injected)
    names: list[str] = list(injected)
    for n in base_names:
        if len(names) >= target:
            break
        if n in used:
            continue
        names.append(n)
        used.add(n)

    if len(names) < target:
        raise RuntimeError(f"name pool exhausted: {len(names)} < {target}")
    names = names[:target]

    cohort_keys = [c["name"] for c in cohorts]
    cohort_weights = [float(c["weight"]) for c in cohorts]

    restricted_quota = int(cast(int, cfg.adversarial["restricted_customer_count"]))
    multi_contact_quota = int(cast(int, cfg.adversarial["multi_billing_contact_count"]))

    customers: list[dict[str, Any]] = []
    for i, name in enumerate(names):
        cohort = _weighted_choice(rng, cohort_keys, cohort_weights)
        if i < restricted_quota:
            kyc = "restricted"
        else:
            kyc = _weighted_choice(rng, list(kyc_w.keys()), list(kyc_w.values()))
        terms = _weighted_choice(rng, terms_opts, terms_w)
        street_no = rng.randint(100, 9999)
        street = rng.choice(streets)
        city, st, zipc = rng.choice(cities)
        # Domain: lower-snake of the first word + ".example".
        slug = name.lower().replace(" ", "-").replace(",", "").replace(".", "")
        primary = f"billing@{slug}.example"
        email = f"{primary}|ap@{slug}.example" if i < multi_contact_quota else primary
        days_offset = rng.randint(0, (as_of - earliest).days)
        created = datetime.combine(
            earliest + timedelta(days=days_offset), datetime.min.time(), tzinfo=UTC
        )
        customers.append(
            {
                "id": f"cust_{i + 1:04d}",
                "name": name,
                "email": email,
                "address": f"{street_no} {street} St, {city}, {st} {zipc}",
                "kyc_status": kyc,
                "default_payment_terms_days": terms,
                "cohort": cohort,
                "created_at": created,
            }
        )
    return customers


def _generate_rate_cards(cfg: Config) -> list[dict[str, Any]]:
    base = cast(list[dict[str, Any]], cfg.rate_cards["entries"])
    edge = cast(list[dict[str, Any]], cfg.adversarial["edge_case_rate_entries"])
    effective_from = cast(str, cfg.rate_cards["effective_from"])
    currency = cast(str, cfg.rate_cards["currency"])

    rows: list[dict[str, Any]] = []
    for i, entry in enumerate(base):
        rows.append(
            {
                "id": f"rc_base_{i + 1:03d}",
                "service": entry["service"],
                "role": entry.get("role"),
                "unit": entry["unit"],
                "list_amount_cents": entry["list_amount_cents"],
                "currency": currency,
                "effective_from": date.fromisoformat(effective_from),
                "effective_to": None,
            }
        )
    for i, entry in enumerate(edge):
        rows.append(
            {
                "id": f"rc_edge_{i + 1:03d}",
                "service": entry["service"],
                "role": entry.get("role"),
                "unit": entry["unit"],
                "list_amount_cents": entry["list_amount_cents"],
                "currency": currency,
                "effective_from": date.fromisoformat(
                    cast(str, entry.get("effective_from", effective_from))
                ),
                "effective_to": (
                    date.fromisoformat(cast(str, entry["effective_to"]))
                    if entry.get("effective_to")
                    else None
                ),
            }
        )
    return rows


def _generate_projects(rng: random.Random, customers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # ~1 project per customer for ~70% of customers; the rest have none.
    rows: list[dict[str, Any]] = []
    for c in customers:
        if rng.random() < 0.7:
            status = _weighted_choice(rng, ["active", "completed", "on_hold"], [0.65, 0.30, 0.05])
            rows.append(
                {
                    "id": f"proj_{c['id']}_001",
                    "customer_id": c["id"],
                    "name": f"{c['name']} Integration",
                    "status": status,
                }
            )
    return rows


def _generate_contracts(
    rng: random.Random,
    cfg: Config,
    customers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Contract]]:
    """Sample contracts for ~50% of customers, target 60 total.

    Returns (rows_for_jsonl, validated_contract_models). The Pydantic
    Contract instances are what verify.py asserts against; the dict
    rows are what hits JSONL/Postgres.
    """
    mix = cast(dict[str, dict[str, float]], cfg.contracts["cohort_billing_mix"])
    as_of = date.fromisoformat(cast(str, cfg.company["as_of_date"]))
    rate_range = cast(list[int], cfg.contracts["monthly_retainer_amount_range_cents"])
    flat_range = cast(list[int], cfg.contracts["flat_fee_sow_amount_range_cents"])
    flat_milestone_range = cast(list[int], cfg.contracts["flat_fee_milestone_count_range"])
    override_count_range = cast(list[int], cfg.contracts["rate_override_count_range"])
    override_discount_range = cast(list[int], cfg.contracts["rate_override_discount_pct_range"])
    cap_options = cast(list[int], cfg.contracts["monthly_hour_cap_options"])
    term_range = cast(list[int], cfg.contracts["term_months_range"])
    expired_fraction = float(cast(float, cfg.contracts["expired_fraction"]))
    source_doc_prob = float(cast(float, cfg.contracts["source_doc_ref_probability"]))
    scope_templates = cast(list[str], cfg.contracts["scope_summary_templates"])
    currency = cast(str, cfg.company["default_currency"])

    rate_cards = cast(list[dict[str, Any]], cfg.rate_cards["entries"])
    hourly_roles = [r for r in rate_cards if r["unit"] == "hour"]

    target_contracts = 60
    # Pick the first ~target_contracts customers in order — deterministic.
    eligible = [c for c in customers if c["kyc_status"] in ("verified", "pending")]
    chosen = eligible[:target_contracts]

    rows: list[dict[str, Any]] = []
    models: list[Contract] = []
    for i, customer in enumerate(chosen):
        cohort = cast(str, customer["cohort"])
        cohort_mix = mix[cohort]
        kinds = list(cohort_mix.keys())
        weights = [cohort_mix[k] for k in kinds]
        billing_kind = _weighted_choice(rng, kinds, weights)

        term_months = rng.randint(term_range[0], term_range[1])
        # Effective date drawn so some contracts are expired pre-as_of.
        is_expired = rng.random() < expired_fraction
        if is_expired:
            expires = as_of - timedelta(days=rng.randint(30, 365))
            effective = expires - timedelta(days=30 * term_months)
        else:
            effective = as_of - timedelta(days=rng.randint(0, 30 * term_months))
            expires = effective + timedelta(days=30 * term_months)

        contract_kind: str
        billing: Any
        if billing_kind == "monthly_retainer":
            contract_kind = "retainer"
            amount = rng.randint(rate_range[0], rate_range[1])
            billing = MonthlyRetainer(monthly_amount_cents=amount)
        elif billing_kind == "flat_fee_sow":
            contract_kind = "sow"
            total = rng.randint(flat_range[0], flat_range[1])
            n_ms = rng.randint(flat_milestone_range[0], flat_milestone_range[1])
            # Split total into n_ms positive parts whose sum is exactly total.
            split = sorted(rng.sample(range(1, total), n_ms - 1)) if n_ms > 1 else []
            parts: list[int] = []
            prev = 0
            for s in split:
                parts.append(s - prev)
                prev = s
            parts.append(total - prev)
            milestones = [
                Milestone(
                    name=f"Milestone {j + 1}",
                    amount_cents=part,
                    due_date=effective + timedelta(days=30 * (j + 1) * (term_months // n_ms)),
                )
                for j, part in enumerate(parts)
            ]
            billing = FlatFeeSOW(total_amount_cents=total, milestones=milestones)
        elif billing_kind == "t_and_m_with_overrides":
            contract_kind = "msa"
            n_overrides = rng.randint(override_count_range[0], override_count_range[1])
            picked_roles = rng.sample(hourly_roles, min(n_overrides, len(hourly_roles)))
            overrides: list[RateOverride] = []
            for r in picked_roles:
                pct = rng.randint(override_discount_range[0], override_discount_range[1])
                discounted = int(int(r["list_amount_cents"]) * (100 - pct) / 100)
                # Round to nearest dollar to keep numbers clean.
                discounted = (discounted // 100) * 100
                overrides.append(
                    RateOverride(
                        role=cast(str, r["role"]),
                        unit="hour",
                        amount_cents=discounted,
                    )
                )
            billing = TimeAndMaterials(
                rate_overrides=overrides,
                monthly_hour_cap=None,
                list_rates_apply=rng.random() < 0.5,
            )
        elif billing_kind == "t_and_m_with_cap":
            contract_kind = "msa"
            cap = rng.choice(cap_options)
            billing = TimeAndMaterials(
                rate_overrides=[],
                monthly_hour_cap=cap,
                list_rates_apply=True,
            )
        else:
            raise RuntimeError(f"unknown billing kind: {billing_kind}")

        scope = rng.choice(scope_templates).format(customer=customer["name"])
        source_doc_ref: str | None = None
        if rng.random() < source_doc_prob:
            source_doc_ref = f"s3://bramble-contracts/contract_{i + 1:04d}.pdf"

        contract_id = f"contract_{i + 1:04d}"
        # Flatten rate_overrides + monthly_hour_cap from T&M billing to
        # top-level, matching the Postgres schema top-level columns.
        billing_dump = billing.model_dump(mode="json")
        rate_overrides_top: list[RateOverride] = []
        monthly_hour_cap_top: int | None = None
        if isinstance(billing, TimeAndMaterials):
            rate_overrides_top = list(billing.rate_overrides)
            monthly_hour_cap_top = billing.monthly_hour_cap
        model = Contract(
            id=contract_id,
            customer_id=cast(str, customer["id"]),
            kind=cast(Any, contract_kind),
            effective_from=effective,
            expires_at=expires,
            currency=currency,
            billing_structure=billing,
            rate_overrides=rate_overrides_top,
            monthly_hour_cap=monthly_hour_cap_top,
            scope_summary=scope,
            source_doc_ref=source_doc_ref,
        )
        models.append(model)

        rate_overrides_dump = [o.model_dump(mode="json") for o in rate_overrides_top]
        monthly_hour_cap = monthly_hour_cap_top

        rows.append(
            {
                "id": contract_id,
                "customer_id": customer["id"],
                "kind": contract_kind,
                "effective_from": effective,
                "expires_at": expires,
                "currency": currency,
                "billing_structure": billing_dump,
                "rate_overrides": rate_overrides_dump,
                "monthly_hour_cap": monthly_hour_cap,
                "scope_summary": scope,
                "source_doc_ref": source_doc_ref,
            }
        )
    return rows, models


def _generate_time_entries(
    rng: random.Random,
    cfg: Config,
    customers: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Roughly 600 time entries against T&M and retainer contracts.

    Only customers with an active project AND a t_and_m contract get
    entries — that's what the rate-card×time invoices later attach to.
    """
    proj_by_customer = {p["customer_id"]: p for p in projects}
    contract_by_customer = {c["customer_id"]: c for c in contracts}
    hourly_rate_roles = [
        r for r in cast(list[dict[str, Any]], cfg.rate_cards["entries"]) if r["unit"] == "hour"
    ]
    as_of = date.fromisoformat(cast(str, cfg.company["as_of_date"]))
    earliest = as_of - timedelta(days=180)

    rows: list[dict[str, Any]] = []
    for c in customers:
        proj = proj_by_customer.get(c["id"])
        contract = contract_by_customer.get(c["id"])
        if proj is None or contract is None:
            continue
        billing = contract["billing_structure"]
        if billing["kind"] != "t_and_m":
            continue
        # Number of entries scales loosely with cohort tier.
        cohort = cast(str, c["cohort"])
        n_entries = {"enterprise": 18, "mid_market": 8, "smb": 3}.get(cohort, 5)
        for j in range(n_entries):
            role_row = rng.choice(hourly_rate_roles)
            role = cast(str, role_row["role"])
            hours = round(rng.uniform(0.5, 8.0), 2)
            hours_micros = int(hours * 1_000_000)
            day_offset = rng.randint(0, (as_of - earliest).days)
            occurred = earliest + timedelta(days=day_offset)
            rows.append(
                {
                    "id": f"te_{c['id']}_{j + 1:03d}",
                    "customer_id": c["id"],
                    "project_id": proj["id"],
                    "role": role,
                    "hours_micros": hours_micros,
                    "occurred_at": occurred,
                    "description": f"{role_row['service']} for {proj['name']}",
                    "invoiced": False,
                }
            )
    return rows


def _generate_invoices(
    rng: random.Random,
    cfg: Config,
    customers: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    rate_cards: list[dict[str, Any]],
    time_entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Produce ~280 historical invoices with line items.

    Distribution across source_type:
      - contract:        ~120 (monthly retainers, SOW milestones)
      - rate_card:       ~70  (flat rate-card services like onboarding)
      - time_tracking:   ~50  (rate_card × time_entries)
      - user_specified:  ~40  (ad-hoc one-off invoices)
    """
    as_of = date.fromisoformat(cast(str, cfg.company["as_of_date"]))
    currency = cast(str, cfg.company["default_currency"])

    invoices: list[dict[str, Any]] = []
    line_items: list[dict[str, Any]] = []
    customer_by_id = {c["id"]: c for c in customers}

    # Helper: status sampler based on issue age.
    def _status_for(issued_at: datetime, terms_days: int) -> tuple[str, datetime | None]:
        age_days = (datetime.combine(as_of, datetime.min.time(), tzinfo=UTC) - issued_at).days
        if age_days < terms_days:
            # Within the payment window.
            r = rng.random()
            if r < 0.55:
                return "sent", None
            if r < 0.75:
                paid_at = issued_at + timedelta(days=rng.randint(1, max(1, age_days)))
                return "paid", paid_at
            if r < 0.85:
                return "draft", None
            return "sent", None
        # Past due window.
        r = rng.random()
        if r < 0.80:
            paid_at = issued_at + timedelta(days=rng.randint(1, terms_days + 10))
            return "paid", paid_at
        if r < 0.93:
            return "overdue", None
        return "disputed", None

    invoice_idx = 0

    def _alloc_invoice_id() -> str:
        nonlocal invoice_idx
        invoice_idx += 1
        return f"inv_{invoice_idx:05d}"

    def _emit_invoice(
        customer: dict[str, Any],
        source_type: str,
        issued_at: datetime,
        line_specs: list[dict[str, Any]],
        contract_id: str | None,
    ) -> None:
        terms = int(customer["default_payment_terms_days"])
        due = issued_at + timedelta(days=terms)
        status, paid_at = _status_for(issued_at, terms)
        total = sum(int(s["line_total_cents"]) for s in line_specs)
        if total <= 0:
            return
        inv_id = _alloc_invoice_id()
        dispute_flag = status == "disputed"
        invoices.append(
            {
                "id": inv_id,
                "customer_id": customer["id"],
                "issued_at": issued_at,
                "due_at": due,
                "total_cents": total,
                "currency": currency,
                "status": status,
                "payment_received_at": paid_at,
                "source_type": source_type,
                "contract_id": contract_id,
                "dispute_flag": dispute_flag,
            }
        )
        for j, s in enumerate(line_specs):
            line_items.append(
                {
                    "id": f"{inv_id}_li_{j + 1:02d}",
                    "invoice_id": inv_id,
                    "line_no": j + 1,
                    "description": s["description"],
                    "quantity_micros": int(s["quantity_micros"]),
                    "unit_amount_cents": int(s["unit_amount_cents"]),
                    "line_total_cents": int(s["line_total_cents"]),
                    "source_type": source_type,
                    "source_refs": s["source_refs"],
                    "computation": s["computation"],
                }
            )

    def _as_date(value: Any) -> date:
        return value if isinstance(value, date) else date.fromisoformat(cast(str, value))

    # --- Contract-derived invoices ---------------------------------
    for contract in contracts:
        customer = customer_by_id[contract["customer_id"]]
        billing = contract["billing_structure"]
        eff = _as_date(contract["effective_from"])
        # End-of-bill window: min(today, contract expiry).
        exp_raw = contract.get("expires_at")
        exp = _as_date(exp_raw) if exp_raw else as_of
        end = min(exp, as_of)
        if billing["kind"] == "monthly_retainer":
            # One invoice per elapsed month, capped at 4 per contract so
            # the total invoice count lands near the ~280 target in
            # build-plan §Synthetic Account 1.
            months = max(1, min(4, (end.year - eff.year) * 12 + (end.month - eff.month)))
            for m in range(months):
                issued = datetime.combine(
                    eff + timedelta(days=30 * m), datetime.min.time(), tzinfo=UTC
                )
                if issued.date() > end:
                    break
                amount = int(billing["monthly_amount_cents"])
                _emit_invoice(
                    customer,
                    "contract",
                    issued,
                    [
                        {
                            "description": f"Monthly retainer ({issued.strftime('%b %Y')})",
                            "quantity_micros": 1_000_000,
                            "unit_amount_cents": amount,
                            "line_total_cents": amount,
                            "source_refs": {"contract_id": contract["id"]},
                            "computation": (f"monthly_retainer.monthly_amount_cents={amount}"),
                        }
                    ],
                    contract["id"],
                )
        elif billing["kind"] == "flat_fee_sow":
            for j, ms in enumerate(cast(list[dict[str, Any]], billing["milestones"])):
                due_date = _as_date(ms["due_date"])
                if due_date > end:
                    continue
                issued = datetime.combine(due_date, datetime.min.time(), tzinfo=UTC)
                amount = int(ms["amount_cents"])
                _emit_invoice(
                    customer,
                    "contract",
                    issued,
                    [
                        {
                            "description": f"SOW milestone: {ms['name']}",
                            "quantity_micros": 1_000_000,
                            "unit_amount_cents": amount,
                            "line_total_cents": amount,
                            "source_refs": {
                                "contract_id": contract["id"],
                                "milestone_index": j,
                            },
                            "computation": (f"flat_fee_sow.milestones[{j}].amount_cents={amount}"),
                        }
                    ],
                    contract["id"],
                )
        # T&M contracts produce time-tracking invoices instead.

    # --- Time-tracking invoices (rate_card × hours) ------------------
    # Group time entries by (customer_id, project_id, role, year_month)
    # and create one invoice per group.
    rate_lookup_by_role: dict[str, int] = {}
    for r in rate_cards:
        if r["unit"] == "hour" and r["role"] is not None:
            rate_lookup_by_role[cast(str, r["role"])] = int(r["list_amount_cents"])

    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for te in time_entries:
        raw = te["occurred_at"]
        occurred = raw if isinstance(raw, date) else date.fromisoformat(cast(str, raw))
        key = (
            cast(str, te["customer_id"]),
            cast(str, te["project_id"]),
            cast(str, te["role"]),
            f"{occurred.year:04d}-{occurred.month:02d}",
        )
        groups.setdefault(key, []).append(te)

    contract_by_customer = {c["customer_id"]: c for c in contracts}
    sorted_group_keys = sorted(groups.keys())
    # Cap at ~50 groups → invoices.
    for key in sorted_group_keys[:50]:
        cust_id, _proj_id, role, ym = key
        customer = customer_by_id[cust_id]
        contract = contract_by_customer.get(cust_id)
        # Effective rate: contract override if present, else list rate.
        unit_rate = rate_lookup_by_role.get(role, 20_000)
        override_refs: dict[str, Any] = {}
        if contract is not None:
            billing = contract["billing_structure"]
            if billing["kind"] == "t_and_m":
                for ov in cast(list[dict[str, Any]], billing.get("rate_overrides", [])):
                    if ov["role"] == role and ov["unit"] == "hour":
                        unit_rate = int(ov["amount_cents"])
                        override_refs = {
                            "contract_id": contract["id"],
                            "rate_override_role": role,
                        }
                        break
        entries = groups[key]
        total_micros = sum(int(e["hours_micros"]) for e in entries)
        line_total = (total_micros * unit_rate) // 1_000_000
        # Issue date = first of the month after the time entries.
        y, m = (int(p) for p in ym.split("-"))
        issue_date = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        if issue_date > as_of:
            continue
        issued_dt = datetime.combine(issue_date, datetime.min.time(), tzinfo=UTC)
        source_refs: dict[str, Any] = {
            "time_entry_ids": sorted(cast(str, e["id"]) for e in entries),
            "rate_card_role": role,
        }
        source_refs.update(override_refs)
        _emit_invoice(
            customer,
            "time_tracking",
            issued_dt,
            [
                {
                    "description": f"{role} services ({ym})",
                    "quantity_micros": total_micros,
                    "unit_amount_cents": unit_rate,
                    "line_total_cents": line_total,
                    "source_refs": source_refs,
                    "computation": (
                        f"sum(hours_micros)={total_micros} * unit_amount_cents="
                        f"{unit_rate} / 1e6 = {line_total}"
                    ),
                }
            ],
            contract["id"] if contract is not None else None,
        )

    # --- Rate-card flat-fee invoices ---------------------------------
    flat_entries = [r for r in rate_cards if r["unit"] == "flat" and r["id"].startswith("rc_base_")]
    if flat_entries:
        # ~70 invoices distributed roughly across all customers.
        flat_count = 70
        for k in range(flat_count):
            customer = customers[k % len(customers)]
            entry = flat_entries[k % len(flat_entries)]
            issued = datetime.combine(
                as_of - timedelta(days=rng.randint(1, 540)),
                datetime.min.time(),
                tzinfo=UTC,
            )
            amount = int(entry["list_amount_cents"])
            _emit_invoice(
                customer,
                "rate_card",
                issued,
                [
                    {
                        "description": cast(str, entry["service"]),
                        "quantity_micros": 1_000_000,
                        "unit_amount_cents": amount,
                        "line_total_cents": amount,
                        "source_refs": {"rate_card_id": entry["id"]},
                        "computation": (f"rate_card[{entry['id']}].list_amount_cents={amount}"),
                    }
                ],
                None,
            )

    # --- User-specified invoices -------------------------------------
    user_count = 40
    descriptions = [
        "Consulting services (custom scope)",
        "Ad-hoc engineering work",
        "Discovery workshop",
        "Out-of-scope feature work",
        "Special-project facilitation",
    ]
    for k in range(user_count):
        customer = customers[(k * 3 + 5) % len(customers)]
        issued = datetime.combine(
            as_of - timedelta(days=rng.randint(1, 540)),
            datetime.min.time(),
            tzinfo=UTC,
        )
        amount = rng.randint(50_000, 1_500_000)
        desc = descriptions[k % len(descriptions)]
        _emit_invoice(
            customer,
            "user_specified",
            issued,
            [
                {
                    "description": desc,
                    "quantity_micros": 1_000_000,
                    "unit_amount_cents": amount,
                    "line_total_cents": amount,
                    "source_refs": {"specified_by_user": True},
                    "computation": "user_specified",
                }
            ],
            None,
        )

    return invoices, line_items


def _generate_transactions(
    rng: random.Random,
    cfg: Config,
    invoices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Vendor expenses + payroll + customer-payment credits."""
    as_of = date.fromisoformat(cast(str, cfg.company["as_of_date"]))
    months = int(cast(int, cfg.company["months_of_history"]))
    earliest = as_of - timedelta(days=30 * months)

    rows: list[dict[str, Any]] = []
    tx_idx = 0

    def _alloc_id() -> str:
        nonlocal tx_idx
        tx_idx += 1
        return f"txn_{tx_idx:06d}"

    # Vendor expenses.
    for vendor in cast(list[dict[str, Any]], cfg.vendors["vendors"]):
        cadence = cast(str, vendor["cadence"])
        amount_lo, amount_hi = cast(list[int], vendor["amount_range_cents"])
        account = cast(str, vendor["account"])
        category = cast(str, vendor["category"])
        d = earliest
        while d <= as_of:
            amount = rng.randint(amount_lo, amount_hi)
            posted = datetime.combine(d, datetime.min.time(), tzinfo=UTC)
            rows.append(
                {
                    "id": _alloc_id(),
                    "account_id": account,
                    "amount_cents": amount,
                    "direction": "debit",
                    "counterparty": vendor["name"],
                    "memo": f"{vendor['name']} — {d.strftime('%b %Y')}",
                    "category": category,
                    "posted_at": posted,
                    "related_invoice_id": None,
                }
            )
            if cadence == "semi_monthly":
                d = d + timedelta(days=15)
            elif cadence == "monthly":
                d = d + timedelta(days=30)
            elif cadence == "weekly":
                d = d + timedelta(days=7)
            else:
                raise RuntimeError(f"unknown cadence: {cadence}")

    # Customer payment credits for paid invoices.
    for inv in invoices:
        if inv["status"] == "paid" and inv["payment_received_at"] is not None:
            posted = cast(datetime, inv["payment_received_at"])
            rows.append(
                {
                    "id": _alloc_id(),
                    "account_id": "acct_operating_001",
                    "amount_cents": int(inv["total_cents"]),
                    "direction": "credit",
                    "counterparty": "ACH from customer",
                    "memo": f"Payment for {inv['id']}",
                    "category": "customer_payment",
                    "posted_at": posted,
                    "related_invoice_id": inv["id"],
                }
            )
    return rows


def _generate_disputes(
    invoices: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stage 2 leaves disputes sparse — 0–10 rows. Stage 14 (v0.2) ships the full ~40."""
    # Pick the first ~5 disputed invoices and their payment-credit txns,
    # if any. Many disputed invoices will have NO payment credit (since
    # we mark `disputed` instead of `paid`); for those we attach the
    # dispute to the most recent customer-payment txn for that customer,
    # falling back to skipping the row if none exists.
    rows: list[dict[str, Any]] = []
    disputed = [i for i in invoices if i["dispute_flag"]][:5]
    for j, inv in enumerate(disputed):
        # Pick any txn for that customer's invoice → use the related
        # payment txn if it exists, else the first vendor txn (so the
        # FK is satisfied; full corpus is Stage 14).
        related = [t for t in transactions if t.get("related_invoice_id") == inv["id"]]
        if not related:
            # Fallback: any operating-account txn.
            related = [t for t in transactions if t["account_id"] == "acct_operating_001"][:1]
        if not related:
            continue
        tx = related[0]
        rows.append(
            {
                "id": f"dispute_{j + 1:04d}",
                "transaction_id": tx["id"],
                "opened_at": cast(datetime, inv["issued_at"]),
                "kind": "merchant_dispute",
                "status": "open",
                "resolution_outcome": None,
            }
        )
    return rows


# ---------------------------------------------------------------------
# Ground-truth labels (placeholder shape; full eval scoring is Stage 7).
# ---------------------------------------------------------------------


def _generate_ground_truth(
    rng: random.Random,
    customers: list[dict[str, Any]],
    invoices: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    rate_cards: list[dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Produce v0.1 ground-truth label JSONLs.

    Per build-plan §Eval Framework §0: ~120 send-invoice cases total
    (~85 train / ~35 holdout). We seed-split on the case so all
    perturbation variants of a single case end up in the same split.

    The label format is intentionally simple at Stage 2: a case_id, the
    expected customer/contract/source_type/total_cents, and a list of
    expected fired rule IDs (deferred — Stage 7 may extend). Stage 11
    iterates on these against the train split only.
    """
    invoices_by_customer: dict[str, list[dict[str, Any]]] = {}
    for inv in invoices:
        invoices_by_customer.setdefault(cast(str, inv["customer_id"]), []).append(inv)

    # --- Invoice resolution labels ---------------------------------
    # Pick 120 cases, one per (customer, source_type) combination where
    # an invoice exists. Deterministic by sorting then capping.
    invoice_pool = sorted(
        invoices,
        key=lambda i: (
            cast(str, i["customer_id"]),
            cast(str, i["source_type"]),
            cast(str, i["id"]),
        ),
    )
    invoice_cases: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for inv in invoice_pool:
        key = (cast(str, inv["customer_id"]), cast(str, inv["source_type"]))
        if key in seen:
            continue
        seen.add(key)
        customer = next(c for c in customers if c["id"] == inv["customer_id"])
        contract_id = inv.get("contract_id")
        case = {
            "case_id": f"ir_{len(invoice_cases) + 1:04d}",
            "request": (f"Send invoice for {customer['name']} — source: {inv['source_type']}"),
            "expected_outcome": "sent",
            "expected_decline_reason": None,
            "expected": {
                "customer_id": inv["customer_id"],
                "source_type": inv["source_type"],
                "total_cents": inv["total_cents"],
                "contract_id": contract_id,
                "currency": inv["currency"],
            },
        }
        invoice_cases.append(case)
        if len(invoice_cases) >= 120:
            break

    # --- Decline cases (Stage 7) ---------------------------------------
    # Clone every 5th sent case as a decline. Deterministic; non-overlapping
    # with the four-amount-source headline slice because we pick from after
    # case 30 (the headline slice is the first 30 by source-type ordering).
    decline_reasons = (
        "amount_too_high_for_approver",
        "customer_on_hold",
        "requested_clarification",
    )
    decline_seeds = [c for i, c in enumerate(invoice_cases) if i >= 30 and i % 5 == 0]
    decline_seeds = decline_seeds[:24]  # 14 train + 10 holdout
    for j, seed in enumerate(decline_seeds):
        invoice_cases.append(
            {
                "case_id": f"ir_d_{j + 1:04d}",
                "request": seed["request"],
                "expected_outcome": "declined",
                "expected_decline_reason": decline_reasons[j % len(decline_reasons)],
                # kept for forward-compat; ignored at score time
                "expected": dict(cast(dict[str, Any], seed["expected"])),
            }
        )

    # --- Policy-rejected cases (Stage 7) -------------------------------
    # One sub-case per Billing-integrity primitive so policy_compliance
    # mechanically validates each rule rejects.
    policy_reject_specs = [
        (
            "require_amount_source",
            "Send invoice for Acme Corp without specifying a source",
        ),
        (
            "contract_consistency_check",
            "Send invoice for Acme Corp for $99999 cited to contract_NONEXISTENT",
        ),
        (
            "prohibit_exceed_contract_cap",
            "Bill Stark Industries $1,000,000 against their $100k cap contract",
        ),
        (
            "currency_consistency_check",
            "Send invoice for Acme Corp in EUR while their contract is USD",
        ),
    ]
    # 10 train + 6 holdout = 16 total. Cycle through the 4 specs 4× = 16 cases.
    for k in range(16):
        rule_id, request = policy_reject_specs[k % len(policy_reject_specs)]
        invoice_cases.append(
            {
                "case_id": f"ir_pr_{k + 1:04d}",
                "request": request,
                "expected_outcome": "policy_rejected",
                "expected_decline_reason": None,
                # consumed by the policy_compliance block below
                "expected_fired_rule": rule_id,
                "expected": {},  # ignored
            }
        )

    # --- Scope-gate labels -----------------------------------------
    in_scope = [
        {
            "case_id": f"sg_{i + 1:04d}",
            "input": f"Please send an invoice to {customers[i % len(customers)]['name']} for $X.",
            "expected_classification": "send_invoice",
        }
        for i in range(60)
    ]
    out_of_scope_inputs = [
        "Can you transfer $5,000 from operating to payroll?",
        "What's the weather in San Francisco?",
        "Refund the last payment we got from Acme.",
        "Open a new credit card account.",
        "Send a wire to Wells Fargo for $25k.",
        "Cancel the subscription with Datadog.",
        "Add a new employee to payroll.",
        "Update the company address on file.",
    ]
    out_of_scope = [
        {
            "case_id": f"sg_{60 + i + 1:04d}",
            "input": out_of_scope_inputs[i % len(out_of_scope_inputs)],
            "expected_classification": "out_of_scope",
        }
        for i in range(60)
    ]
    scope_cases = in_scope + out_of_scope

    # --- Policy compliance labels ----------------------------------
    # ``expected_fired_rules`` lists rules whose predicate is expected
    # to return a Violation (the engine emits event_kind='rule_fired').
    # A rule whose predicate returns None emits 'rule_skipped' and is
    # NOT in this set. Sent / declined cases pass cleanly so the set is
    # empty; policy_rejected cases name the specific Billing-integrity
    # primitive expected to trip.
    policy_cases: list[dict[str, Any]] = []
    for case in invoice_cases:
        outcome = case.get("expected_outcome")
        if outcome == "policy_rejected":
            expected_rules = [cast(str, case["expected_fired_rule"])]
        else:
            expected_rules = []
        policy_cases.append(
            {
                "case_id": f"pc_{case['case_id']}",
                "invoice_case_id": case["case_id"],
                "expected_fired_rules": expected_rules,
            }
        )

    # --- Perturbation stability labels -----------------------------
    # The actual perturbations are Stage 9; here we just declare which
    # invoice cases are perturbation seeds and the invariance class.
    perturbation_cases: list[dict[str, Any]] = []
    for i, case in enumerate(invoice_cases[:60]):
        perturbation_cases.append(
            {
                "case_id": f"pt_{case['case_id']}",
                "seed_case_id": case["case_id"],
                "perturbation_classes": [
                    "whitespace_case_format",
                    "amount_format",
                    "memo_paraphrase",
                ],
                "expected_invariance": "invariant",
            }
        )
        # Every 5th case also gets a semantic-flip variant case.
        if i % 5 == 0:
            perturbation_cases.append(
                {
                    "case_id": f"pt_{case['case_id']}_flip",
                    "seed_case_id": case["case_id"],
                    "perturbation_classes": ["contract_semantic_flip"],
                    "expected_invariance": "variant",
                }
            )

    def _split(
        cases: list[dict[str, Any]],
        holdout_pct: float,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        # Seed-level split: take a deterministic permutation, slice.
        # We DO NOT use rng here — that would tie split to the global
        # generator order. We use a separately-seeded Random so adding
        # a new corpus type doesn't reshuffle the others.
        local = random.Random(0xC0FFEE)
        order = list(range(len(cases)))
        local.shuffle(order)
        n_holdout = int(len(cases) * holdout_pct)
        holdout_ids = set(order[:n_holdout])
        train: list[dict[str, Any]] = []
        holdout: list[dict[str, Any]] = []
        for i, c in enumerate(cases):
            (holdout if i in holdout_ids else train).append(c)
        return train, holdout

    # Suppress unused-argument warning while keeping API symmetric.
    _ = rng, rate_cards

    train: dict[str, list[dict[str, Any]]] = {}
    holdout: dict[str, list[dict[str, Any]]] = {}
    for name, cases in [
        ("invoice_resolution_labels", invoice_cases),
        ("scope_gate_labels", scope_cases),
        ("policy_compliance_labels", policy_cases),
        ("perturbation_stability_labels", perturbation_cases),
    ]:
        t, h = _split(cases, holdout_pct=0.30)
        train[name] = t
        holdout[name] = h
    return {"train": train, "holdout": holdout}


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------


def simulate(seed: int | None = None) -> None:
    """Generate all JSONL artifacts under ``synthetic_account_1/generated/``."""
    cfg = _load_config()
    effective_seed = seed if seed is not None else int(cast(int, cfg.company["default_seed"]))
    rng = random.Random(effective_seed)

    # Avoid stale rows from a previous run colliding with new ones.
    for d in (BANK_DIR, INTERNAL_DIR):
        if d.exists():
            for f in sorted(d.iterdir()):
                if f.is_file():
                    f.unlink()
    for split in ("train", "holdout"):
        d = GROUND_TRUTH_DIR / split
        if d.exists():
            for f in sorted(d.iterdir()):
                if f.is_file():
                    f.unlink()

    accounts = _generate_accounts(cfg)
    customers = _generate_customers(rng, cfg)
    rate_cards = _generate_rate_cards(cfg)
    projects = _generate_projects(rng, customers)
    contracts, _models = _generate_contracts(rng, cfg, customers)
    time_entries = _generate_time_entries(rng, cfg, customers, projects, contracts)
    invoices, line_items = _generate_invoices(
        rng, cfg, customers, contracts, rate_cards, time_entries
    )
    transactions = _generate_transactions(rng, cfg, invoices)
    disputes = _generate_disputes(invoices, transactions)
    ground_truth = _generate_ground_truth(rng, customers, invoices, contracts, rate_cards)

    # --- bank/ ---------------------------------------------------
    _write_json(BANK_DIR / "accounts.json", accounts)
    _write_jsonl(BANK_DIR / "customers.jsonl", customers)
    _write_jsonl(BANK_DIR / "transactions.jsonl", transactions)
    _write_jsonl(BANK_DIR / "invoices.jsonl", invoices)
    _write_jsonl(BANK_DIR / "invoice_line_items.jsonl", line_items)
    _write_jsonl(BANK_DIR / "disputes.jsonl", disputes)

    # --- account_internal/ ----------------------------------------
    _write_jsonl(INTERNAL_DIR / "projects.jsonl", projects)
    _write_jsonl(INTERNAL_DIR / "contracts.jsonl", contracts)
    _write_jsonl(INTERNAL_DIR / "time_tracking.jsonl", time_entries)
    _write_jsonl(INTERNAL_DIR / "rate_card_lookup.jsonl", rate_cards)

    # --- ground_truth/ -------------------------------------------
    for split, files in ground_truth.items():
        for name, rows in files.items():
            _write_jsonl(GROUND_TRUTH_DIR / split / f"{name}.jsonl", rows)

    # Use math.fsum to keep the print summary stable.
    _ = math.fsum  # silence unused-import warning if reorganized later
    print(
        f"simulate: seed={effective_seed} "
        f"customers={len(customers)} invoices={len(invoices)} "
        f"line_items={len(line_items)} transactions={len(transactions)} "
        f"contracts={len(contracts)} time_entries={len(time_entries)} "
        f"disputes={len(disputes)}"
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synthetic_account_1.simulate")
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Override the seed defined in config/company.yaml.",
    )
    args = parser.parse_args(argv)
    simulate(seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
