# Stage 5 — Policy Engine + Primitive Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `compass.policy` (engine, primitives, hashing, sink, agent binding) and wire it into `SendInvoiceWorkflow` so the policy review gate exercises passing, blocking, and escalating proposals end-to-end.

**Architecture:** A small Python policy library (`compass/policy/`) — Rule/Phase/Severity/Decision/Violation/Predicate types, an async `evaluate(rules, phase, context, sink=...)` engine, a `@primitive` registry, a Sink protocol (with AuditLogSink for production, InMemorySink for tests), `hash_rules` + `write_policy_snapshot` for audit reconstructability. `policies/send_invoice.py` exports a 12-rule `RULES: list[Rule]` driven by 8 framework-core primitives plus 4 app-specific Billing integrity primitives. The existing `evaluate_policy` Temporal activity is rewritten to switch on phase, call `evaluate()`, write `policy_snapshots`, and translate exceptions into Temporal retry semantics; the workflow body builds a policy context from `Runner.run`'s `RunResult` and calls `evaluate_policy` once at `pre_action_proposal` and again at `pre_execute`.

**Tech Stack:** Python 3.12, dataclasses, `psycopg[binary]==3.3.4`, `temporalio==1.27.2`, `openai-agents==0.17.4`, `pydantic==2.13.4`. Tests via `pytest==9.0.3` + `pytest-asyncio==1.4.0`. Existing infra: local Postgres (`compass_test` database), `WorkflowEnvironment.start_time_skipping` + `AgentEnvironment` + `TestModel` for workflow tests.

**Spec:** `docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md` — read it before starting. It pins context schemas, error taxonomy, naming, and the test matrix.

---

## File map

**Created in `compass/`:**
- `compass/__init__.py` — empty package marker
- `compass/policy/__init__.py` — public API re-exports
- `compass/policy/types.py` — `Phase`, `Severity`, `Violation`, `Decision`, `Predicate`, `Rule`
- `compass/policy/errors.py` — `PolicyDecisionError`, `PolicyEngineError`, `PolicyInfraError` (positive `retryable` flag)
- `compass/policy/paths.py` — `resolve_dotted(ctx, path)` with `[*]` wildcard support; `MISSING` sentinel
- `compass/policy/registry.py` — `@primitive` decorator, `list_primitives()`, `_freeze()`
- `compass/policy/sink.py` — `Sink` protocol, `InMemorySink`, `NullSink`, `MultiSink`, `register_sink`, `clear_sinks`
- `compass/policy/engine.py` — `evaluate(...)` + phase wrappers
- `compass/policy/hashing.py` — `canonicalize_rule(rule)`, `hash_rules(rules)`
- `compass/policy/snapshot.py` — `write_policy_snapshot(conn, workflow, rules)`
- `compass/policy/audit_sink.py` — `AuditLogSink(conn, workflow_run_id, allocator, policy_hash)`, `SequenceAllocator`
- `compass/policy/agent.py` — `attach_to_agent(agent, rules, *, sink_factory=None)`
- `compass/policy/primitives/__init__.py` — empty
- `compass/policy/primitives/value.py` — `numeric_threshold`
- `compass/policy/primitives/identity.py` — `entity_status_equals`
- `compass/policy/primitives/resolution.py` — `require_existing_entity`
- `compass/policy/primitives/evidence.py` — `require_evidence_citation`
- `compass/policy/primitives/approval.py` — `prohibit_silent_modification_after_confirmation`, `prohibit_policy_drift_after_confirmation`
- `compass/policy/primitives/audit.py` — `log_policy_version`, `log_data_sources_consulted`

**Created in project code:**
- `policies/__init__.py` — empty
- `policies/send_invoice.py` — `RULES: list[Rule]`
- `workflows/send_invoice/primitives.py` — `require_amount_source`, `contract_consistency_check`, `prohibit_exceed_contract_cap`, `currency_consistency_check`
- `workflows/send_invoice/context.py` — `extract_tool_calls`, `project_resolved_entities`, `extract_reasoning_text`, `hash_proposal`

**Modified in project code:**
- `workflows/send_invoice/types.py` — `EvaluatePolicyInput` grows `phase` + `context`; `PolicyDecision` renamed to `PolicyDecisionPayload` with `policy_hash`, `escalations`, `next_sequence_no`
- `workflows/send_invoice/activities.py` — `evaluate_policy` switches on phase, opens DB, writes snapshot, runs `evaluate`, maps exceptions; `audit_log` gains `is_terminal_event` kwarg that runs `evaluate_audit_validation`
- `workflows/send_invoice/workflow.py` — after `Runner.run` builds policy context; computes proposal hash; threads `policy_hash` through pre_execute; final audit_log call sets `is_terminal_event=True`
- `workflows/send_invoice/sandbox.py` — add `compass` to passthrough modules list

**Created tests:**
- `tests/compass/__init__.py`
- `tests/compass/policy/__init__.py`
- `tests/compass/policy/conftest.py`
- `tests/compass/policy/test_paths.py`
- `tests/compass/policy/test_registry.py`
- `tests/compass/policy/test_sink.py`
- `tests/compass/policy/test_engine.py`
- `tests/compass/policy/test_hash.py`
- `tests/compass/policy/test_rule_validation.py`
- `tests/compass/policy/primitives/__init__.py`
- `tests/compass/policy/primitives/test_value.py`
- `tests/compass/policy/primitives/test_identity.py`
- `tests/compass/policy/primitives/test_resolution.py`
- `tests/compass/policy/primitives/test_evidence.py`
- `tests/compass/policy/primitives/test_approval.py`
- `tests/compass/policy/primitives/test_audit.py`
- `tests/policies/__init__.py`
- `tests/policies/conftest.py`
- `tests/policies/test_send_invoice_rules.py`
- `tests/workflows/send_invoice/test_context.py`
- `tests/workflows/send_invoice/test_workflow_policy.py`

---

## Task 1: Foundation — compass package skeleton, types, errors

**Files:**
- Create: `compass/__init__.py`
- Create: `compass/policy/__init__.py`
- Create: `compass/policy/types.py`
- Create: `compass/policy/errors.py`
- Create: `tests/compass/__init__.py`
- Create: `tests/compass/policy/__init__.py`
- Create: `tests/compass/policy/conftest.py`
- Create: `tests/compass/policy/test_rule_validation.py`

- [ ] **Step 1: Create `compass/__init__.py`** (empty file)

```python
```

- [ ] **Step 2: Create `compass/policy/types.py`**

```python
"""Public types for the Compass policy engine.

Every type here is part of the public API and re-exported from
``compass.policy``. Renaming or removing one is a breaking change.

See docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md
§Types for design rationale (why Predicate is a dataclass not a bare
callable, why ESCALATE is rejected at OpenAI Agents SDK-bound phases).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Phase(StrEnum):
    """Where in the workflow lifecycle a rule fires.

    StrEnum so equality to ``audit_log.phase`` (TEXT column) is direct.
    """

    input_validation = "input_validation"
    output_validation = "output_validation"
    pre_action_proposal = "pre_action_proposal"
    pre_execute = "pre_execute"
    audit_validation = "audit_validation"


class Severity(StrEnum):
    """What happens when a rule fires.

    BLOCK short-circuits the workflow to audit-and-reject; ESCALATE
    routes to human review with the violation surfaced. ESCALATE is only
    realizable at workflow-level phases — OpenAI Agents SDK guardrails
    are tripwire-or-nothing by contract.
    """

    BLOCK = "block"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class Violation:
    """A predicate's report that its rule fired.

    Predicates construct this with ``rule_id=""``; the engine fills the
    real ``rule_id`` from the surrounding Rule. ``evidence`` is rule-
    specific structured data that lands in ``audit_log.payload`` — keep
    it small, keep it JSON-serializable, name keys so a future reader
    knows what they mean without source code.
    """

    rule_id: str
    message: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class Decision:
    """The engine's verdict for one ``(phase, context)`` evaluation.

    ``permit=False`` only when at least one BLOCK rule fired.
    Escalations route to human review but do not flip ``permit`` — the
    workflow already gates on a human signal, so an escalation surfaces
    in the approval UI with the violation visible.
    """

    permit: bool
    violations: tuple[Violation, ...]
    escalations: tuple[Violation, ...]
    rule_ids_fired: tuple[str, ...]


PredicateFn = Callable[
    [Mapping[str, Any]],
    Awaitable[Violation | None] | (Violation | None),
]


@dataclass(frozen=True)
class Predicate:
    """The check a Rule actually runs. Returned by a primitive factory.

    Constructed by primitive factories — not directly. The factory
    decorated with ``@primitive("foo")`` returns a Predicate whose
    ``primitive_name="foo"`` and ``params={...kwargs passed to factory}``
    so the rule's identity and configuration are introspectable for
    hashing, coverage, and audit reconstruction.

    Sync and async predicate bodies are both supported; the wrapper
    awaits as needed. Use async when the body calls ``Runner.run`` for
    an LLM-judge — Temporal's OpenAIAgentsPlugin wraps those calls as
    activities, which is what keeps the eval boundary replay-safe.
    """

    primitive_name: str
    params: Mapping[str, Any]
    fn: PredicateFn

    async def __call__(self, ctx: Mapping[str, Any]) -> Violation | None:
        result = self.fn(ctx)
        if inspect.isawaitable(result):
            return await result
        return result


@dataclass(frozen=True)
class Rule:
    """One constraint inside a policy.

    The ``id`` is referenced by ``audit_log.rule_id``, by trace
    assertions in the eval framework, and by the coverage report — it
    is the stable handle on this rule across audit retention windows
    (7+ years for banking). Renaming an id in use breaks historic
    queries; treat ids as append-only.

    The ``phase`` implicitly determines what's in the context dict the
    predicate receives — see spec §Context schemas.

    ``regulatory_basis`` is denormalized into every ``rule_fired``
    event's ``payload`` so 5-year-old audit rows are interpretable
    without joining back to source.

    ``must_be_covered=True`` flags the rule for the Stage-10 CI gate
    that fails the build if the holdout corpus doesn't exercise it.

    ``surface_to_user=True`` lets the approval UI display the violation
    message; set False for internal-only rules (none ship at Stage 5).
    """

    id: str
    phase: Phase
    predicate: Predicate
    severity: Severity = Severity.BLOCK
    surface_to_user: bool = True
    regulatory_basis: tuple[str, ...] = ()
    must_be_covered: bool = False
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.severity is Severity.ESCALATE and self.phase in {
            Phase.input_validation,
            Phase.output_validation,
        }:
            raise ValueError(
                f"Rule {self.id!r}: ESCALATE is not realizable at phase "
                f"{self.phase.value} — OpenAI Agents SDK guardrails are "
                "tripwire-only. Use BLOCK, or move the rule to a "
                "workflow-level phase (pre_action_proposal, pre_execute, "
                "audit_validation)."
            )
```

- [ ] **Step 3: Create `compass/policy/errors.py`**

```python
"""Compass policy exception taxonomy.

Three exception types with different retry semantics. The ``retryable``
attribute is positive ("True means retry"); the one place we negate it
to Temporal's ``non_retryable=`` is the activity boundary in
``workflows/send_invoice/activities.py``.

See spec §Errors for the full taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from compass.policy.types import Decision


@dataclass
class PolicyDecisionError(Exception):
    """A rule decided to block or escalate.

    Deterministic — retrying the same predicate against the same context
    yields the same answer. Never retryable.
    """

    decision: Decision
    retryable: ClassVar[bool] = False

    def __str__(self) -> str:
        rule_ids = ", ".join(self.decision.rule_ids_fired)
        return f"policy blocked: rule_ids_fired=[{rule_ids}]"


@dataclass
class PolicyEngineError(Exception):
    """The engine itself failed to evaluate.

    Causes: predicate raised, primitive not registered, malformed
    context. Transient causes are plausible (an LLM-judge sub-agent
    timing out, transient registry contention) so this is retryable.
    """

    rule_id: str | None
    cause: BaseException | None
    retryable: ClassVar[bool] = True

    def __str__(self) -> str:
        head = f"engine error in rule {self.rule_id!r}" if self.rule_id else "engine error"
        return f"{head}: {self.cause}" if self.cause else head


@dataclass
class PolicyInfraError(Exception):
    """A pre-loop fact-loading activity or snapshot write failed.

    Postgres outage, network blip, etc. Retryable, but typed separately
    from PolicyEngineError so on-call can tell "policy decided no" from
    "database is down".
    """

    cause: BaseException
    retryable: ClassVar[bool] = True

    def __str__(self) -> str:
        return f"policy infra error: {self.cause}"
```

- [ ] **Step 4: Create `compass/policy/__init__.py`** with re-exports

```python
"""Compass policy engine — public API.

See docs/build-plan.md §Policy Engine + Primitive Library and
docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md.
"""

from compass.policy.errors import (
    PolicyDecisionError,
    PolicyEngineError,
    PolicyInfraError,
)
from compass.policy.types import (
    Decision,
    Phase,
    Predicate,
    Rule,
    Severity,
    Violation,
)

__all__ = [
    "Decision",
    "Phase",
    "PolicyDecisionError",
    "PolicyEngineError",
    "PolicyInfraError",
    "Predicate",
    "Rule",
    "Severity",
    "Violation",
]
```

- [ ] **Step 5: Create `tests/compass/__init__.py` and `tests/compass/policy/__init__.py`** (both empty)

```python
```

- [ ] **Step 6: Create `tests/compass/policy/conftest.py`** (shared fixtures used by later tasks)

```python
"""Shared fixtures for compass.policy tests."""

from __future__ import annotations

from typing import Any

import pytest

from compass.policy.types import Predicate, Violation


def make_predicate(
    name: str = "test_primitive",
    params: dict[str, Any] | None = None,
    *,
    fires: bool = False,
    message: str = "boom",
    evidence: dict[str, Any] | None = None,
) -> Predicate:
    """Construct a synthetic predicate for engine/registry tests.

    Returning a non-None Violation when ``fires=True`` lets a single
    factory cover both fire and skip cases.
    """

    def fn(_ctx: Any) -> Violation | None:
        if not fires:
            return None
        return Violation(rule_id="", message=message, evidence=evidence or {})

    return Predicate(primitive_name=name, params=params or {}, fn=fn)


@pytest.fixture
def firing_predicate() -> Predicate:
    return make_predicate("test_primitive", fires=True)


@pytest.fixture
def passing_predicate() -> Predicate:
    return make_predicate("test_primitive", fires=False)
```

- [ ] **Step 7: Write the failing test `tests/compass/policy/test_rule_validation.py`**

```python
"""Rule.__post_init__ enforces the severity-vs-phase invariant.

Build-plan §Policy Engine: ESCALATE is only realizable at workflow-
level phases. Stage-5 spec §Types pins the rejection to construction
time so misconfigurations are caught at module import, not at first
evaluation.
"""

from __future__ import annotations

import pytest

from compass.policy import Phase, Rule, Severity
from tests.compass.policy.conftest import make_predicate


def test_escalate_at_input_validation_rejected() -> None:
    with pytest.raises(ValueError, match="ESCALATE is not realizable"):
        Rule(
            id="bad",
            phase=Phase.input_validation,
            predicate=make_predicate(),
            severity=Severity.ESCALATE,
        )


def test_escalate_at_output_validation_rejected() -> None:
    with pytest.raises(ValueError, match="ESCALATE is not realizable"):
        Rule(
            id="bad",
            phase=Phase.output_validation,
            predicate=make_predicate(),
            severity=Severity.ESCALATE,
        )


def test_escalate_at_workflow_phase_accepted() -> None:
    rule = Rule(
        id="ok",
        phase=Phase.pre_action_proposal,
        predicate=make_predicate(),
        severity=Severity.ESCALATE,
    )
    assert rule.severity is Severity.ESCALATE


def test_block_at_input_validation_accepted() -> None:
    rule = Rule(
        id="ok",
        phase=Phase.input_validation,
        predicate=make_predicate(),
        severity=Severity.BLOCK,
    )
    assert rule.severity is Severity.BLOCK
```

- [ ] **Step 8: Run to verify it passes** (types + __post_init__ already implemented in Step 2)

Run: `uv run pytest tests/compass/policy/test_rule_validation.py -v`
Expected: 4 passed.

- [ ] **Step 9: Commit**

```bash
git add compass/ tests/compass/__init__.py tests/compass/policy/__init__.py tests/compass/policy/conftest.py tests/compass/policy/test_rule_validation.py
git commit -m "feat(stage-5): compass.policy types + errors + Rule.__post_init__ guard"
```

---

## Task 2: Path resolver (`compass/policy/paths.py`)

Predicates take field paths like `"proposal.line_items[*].source_refs"`. A pure helper resolves them against a context dict; the `MISSING` sentinel lets predicates distinguish "absent" from "present but falsy".

**Files:**
- Create: `compass/policy/paths.py`
- Create: `tests/compass/policy/test_paths.py`

- [ ] **Step 1: Write the failing test `tests/compass/policy/test_paths.py`**

```python
"""resolve_dotted: dotted-path lookup with [*] wildcard."""

from __future__ import annotations

from compass.policy.paths import MISSING, resolve_dotted


def test_single_key() -> None:
    assert resolve_dotted({"a": 1}, "a") == 1


def test_nested_keys() -> None:
    assert resolve_dotted({"a": {"b": {"c": 7}}}, "a.b.c") == 7


def test_missing_root_returns_sentinel() -> None:
    assert resolve_dotted({}, "a") is MISSING


def test_missing_nested_returns_sentinel() -> None:
    assert resolve_dotted({"a": {"b": 1}}, "a.c") is MISSING


def test_traverse_through_none_returns_sentinel() -> None:
    assert resolve_dotted({"a": None}, "a.b") is MISSING


def test_wildcard_collects_list_elements() -> None:
    ctx = {"items": [{"x": 1}, {"x": 2}, {"x": 3}]}
    assert resolve_dotted(ctx, "items[*].x") == [1, 2, 3]


def test_wildcard_terminal() -> None:
    ctx = {"items": [{"x": 1}, {"x": 2}]}
    assert resolve_dotted(ctx, "items[*]") == [{"x": 1}, {"x": 2}]


def test_wildcard_on_missing_list_returns_sentinel() -> None:
    assert resolve_dotted({}, "items[*].x") is MISSING


def test_wildcard_on_non_list_returns_sentinel() -> None:
    assert resolve_dotted({"items": "not a list"}, "items[*].x") is MISSING


def test_present_falsy_value_not_sentinel() -> None:
    assert resolve_dotted({"a": 0}, "a") == 0
    assert resolve_dotted({"a": ""}, "a") == ""
    assert resolve_dotted({"a": []}, "a") == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/test_paths.py -v`
Expected: ImportError on `compass.policy.paths`.

- [ ] **Step 3: Implement `compass/policy/paths.py`**

```python
"""Dotted-path resolver with ``[*]`` wildcard.

Predicates use string paths to navigate the context dict. The MISSING
sentinel lets predicates distinguish "key absent" from "key present
with falsy value" — important for ``require_existing_entity`` (where
absence is the firing condition) versus ``entity_status_equals``
(where absence may mean "skip this rule, the entity wasn't queried").

Grammar:
    path     := segment ('.' segment)*
    segment  := identifier | identifier '[*]'

``items[*].x`` returns a list of every item's ``x``. ``items[*]`` (no
suffix) returns the list itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

# Sentinel for "the path did not resolve to anything". A unique object
# so callers compare with ``is``.
MISSING: Final[object] = object()


def resolve_dotted(ctx: Mapping[str, Any], path: str) -> Any:
    """Navigate ``ctx`` along ``path``; return MISSING on any miss."""
    segments = path.split(".")
    return _resolve(ctx, segments)


def _resolve(node: Any, segments: list[str]) -> Any:
    if not segments:
        return node
    head, *rest = segments
    wildcard = head.endswith("[*]")
    key = head[:-3] if wildcard else head

    if node is None or not isinstance(node, Mapping) or key not in node:
        return MISSING
    value = node[key]

    if wildcard:
        if not isinstance(value, list):
            return MISSING
        # Map remaining segments over every element; drop MISSING entries
        # would be misleading, so we return MISSING for the whole path
        # if any element fails to resolve.
        resolved = [_resolve(item, rest) for item in value]
        if any(r is MISSING for r in resolved):
            return MISSING
        return resolved
    return _resolve(value, rest)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/test_paths.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add compass/policy/paths.py tests/compass/policy/test_paths.py
git commit -m "feat(stage-5): dotted-path resolver with [*] wildcard"
```

---

## Task 3: Primitive registry (`compass/policy/registry.py`)

The `@primitive` decorator + `list_primitives()`. Captures factory name + frozen params into the returned `Predicate` so hashing can serialize them and coverage can count them. Rejects duplicate name registrations.

**Files:**
- Create: `compass/policy/registry.py`
- Create: `tests/compass/policy/test_registry.py`
- Modify: `compass/policy/__init__.py` (export `primitive`, `list_primitives`)

- [ ] **Step 1: Write the failing test `tests/compass/policy/test_registry.py`**

```python
"""@primitive registry semantics."""

from __future__ import annotations

import pytest

from compass.policy.registry import (
    _REGISTRY,
    list_primitives,
    primitive,
)
from compass.policy.types import Predicate, Violation


@pytest.fixture(autouse=True)
def _clear_registry():
    """Tests in this module install primitives — reset between runs."""
    snapshot = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


def test_decorator_returns_predicate_with_name_and_params() -> None:
    @primitive("my_threshold")
    def my_threshold(*, max: int):
        def check(_ctx):
            return None
        return check

    pred = my_threshold(max=10)
    assert isinstance(pred, Predicate)
    assert pred.primitive_name == "my_threshold"
    assert pred.params == {"max": 10}


def test_params_are_frozen() -> None:
    @primitive("my_threshold")
    def my_threshold(*, max: int):
        def check(_ctx):
            return None
        return check

    pred = my_threshold(max=10)
    with pytest.raises(TypeError):
        pred.params["max"] = 99  # type: ignore[index]


def test_list_primitives_returns_registered() -> None:
    @primitive("first")
    def first(): return lambda _ctx: None
    @primitive("second")
    def second(): return lambda _ctx: None

    catalogue = list_primitives()
    assert set(catalogue.keys()) == {"first", "second"}


def test_duplicate_registration_raises() -> None:
    @primitive("dup")
    def dup_a(): return lambda _ctx: None

    with pytest.raises(RuntimeError, match="duplicate primitive"):
        @primitive("dup")
        def dup_b(): return lambda _ctx: None


async def test_predicate_passes_through_violation() -> None:
    @primitive("returns_violation")
    def factory():
        def check(_ctx):
            return Violation(rule_id="", message="x", evidence={})
        return check

    pred = factory()
    result = await pred({})
    assert result is not None
    assert result.message == "x"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/test_registry.py -v`
Expected: ImportError on `compass.policy.registry`.

- [ ] **Step 3: Implement `compass/policy/registry.py`**

```python
"""Primitive registry — ``@primitive`` decorator and ``list_primitives``.

Why the registry exists: ``hash_rules`` needs primitive name + frozen
params to canonicalize rule sets, and the Stage-10 coverage report
counts rule fires per primitive. Without registration, neither has a
stable handle. See spec §Registry — @primitive.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

from compass.policy.types import Predicate

# Module-level catalogue. Public surface is ``list_primitives()``;
# direct access is allowed inside tests (see conftest auto-reset).
_REGISTRY: dict[str, Callable[..., Predicate]] = {}


def primitive(name: str) -> Callable[[Callable[..., Callable]], Callable[..., Predicate]]:
    """Register a primitive factory under ``name``.

    The decorated factory MUST take keyword-only arguments (so the
    serialization in ``hash_rules`` is deterministic). It returns a
    plain callable ``(ctx) -> Violation | None``; the wrapper packages
    that callable + name + frozen params into a Predicate.
    """

    def decorator(factory: Callable[..., Callable]) -> Callable[..., Predicate]:
        @functools.wraps(factory)
        def wrapped(**params: Any) -> Predicate:
            fn = factory(**params)
            return Predicate(primitive_name=name, params=_freeze(params), fn=fn)

        if name in _REGISTRY:
            raise RuntimeError(f"duplicate primitive registration: {name!r}")
        _REGISTRY[name] = wrapped
        return wrapped

    return decorator


def list_primitives() -> dict[str, Callable[..., Predicate]]:
    """Snapshot of the registry, keyed by primitive name."""
    return dict(_REGISTRY)


def _freeze(params: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a read-only view of ``params``.

    A primitive's params are the rule's identity for hashing; mutation
    after construction would corrupt the hash. MappingProxyType gives
    read-only access without copying.
    """
    return MappingProxyType(dict(params))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/test_registry.py -v`
Expected: 5 passed.

- [ ] **Step 5: Export from `compass/policy/__init__.py`**

Modify `compass/policy/__init__.py` — add to imports and `__all__`:

```python
from compass.policy.registry import list_primitives, primitive
```

And add `"list_primitives"`, `"primitive"` to `__all__`.

- [ ] **Step 6: Commit**

```bash
git add compass/policy/registry.py compass/policy/__init__.py tests/compass/policy/test_registry.py
git commit -m "feat(stage-5): @primitive registry + list_primitives"
```

---

## Task 4: Sink module (`compass/policy/sink.py`)

Protocol + 3 implementations + global register hooks. `AuditLogSink` (DB-backed) lives in a separate file — Task 8 — because it depends on psycopg.

**Files:**
- Create: `compass/policy/sink.py`
- Create: `tests/compass/policy/test_sink.py`
- Modify: `compass/policy/__init__.py`

- [ ] **Step 1: Write the failing test `tests/compass/policy/test_sink.py`**

```python
"""Sink protocol + in-memory/null/multi sinks + register_sink."""

from __future__ import annotations

import pytest

from compass.policy.sink import (
    InMemorySink,
    MultiSink,
    NullSink,
    clear_sinks,
    get_registered_sinks,
    register_sink,
)


@pytest.fixture(autouse=True)
def _clear_global():
    clear_sinks()
    yield
    clear_sinks()


async def test_in_memory_sink_captures_events() -> None:
    sink = InMemorySink()
    await sink.emit({"event_kind": "rule_fired", "rule_id": "r1"})
    await sink.emit({"event_kind": "rule_skipped", "rule_id": "r2"})
    assert sink.events == [
        {"event_kind": "rule_fired", "rule_id": "r1"},
        {"event_kind": "rule_skipped", "rule_id": "r2"},
    ]


async def test_null_sink_is_silent() -> None:
    sink = NullSink()
    await sink.emit({"event_kind": "rule_fired", "rule_id": "r1"})
    # No assertion needed; just verifying no exception.


async def test_multi_sink_fans_out() -> None:
    a, b = InMemorySink(), InMemorySink()
    multi = MultiSink([a, b])
    await multi.emit({"event_kind": "rule_fired", "rule_id": "r1"})
    assert a.events == [{"event_kind": "rule_fired", "rule_id": "r1"}]
    assert b.events == [{"event_kind": "rule_fired", "rule_id": "r1"}]


def test_register_sink_adds_to_global_list() -> None:
    sink = InMemorySink()
    register_sink(sink)
    assert get_registered_sinks() == [sink]


def test_clear_sinks_empties_registry() -> None:
    register_sink(InMemorySink())
    clear_sinks()
    assert get_registered_sinks() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/test_sink.py -v`
Expected: ImportError on `compass.policy.sink`.

- [ ] **Step 3: Implement `compass/policy/sink.py`**

```python
"""Sink — where the engine sends rule_fired / rule_skipped events.

The engine emits one event per evaluated rule. Sinks decide where the
event lands: an in-memory list for unit tests, an audit_log row for
production, fan-out for both at once. Decoupling means the engine
doesn't know about Postgres, and tests don't need a database.

Three sinks ship at Stage 5:

* InMemorySink — for tests
* NullSink — default when no sink registered; discards
* MultiSink — fan-out
* AuditLogSink — in compass/policy/audit_sink.py (DB-backed)

See spec §Sink for the architectural rationale.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Sink(Protocol):
    """One method: emit an event dict."""

    async def emit(self, event: dict[str, Any]) -> None: ...


class InMemorySink:
    """Collect events in a list. Use in unit tests."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class NullSink:
    """Drop events on the floor. Default when nothing is registered."""

    async def emit(self, event: dict[str, Any]) -> None:
        return None


class MultiSink:
    """Fan an event out to every wrapped sink."""

    def __init__(self, sinks: Iterable[Sink]) -> None:
        self._sinks: list[Sink] = list(sinks)

    async def emit(self, event: dict[str, Any]) -> None:
        for sink in self._sinks:
            await sink.emit(event)


# ---- module-level registry (process-wide additive sinks) ----

_REGISTERED: list[Sink] = []


def register_sink(sink: Sink) -> None:
    """Add ``sink`` to the process-wide list.

    The workflow's evaluate_policy activity passes an explicit
    AuditLogSink alongside whatever is registered — registered sinks
    are for cross-cutting concerns (a future Langfuse exporter, etc.).
    """
    _REGISTERED.append(sink)


def get_registered_sinks() -> list[Sink]:
    return list(_REGISTERED)


def clear_sinks() -> None:
    """For tests. Not part of production behavior."""
    _REGISTERED.clear()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/test_sink.py -v`
Expected: 5 passed.

- [ ] **Step 5: Export from `compass/policy/__init__.py`**

Add to imports:

```python
from compass.policy.sink import (
    InMemorySink,
    MultiSink,
    NullSink,
    Sink,
    clear_sinks,
    register_sink,
)
```

And add to `__all__`: `"InMemorySink"`, `"MultiSink"`, `"NullSink"`, `"Sink"`, `"clear_sinks"`, `"register_sink"`.

- [ ] **Step 6: Commit**

```bash
git add compass/policy/sink.py compass/policy/__init__.py tests/compass/policy/test_sink.py
git commit -m "feat(stage-5): Sink protocol + InMemory/Null/Multi + register_sink"
```

---

## Task 5: Engine (`compass/policy/engine.py`)

The async `evaluate(rules, phase, context, *, sink) -> Decision` and the three phase wrappers. Loop semantics, severity bucketing, exception wrapping.

**Files:**
- Create: `compass/policy/engine.py`
- Create: `tests/compass/policy/test_engine.py`
- Modify: `compass/policy/__init__.py`

- [ ] **Step 1: Write the failing test `tests/compass/policy/test_engine.py`**

```python
"""Engine: evaluate() loop semantics, severity routing, exception wrapping."""

from __future__ import annotations

import pytest

from compass.policy import (
    Decision,
    Phase,
    PolicyEngineError,
    Rule,
    Severity,
    Violation,
)
from compass.policy.engine import (
    evaluate,
    evaluate_audit_validation,
    evaluate_pre_action_proposal,
    evaluate_pre_execute,
)
from compass.policy.sink import InMemorySink
from compass.policy.types import Predicate
from tests.compass.policy.conftest import make_predicate


async def test_no_matching_rules_permits() -> None:
    sink = InMemorySink()
    decision = await evaluate([], Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is True
    assert decision.rule_ids_fired == ()
    assert sink.events == []


async def test_phase_mismatch_skipped_silently() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r_pre_exec_only",
        phase=Phase.pre_execute,
        predicate=make_predicate(fires=True),
    )
    decision = await evaluate([rule], Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is True
    assert sink.events == []  # different-phase rules emit no events


async def test_skipped_rule_emits_event() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r1",
        phase=Phase.pre_action_proposal,
        predicate=make_predicate(fires=False),
    )
    decision = await evaluate([rule], Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is True
    assert decision.rule_ids_fired == ()
    assert sink.events == [
        {
            "event_kind": "rule_skipped",
            "rule_id": "r1",
            "phase": "pre_action_proposal",
        }
    ]


async def test_block_rule_fires_and_blocks() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r_block",
        phase=Phase.pre_action_proposal,
        predicate=make_predicate(fires=True, message="bad", evidence={"x": 1}),
        regulatory_basis=("SOP-1",),
        severity=Severity.BLOCK,
    )
    decision = await evaluate([rule], Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is False
    assert decision.rule_ids_fired == ("r_block",)
    assert len(decision.violations) == 1
    assert decision.violations[0].rule_id == "r_block"
    assert decision.violations[0].message == "bad"
    assert decision.violations[0].evidence == {"x": 1}
    fired_events = [e for e in sink.events if e["event_kind"] == "rule_fired"]
    assert len(fired_events) == 1
    assert fired_events[0]["rule_id"] == "r_block"
    assert fired_events[0]["decision"] == "block"
    assert fired_events[0]["regulatory_basis"] == ["SOP-1"]


async def test_escalate_rule_fires_but_permits() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r_esc",
        phase=Phase.pre_action_proposal,
        predicate=make_predicate(fires=True),
        severity=Severity.ESCALATE,
    )
    decision = await evaluate([rule], Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is True       # escalation does not block
    assert decision.rule_ids_fired == ("r_esc",)
    assert len(decision.escalations) == 1
    assert decision.violations == ()


async def test_declaration_order_preserved() -> None:
    sink = InMemorySink()
    rules = [
        Rule(id=f"r{i}", phase=Phase.pre_action_proposal,
             predicate=make_predicate(fires=True))
        for i in range(3)
    ]
    decision = await evaluate(rules, Phase.pre_action_proposal, {}, sink=sink)
    assert decision.rule_ids_fired == ("r0", "r1", "r2")
    assert [e["rule_id"] for e in sink.events] == ["r0", "r1", "r2"]


async def test_predicate_exception_wrapped_as_engine_error() -> None:
    def raises(_ctx):
        raise RuntimeError("predicate exploded")

    pred = Predicate(primitive_name="bad", params={}, fn=raises)
    rule = Rule(id="r_bad", phase=Phase.pre_action_proposal, predicate=pred)
    sink = InMemorySink()
    with pytest.raises(PolicyEngineError) as exc:
        await evaluate([rule], Phase.pre_action_proposal, {}, sink=sink)
    assert exc.value.rule_id == "r_bad"
    assert isinstance(exc.value.cause, RuntimeError)


async def test_evaluate_pre_action_proposal_wrapper() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r1",
        phase=Phase.pre_action_proposal,
        predicate=make_predicate(fires=False),
    )
    decision = await evaluate_pre_action_proposal([rule], {}, sink=sink)
    assert isinstance(decision, Decision)
    assert decision.permit is True


async def test_evaluate_pre_execute_wrapper_filters_phase() -> None:
    sink = InMemorySink()
    rules = [
        Rule(id="r_proposal", phase=Phase.pre_action_proposal,
             predicate=make_predicate(fires=True)),
        Rule(id="r_exec", phase=Phase.pre_execute,
             predicate=make_predicate(fires=False)),
    ]
    decision = await evaluate_pre_execute(rules, {}, sink=sink)
    # Only the pre_execute rule was evaluated.
    assert [e["rule_id"] for e in sink.events] == ["r_exec"]
    assert decision.permit is True


async def test_evaluate_audit_validation_wrapper() -> None:
    sink = InMemorySink()
    rule = Rule(
        id="r_audit",
        phase=Phase.audit_validation,
        predicate=make_predicate(fires=False),
    )
    decision = await evaluate_audit_validation([rule], {}, sink=sink)
    assert decision.permit is True


async def test_mixed_block_and_escalate_blocks() -> None:
    sink = InMemorySink()
    rules = [
        Rule(id="r_block", phase=Phase.pre_action_proposal,
             predicate=make_predicate(fires=True), severity=Severity.BLOCK),
        Rule(id="r_esc", phase=Phase.pre_action_proposal,
             predicate=make_predicate(fires=True), severity=Severity.ESCALATE),
    ]
    decision = await evaluate(rules, Phase.pre_action_proposal, {}, sink=sink)
    assert decision.permit is False
    assert decision.rule_ids_fired == ("r_block", "r_esc")
    assert len(decision.violations) == 1
    assert len(decision.escalations) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/test_engine.py -v`
Expected: ImportError on `compass.policy.engine`.

- [ ] **Step 3: Implement `compass/policy/engine.py`**

```python
"""Compass policy engine — the ``evaluate`` core.

Pure, async. Walks ``rules`` in declaration order, runs each whose
``phase`` matches, emits one event per evaluated rule to ``sink``,
buckets violations by severity, returns a Decision.

The function itself does no I/O — sinks do. Predicates may invoke
sub-agent ``Runner.run`` calls; those are activity-wrapped by the
OpenAIAgentsPlugin so the engine's purity isn't violated.

See spec §Engine — evaluate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from compass.policy.errors import PolicyEngineError
from compass.policy.sink import Sink
from compass.policy.types import Decision, Phase, Rule, Severity, Violation


async def evaluate(
    rules: Sequence[Rule],
    phase: Phase,
    context: Mapping[str, Any],
    *,
    sink: Sink,
) -> Decision:
    """Run every rule whose phase matches; return the aggregate Decision."""
    violations: list[Violation] = []
    escalations: list[Violation] = []
    rule_ids_fired: list[str] = []

    for rule in rules:
        if rule.phase != phase:
            continue
        try:
            outcome = await rule.predicate(context)
        except Exception as exc:  # noqa: BLE001 — wrap *any* predicate raise
            raise PolicyEngineError(rule_id=rule.id, cause=exc) from exc

        if outcome is None:
            await sink.emit(
                {
                    "event_kind": "rule_skipped",
                    "rule_id": rule.id,
                    "phase": phase.value,
                }
            )
            continue

        # Predicate constructed Violation with rule_id=""; fill in from rule.
        violation = Violation(
            rule_id=rule.id,
            message=outcome.message,
            evidence=outcome.evidence,
        )
        rule_ids_fired.append(rule.id)
        if rule.severity is Severity.ESCALATE:
            escalations.append(violation)
        else:
            violations.append(violation)

        await sink.emit(
            {
                "event_kind": "rule_fired",
                "rule_id": rule.id,
                "phase": phase.value,
                "decision": rule.severity.value,
                "evidence": violation.evidence,
                "message": violation.message,
                "regulatory_basis": list(rule.regulatory_basis),
            }
        )

    permit = len(violations) == 0  # escalations do not block
    return Decision(
        permit=permit,
        violations=tuple(violations),
        escalations=tuple(escalations),
        rule_ids_fired=tuple(rule_ids_fired),
    )


# ---- phase-specific wrappers --------------------------------------


async def evaluate_pre_action_proposal(
    rules: Sequence[Rule],
    context: Mapping[str, Any],
    *,
    sink: Sink,
) -> Decision:
    return await evaluate(rules, Phase.pre_action_proposal, context, sink=sink)


async def evaluate_pre_execute(
    rules: Sequence[Rule],
    context: Mapping[str, Any],
    *,
    sink: Sink,
) -> Decision:
    return await evaluate(rules, Phase.pre_execute, context, sink=sink)


async def evaluate_audit_validation(
    rules: Sequence[Rule],
    context: Mapping[str, Any],
    *,
    sink: Sink,
) -> Decision:
    return await evaluate(rules, Phase.audit_validation, context, sink=sink)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/test_engine.py -v`
Expected: 11 passed.

- [ ] **Step 5: Export from `compass/policy/__init__.py`**

Add:

```python
from compass.policy.engine import (
    evaluate,
    evaluate_audit_validation,
    evaluate_pre_action_proposal,
    evaluate_pre_execute,
)
```

And the four names to `__all__`.

- [ ] **Step 6: Commit**

```bash
git add compass/policy/engine.py compass/policy/__init__.py tests/compass/policy/test_engine.py
git commit -m "feat(stage-5): policy engine evaluate() + phase wrappers"
```

---

## Task 6: Hashing (`compass/policy/hashing.py`)

Canonical serialization + sha256 of a rule set. Same serialization used by `policy_snapshots.rules_json`.

**Files:**
- Create: `compass/policy/hashing.py`
- Create: `tests/compass/policy/test_hash.py`
- Modify: `compass/policy/__init__.py`

- [ ] **Step 1: Write the failing test `tests/compass/policy/test_hash.py`**

```python
"""hash_rules: canonical, deterministic, param-sensitive."""

from __future__ import annotations

from compass.policy import Phase, Rule, Severity
from compass.policy.hashing import canonicalize_rule, hash_rules, serialize_rules
from compass.policy.types import Predicate


def _pred(name: str = "p", **params) -> Predicate:
    def check(_ctx):
        return None
    return Predicate(primitive_name=name, params=dict(params), fn=check)


def test_same_rules_same_hash() -> None:
    r = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred("p", max=10))
    assert hash_rules([r]) == hash_rules([r])


def test_param_change_changes_hash() -> None:
    r1 = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred("p", max=10))
    r2 = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred("p", max=11))
    assert hash_rules([r1]) != hash_rules([r2])


def test_param_key_order_does_not_change_hash() -> None:
    # Params dicts ordered differently must hash identically.
    r1 = Rule(id="r1", phase=Phase.pre_action_proposal,
              predicate=Predicate(primitive_name="p",
                                  params={"a": 1, "b": 2}, fn=lambda _c: None))
    r2 = Rule(id="r1", phase=Phase.pre_action_proposal,
              predicate=Predicate(primitive_name="p",
                                  params={"b": 2, "a": 1}, fn=lambda _c: None))
    assert hash_rules([r1]) == hash_rules([r2])


def test_rule_reorder_changes_hash() -> None:
    # Declaration order is part of the policy identity.
    r1 = Rule(id="a", phase=Phase.pre_action_proposal, predicate=_pred())
    r2 = Rule(id="b", phase=Phase.pre_action_proposal, predicate=_pred())
    assert hash_rules([r1, r2]) != hash_rules([r2, r1])


def test_severity_change_changes_hash() -> None:
    r1 = Rule(id="r1", phase=Phase.pre_action_proposal,
              predicate=_pred(), severity=Severity.BLOCK)
    r2 = Rule(id="r1", phase=Phase.pre_action_proposal,
              predicate=_pred(), severity=Severity.ESCALATE)
    assert hash_rules([r1]) != hash_rules([r2])


def test_regulatory_basis_change_changes_hash() -> None:
    r1 = Rule(id="r1", phase=Phase.pre_action_proposal,
              predicate=_pred(), regulatory_basis=("a",))
    r2 = Rule(id="r1", phase=Phase.pre_action_proposal,
              predicate=_pred(), regulatory_basis=("b",))
    assert hash_rules([r1]) != hash_rules([r2])


def test_canonicalize_rule_keys() -> None:
    r = Rule(id="r1", phase=Phase.pre_action_proposal,
             predicate=_pred("p", max=10),
             severity=Severity.BLOCK,
             regulatory_basis=("SOP-1",),
             tags=("tag1", "tag2"),
             must_be_covered=True)
    canon = canonicalize_rule(r)
    assert canon["id"] == "r1"
    assert canon["phase"] == "pre_action_proposal"
    assert canon["primitive"] == "p"
    assert canon["params"] == {"max": 10}
    assert canon["severity"] == "block"
    assert canon["regulatory_basis"] == ["SOP-1"]
    assert canon["tags"] == ["tag1", "tag2"]
    assert canon["must_be_covered"] is True
    assert canon["surface_to_user"] is True


def test_serialize_rules_is_a_list() -> None:
    r = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred())
    assert isinstance(serialize_rules([r]), list)
    assert serialize_rules([r])[0]["id"] == "r1"


def test_hash_is_hex_sha256() -> None:
    r = Rule(id="r1", phase=Phase.pre_action_proposal, predicate=_pred())
    h = hash_rules([r])
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/test_hash.py -v`
Expected: ImportError on `compass.policy.hashing`.

- [ ] **Step 3: Implement `compass/policy/hashing.py`**

```python
"""Canonical hashing of a rule set.

The hash is the audit log's reconstructability anchor: every
``audit_log`` row carries ``policy_hash``, every distinct hash has a
matching ``policy_snapshots.rules_json``, and ``rules_json`` is
byte-identical to what produced the hash. See spec §Hashing.

Canonicalization rules:

* Rules in declaration order (matches iteration order in ``evaluate``).
* Per rule: ``{id, phase, primitive, params, severity, regulatory_basis,
  tags, must_be_covered, surface_to_user}``.
* Tuples → JSON-native lists; param dicts → sorted keys (recursively).
* ``json.dumps(..., sort_keys=False, separators=(',', ':'))`` over the
  sorted-key dicts; sha256 hex.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from compass.policy.types import Rule


def canonicalize_rule(rule: Rule) -> dict[str, Any]:
    """Serialize one rule to a JSON-safe, deterministic dict."""
    return {
        "id": rule.id,
        "phase": rule.phase.value,
        "primitive": rule.predicate.primitive_name,
        "params": _sort_recursive(dict(rule.predicate.params)),
        "severity": rule.severity.value,
        "regulatory_basis": list(rule.regulatory_basis),
        "tags": list(rule.tags),
        "must_be_covered": rule.must_be_covered,
        "surface_to_user": rule.surface_to_user,
    }


def serialize_rules(rules: Sequence[Rule]) -> list[dict[str, Any]]:
    """Canonical list-of-dicts for the full rule set."""
    return [canonicalize_rule(r) for r in rules]


def hash_rules(rules: Sequence[Rule]) -> str:
    """sha256 hex over the canonical serialization."""
    blob = json.dumps(serialize_rules(rules), sort_keys=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _sort_recursive(value: Any) -> Any:
    """Sort dict keys at every level so param order doesn't affect the hash."""
    if isinstance(value, Mapping):
        return {k: _sort_recursive(value[k]) for k in sorted(value)}
    if isinstance(value, list | tuple):
        return [_sort_recursive(v) for v in value]
    return value
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/test_hash.py -v`
Expected: 9 passed.

- [ ] **Step 5: Export from `compass/policy/__init__.py`**

Add:

```python
from compass.policy.hashing import canonicalize_rule, hash_rules, serialize_rules
```

And the three names to `__all__`.

- [ ] **Step 6: Commit**

```bash
git add compass/policy/hashing.py compass/policy/__init__.py tests/compass/policy/test_hash.py
git commit -m "feat(stage-5): hash_rules + canonical serialization"
```

---

## Task 7: Snapshot writer (`compass/policy/snapshot.py`)

Inserts into `policy_snapshots`; idempotent via PK + `ON CONFLICT DO NOTHING`. Tested via the workflow integration tests (no dedicated unit-test file because mocking psycopg adds noise without information).

**Files:**
- Create: `compass/policy/snapshot.py`
- Modify: `compass/policy/__init__.py`

- [ ] **Step 1: Implement `compass/policy/snapshot.py`**

```python
"""Write a policy_snapshots row inside the evaluate_policy transaction.

Called once per evaluate_policy activity invocation; ``ON CONFLICT DO
NOTHING`` makes the second-and-later calls per worker × hash a no-op.
The serialized ``rules_json`` is byte-identical to what ``hash_rules``
hashed, so a 5-year-old audit row's policy_hash always resolves to a
reconstructable rule set.

Tested via tests/workflows/send_invoice/test_workflow_policy.py
(`test_policy_snapshot_written_once`).
"""

from __future__ import annotations

from collections.abc import Sequence

import psycopg
from psycopg.types.json import Jsonb

from compass.policy.hashing import hash_rules, serialize_rules
from compass.policy.types import Rule


async def write_policy_snapshot(
    conn: psycopg.AsyncConnection,
    workflow: str,
    rules: Sequence[Rule],
) -> str:
    """Idempotently INSERT a policy_snapshots row; return the policy_hash.

    Must be called inside an open transaction on ``conn`` — the caller
    (evaluate_policy activity) commits after the audit writes also
    succeed.
    """
    policy_hash = hash_rules(rules)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO policy_snapshots (policy_hash, workflow, rules_json)
            VALUES (%s, %s, %s)
            ON CONFLICT (policy_hash) DO NOTHING
            """,
            (policy_hash, workflow, Jsonb(serialize_rules(rules))),
        )
    return policy_hash
```

- [ ] **Step 2: Export from `compass/policy/__init__.py`**

Add:

```python
from compass.policy.snapshot import write_policy_snapshot
```

And `"write_policy_snapshot"` to `__all__`.

- [ ] **Step 3: Commit**

```bash
git add compass/policy/snapshot.py compass/policy/__init__.py
git commit -m "feat(stage-5): write_policy_snapshot — audit reconstructability"
```

---

## Task 8: AuditLogSink + SequenceAllocator (`compass/policy/audit_sink.py`)

DB-backed sink. Writes one `audit_log` row per emitted event. Allocator owned outside the sink so the workflow can return `next_sequence_no` from the activity.

**Files:**
- Create: `compass/policy/audit_sink.py`
- Modify: `compass/policy/__init__.py`

- [ ] **Step 1: Implement `compass/policy/audit_sink.py`**

```python
"""AuditLogSink — Sink implementation that writes audit_log rows.

Each emit becomes one INSERT inside the activity's open transaction.
Idempotent via UNIQUE (workflow_run_id, sequence_no) + ON CONFLICT DO
NOTHING from db/schema.sql; activity retries that re-emit the same
events collide harmlessly with the previous attempt's writes.

SequenceAllocator wraps a monotonic counter the activity uses to assign
sequence_no values. The workflow allocates the starting value and the
activity returns peek() so the workflow's own counter stays in sync.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


class SequenceAllocator:
    """Monotonic counter the sink draws sequence_no values from.

    Starts at the value the workflow passes in. Each call to ``next()``
    increments and returns. ``peek()`` returns the next-free value
    without advancing — the activity returns peek() to the workflow so
    the workflow's _next_seq can resume from there.
    """

    def __init__(self, starting_sequence_no: int) -> None:
        if starting_sequence_no < 1:
            raise ValueError("starting_sequence_no must be >= 1")
        self._next: int = starting_sequence_no

    def __iter__(self) -> Iterator[int]:
        return self

    def __next__(self) -> int:
        value = self._next
        self._next += 1
        return value

    def peek(self) -> int:
        return self._next


class AuditLogSink:
    """Writes each event to audit_log as one row.

    Caller must keep ``conn`` open across all emits (one transaction).
    The sink does not commit — that's the activity's job once all writes
    (snapshot + rule events) succeed.
    """

    def __init__(
        self,
        conn: psycopg.AsyncConnection,
        workflow_run_id: str,
        allocator: SequenceAllocator,
        policy_hash: str,
    ) -> None:
        self._conn = conn
        self._workflow_run_id = workflow_run_id
        self._allocator = allocator
        self._policy_hash = policy_hash

    async def emit(self, event: dict[str, Any]) -> None:
        sequence_no = next(self._allocator)
        # decision column: 'block' / 'escalate' for fired; NULL for skipped.
        decision = event.get("decision")
        phase = event["phase"]
        async with self._conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO audit_log (
                    workflow_run_id, sequence_no, phase, event_kind,
                    rule_id, policy_hash, decision, actor, payload
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (workflow_run_id, sequence_no) DO NOTHING
                """,
                (
                    self._workflow_run_id,
                    sequence_no,
                    phase,
                    event["event_kind"],
                    event.get("rule_id"),
                    self._policy_hash,
                    decision,
                    None,  # actor: NULL for rule_* events
                    Jsonb(_payload_from_event(event)),
                ),
            )


def _payload_from_event(event: dict[str, Any]) -> dict[str, Any]:
    """Build the JSONB payload that lands in audit_log.payload."""
    payload: dict[str, Any] = {}
    # regulatory_basis is denormalized into payload for 7-year audit
    # interpretability without joining policy_snapshots.
    if "regulatory_basis" in event:
        payload["regulatory_basis"] = event["regulatory_basis"]
    if "message" in event:
        payload["message"] = event["message"]
    if "evidence" in event:
        payload["evidence"] = event["evidence"]
    return payload
```

- [ ] **Step 2: Export from `compass/policy/__init__.py`**

Add:

```python
from compass.policy.audit_sink import AuditLogSink, SequenceAllocator
```

And both to `__all__`.

- [ ] **Step 3: Commit**

```bash
git add compass/policy/audit_sink.py compass/policy/__init__.py
git commit -m "feat(stage-5): AuditLogSink + SequenceAllocator"
```

---

## Task 9: `numeric_threshold` primitive

**Files:**
- Create: `compass/policy/primitives/__init__.py` (empty)
- Create: `compass/policy/primitives/value.py`
- Create: `tests/compass/policy/primitives/__init__.py` (empty)
- Create: `tests/compass/policy/primitives/test_value.py`

- [ ] **Step 1: Create empty `compass/policy/primitives/__init__.py` and `tests/compass/policy/primitives/__init__.py`**

```python
```

- [ ] **Step 2: Write the failing test `tests/compass/policy/primitives/test_value.py`**

```python
"""numeric_threshold: above/below/within/missing-path cases."""

from __future__ import annotations

import pytest

from compass.policy.primitives.value import numeric_threshold


async def test_above_max_fires() -> None:
    pred = numeric_threshold(field="proposal.total_cents", max=10_000)
    v = await pred({"proposal": {"total_cents": 15_000}})
    assert v is not None
    assert v.evidence == {"field": "proposal.total_cents", "value": 15_000, "max": 10_000}


async def test_equal_max_skips() -> None:
    pred = numeric_threshold(field="x", max=10)
    assert await pred({"x": 10}) is None


async def test_below_min_fires() -> None:
    pred = numeric_threshold(field="x", min=5)
    v = await pred({"x": 1})
    assert v is not None
    assert v.evidence == {"field": "x", "value": 1, "min": 5}


async def test_within_band_skips() -> None:
    pred = numeric_threshold(field="x", min=0, max=10)
    assert await pred({"x": 5}) is None


async def test_missing_field_fires_with_clear_evidence() -> None:
    """A missing field is a bug, not a skip — surface it loudly."""
    pred = numeric_threshold(field="x", max=10)
    v = await pred({})
    assert v is not None
    assert "missing" in v.message.lower()


async def test_neither_min_nor_max_raises_at_factory_time() -> None:
    with pytest.raises(ValueError, match="at least one of"):
        numeric_threshold(field="x")
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/primitives/test_value.py -v`
Expected: ImportError on `compass.policy.primitives.value`.

- [ ] **Step 4: Implement `compass/policy/primitives/value.py`**

```python
"""numeric_threshold — value-band check on a numeric field.

Build-plan §Primitive families — Value gates. Fires when ``value`` is
below ``min`` or above ``max``. Both bounds are inclusive at the
boundary (equal-to-max passes — banking thresholds are typically
"strictly greater than", and rounded values land on the boundary).

Phase: pre_action_proposal.
"""

from __future__ import annotations

from typing import Any

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("numeric_threshold")
def numeric_threshold(
    *,
    field: str,
    min: int | float | None = None,
    max: int | float | None = None,
):
    """Factory. Returns a sync predicate that fails on out-of-band values."""
    if min is None and max is None:
        raise ValueError(
            "numeric_threshold: at least one of min= or max= must be set "
            "(an open-ended band evaluates nothing)."
        )

    def check(ctx: dict[str, Any]) -> Violation | None:
        value = resolve_dotted(ctx, field)
        if value is MISSING:
            return Violation(
                rule_id="",
                message=f"{field} missing from context",
                evidence={"field": field, "reason": "missing"},
            )
        if max is not None and value > max:
            return Violation(
                rule_id="",
                message=f"{field}={value} exceeds max {max}",
                evidence={"field": field, "value": value, "max": max},
            )
        if min is not None and value < min:
            return Violation(
                rule_id="",
                message=f"{field}={value} below min {min}",
                evidence={"field": field, "value": value, "min": min},
            )
        return None

    return check
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/primitives/test_value.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add compass/policy/primitives/__init__.py compass/policy/primitives/value.py tests/compass/policy/primitives/__init__.py tests/compass/policy/primitives/test_value.py
git commit -m "feat(stage-5): numeric_threshold primitive"
```

---

## Task 10: `entity_status_equals` primitive

**Files:**
- Create: `compass/policy/primitives/identity.py`
- Create: `tests/compass/policy/primitives/test_identity.py`

- [ ] **Step 1: Write the failing test `tests/compass/policy/primitives/test_identity.py`**

```python
"""entity_status_equals — fires when an entity's status field doesn't match."""

from __future__ import annotations

from compass.policy.primitives.identity import entity_status_equals


async def test_matching_status_skips() -> None:
    pred = entity_status_equals(
        field="resolved_entities.customer.kyc_status", expected_status="verified",
    )
    ctx = {"resolved_entities": {"customer": {"kyc_status": "verified"}}}
    assert await pred(ctx) is None


async def test_mismatched_status_fires() -> None:
    pred = entity_status_equals(
        field="resolved_entities.customer.kyc_status", expected_status="verified",
    )
    ctx = {"resolved_entities": {"customer": {"kyc_status": "pending"}}}
    v = await pred(ctx)
    assert v is not None
    assert v.evidence["expected"] == "verified"
    assert v.evidence["actual"] == "pending"


async def test_missing_path_skips_silently() -> None:
    """No customer in resolved_entities = the agent didn't query one;
    other rules (require_existing_entity) handle missing entities."""
    pred = entity_status_equals(
        field="resolved_entities.customer.kyc_status", expected_status="verified",
    )
    assert await pred({"resolved_entities": {}}) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/primitives/test_identity.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `compass/policy/primitives/identity.py`**

```python
"""entity_status_equals — fires when an entity's status field is not the expected value.

Build-plan §Primitive families — Identity gates. Used for KYC checks,
account-status checks, etc. The convention for "the entity wasn't
queried" is to SKIP rather than fire — separation of concerns from
require_existing_entity, which is the rule that catches absence.

Phase: pre_action_proposal.
"""

from __future__ import annotations

from typing import Any

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("entity_status_equals")
def entity_status_equals(*, field: str, expected_status: str):
    """Returns a predicate that fails if field's value != expected_status.

    Path MISSING is treated as "skip" — different from
    numeric_threshold, where missing == fire. Status checks attach to
    optional resolved-entity sub-paths; "customer wasn't queried" is
    not the same as "customer.kyc_status is bad".
    """

    def check(ctx: dict[str, Any]) -> Violation | None:
        actual = resolve_dotted(ctx, field)
        if actual is MISSING:
            return None
        if actual == expected_status:
            return None
        return Violation(
            rule_id="",
            message=f"{field}={actual!r}, expected {expected_status!r}",
            evidence={"field": field, "expected": expected_status, "actual": actual},
        )

    return check
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/primitives/test_identity.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add compass/policy/primitives/identity.py tests/compass/policy/primitives/test_identity.py
git commit -m "feat(stage-5): entity_status_equals primitive"
```

---

## Task 11: `require_existing_entity` primitive

**Files:**
- Create: `compass/policy/primitives/resolution.py`
- Create: `tests/compass/policy/primitives/test_resolution.py`

- [ ] **Step 1: Write the failing test**

```python
"""require_existing_entity — fires when the resolved entity is missing/None."""

from __future__ import annotations

from compass.policy.primitives.resolution import require_existing_entity


async def test_entity_present_skips() -> None:
    pred = require_existing_entity(
        field="resolved_entities.customer", entity_type="customer",
    )
    ctx = {"resolved_entities": {"customer": {"id": "cust_alpha", "name": "Acme"}}}
    assert await pred(ctx) is None


async def test_entity_none_fires() -> None:
    pred = require_existing_entity(
        field="resolved_entities.customer", entity_type="customer",
    )
    ctx = {"resolved_entities": {"customer": None}}
    v = await pred(ctx)
    assert v is not None
    assert v.evidence["entity_type"] == "customer"


async def test_entity_missing_fires() -> None:
    pred = require_existing_entity(
        field="resolved_entities.customer", entity_type="customer",
    )
    v = await pred({"resolved_entities": {}})
    assert v is not None
    assert "customer" in v.message.lower()


async def test_entity_empty_dict_fires() -> None:
    pred = require_existing_entity(
        field="resolved_entities.customer", entity_type="customer",
    )
    v = await pred({"resolved_entities": {"customer": {}}})
    assert v is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/primitives/test_resolution.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `compass/policy/primitives/resolution.py`**

```python
"""require_existing_entity — fires when a required entity isn't resolved.

Build-plan §Primitive families — Resolution gates. The agent looked up
a customer (or contract, etc.) via MCP; the workflow's context-builder
projected the result into resolved_entities; this rule fires if the
projection failed.

Phase: pre_action_proposal.
"""

from __future__ import annotations

from typing import Any

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("require_existing_entity")
def require_existing_entity(*, field: str, entity_type: str):
    """Returns a predicate that fails when field is missing, None, or empty.

    "Empty dict" counts as absence — a customer record without an id is
    not a customer for our purposes. List entities aren't supported by
    this primitive at v0.1; use a different primitive for those.
    """

    def check(ctx: dict[str, Any]) -> Violation | None:
        value = resolve_dotted(ctx, field)
        if value is MISSING or value is None or value == {}:
            return Violation(
                rule_id="",
                message=f"required {entity_type} not resolved at {field}",
                evidence={"field": field, "entity_type": entity_type},
            )
        return None

    return check
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/primitives/test_resolution.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add compass/policy/primitives/resolution.py tests/compass/policy/primitives/test_resolution.py
git commit -m "feat(stage-5): require_existing_entity primitive"
```

---

## Task 12: `require_evidence_citation` primitive

**Files:**
- Create: `compass/policy/primitives/evidence.py`
- Create: `tests/compass/policy/primitives/test_evidence.py`

- [ ] **Step 1: Write the failing test**

```python
"""require_evidence_citation — fires when source_refs are missing/empty."""

from __future__ import annotations

from compass.policy.primitives.evidence import require_evidence_citation


async def test_all_lines_cited_skips() -> None:
    pred = require_evidence_citation(field="proposal.line_items[*].source_refs")
    ctx = {"proposal": {"line_items": [
        {"source_refs": ["te_1"]},
        {"source_refs": ["te_2", "te_3"]},
    ]}}
    assert await pred(ctx) is None


async def test_one_line_empty_refs_fires() -> None:
    pred = require_evidence_citation(field="proposal.line_items[*].source_refs")
    ctx = {"proposal": {"line_items": [
        {"source_refs": ["te_1"]},
        {"source_refs": []},
    ]}}
    v = await pred(ctx)
    assert v is not None
    assert v.evidence["empty_line_indices"] == [1]


async def test_missing_path_fires_loudly() -> None:
    pred = require_evidence_citation(field="proposal.line_items[*].source_refs")
    v = await pred({"proposal": {}})
    assert v is not None
    assert "missing" in v.message.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/primitives/test_evidence.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `compass/policy/primitives/evidence.py`**

```python
"""require_evidence_citation — every list element at field must be truthy.

Build-plan §Primitive families — Evidence / citation gates. Specifically
shaped for ``proposal.line_items[*].source_refs`` and similar paths
that resolve to a list of lists. Each inner list must be non-empty.

Phase: pre_action_proposal.
"""

from __future__ import annotations

from typing import Any

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("require_evidence_citation")
def require_evidence_citation(*, field: str):
    """Returns a predicate that fails if any inner list at field is empty."""

    def check(ctx: dict[str, Any]) -> Violation | None:
        values = resolve_dotted(ctx, field)
        if values is MISSING:
            return Violation(
                rule_id="",
                message=f"{field} missing from context",
                evidence={"field": field, "reason": "missing"},
            )
        if not isinstance(values, list):
            return Violation(
                rule_id="",
                message=f"{field} resolved to non-list",
                evidence={"field": field, "reason": "non-list"},
            )
        empty_indices = [i for i, v in enumerate(values) if not v]
        if empty_indices:
            return Violation(
                rule_id="",
                message=f"{field}: lines {empty_indices} have no citations",
                evidence={"field": field, "empty_line_indices": empty_indices},
            )
        return None

    return check
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/primitives/test_evidence.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add compass/policy/primitives/evidence.py tests/compass/policy/primitives/test_evidence.py
git commit -m "feat(stage-5): require_evidence_citation primitive"
```

---

## Task 13: Approval-phase primitives

**Files:**
- Create: `compass/policy/primitives/approval.py`
- Create: `tests/compass/policy/primitives/test_approval.py`

- [ ] **Step 1: Write the failing test**

```python
"""Approval-phase primitives: silent-modification and policy-drift detection."""

from __future__ import annotations

from compass.policy.primitives.approval import (
    prohibit_policy_drift_after_confirmation,
    prohibit_silent_modification_after_confirmation,
)


# ---- prohibit_silent_modification_after_confirmation ----


async def test_no_modification_skips() -> None:
    """Stage-5 happy path: proposal unchanged across approval wait."""
    pred = prohibit_silent_modification_after_confirmation()
    ctx = {
        "proposal": {"customer_id": "cust_alpha", "total_cents": 80000},
        "proposal_hash_at_proposal": "abc123",
    }
    # Inject current hash to match - test relies on the primitive using
    # hash_proposal(ctx["proposal"]) and comparing.
    ctx["__test_current_proposal_hash__"] = "abc123"
    assert await pred(ctx) is None


async def test_modification_detected_fires() -> None:
    pred = prohibit_silent_modification_after_confirmation()
    ctx = {
        "proposal": {"customer_id": "cust_alpha", "total_cents": 99999},
        "proposal_hash_at_proposal": "abc123",
        "__test_current_proposal_hash__": "def456",
    }
    v = await pred(ctx)
    assert v is not None
    assert v.evidence["hash_at_proposal"] == "abc123"
    assert v.evidence["hash_at_execute"] == "def456"


# ---- prohibit_policy_drift_after_confirmation ----


async def test_no_drift_skips() -> None:
    pred = prohibit_policy_drift_after_confirmation()
    ctx = {
        "policy_hash_at_proposal": "abc123",
        "__test_current_policy_hash__": "abc123",
    }
    assert await pred(ctx) is None


async def test_drift_detected_fires() -> None:
    pred = prohibit_policy_drift_after_confirmation()
    ctx = {
        "policy_hash_at_proposal": "abc123",
        "__test_current_policy_hash__": "def456",
    }
    v = await pred(ctx)
    assert v is not None
    assert v.evidence["hash_at_proposal"] == "abc123"
    assert v.evidence["hash_now"] == "def456"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/primitives/test_approval.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `compass/policy/primitives/approval.py`**

```python
"""Approval-phase primitives — drift detection.

Build-plan §Primitive families — Approval gates. Both rules fire at
pre_execute. Both read pre-computed hashes from the context (the
workflow puts hash_at_proposal in there during the pre_action_proposal
phase) and compare to a "current" hash also placed in context.

The test stubs accept __test_current_*_hash__ keys so unit tests don't
need a live workflow; production callers populate the production keys
``current_proposal_hash`` / ``current_policy_hash`` instead.

Phase: pre_execute.
"""

from __future__ import annotations

from typing import Any

from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("prohibit_silent_modification_after_confirmation")
def prohibit_silent_modification_after_confirmation():
    """Returns a predicate that fails when the proposal changed after approval.

    Compares ``proposal_hash_at_proposal`` (captured by the workflow at
    pre_action_proposal time) to ``current_proposal_hash``
    (recomputed at pre_execute time). At Stage 5 these always match
    (no UI yet); Stage 12 makes this load-bearing when the approval UI
    can edit the proposal.
    """

    def check(ctx: dict[str, Any]) -> Violation | None:
        at_proposal = ctx.get("proposal_hash_at_proposal")
        current = ctx.get("__test_current_proposal_hash__") or ctx.get(
            "current_proposal_hash"
        )
        if at_proposal is None or current is None:
            return None
        if at_proposal == current:
            return None
        return Violation(
            rule_id="",
            message="proposal hash differs between approval and execute",
            evidence={
                "hash_at_proposal": at_proposal,
                "hash_at_execute": current,
            },
        )

    return check


@primitive("prohibit_policy_drift_after_confirmation")
def prohibit_policy_drift_after_confirmation():
    """Returns a predicate that fires when the policy changed during approval wait.

    Compares ``policy_hash_at_proposal`` to ``current_policy_hash``.
    A worker restart that loaded new RULES between the agent's draft
    and the human's approval drives this. ESCALATE semantics (not
    BLOCK) — the human should re-approve, not silently get rejected.
    """

    def check(ctx: dict[str, Any]) -> Violation | None:
        at_proposal = ctx.get("policy_hash_at_proposal")
        current = ctx.get("__test_current_policy_hash__") or ctx.get(
            "current_policy_hash"
        )
        if at_proposal is None or current is None:
            return None
        if at_proposal == current:
            return None
        return Violation(
            rule_id="",
            message="policy hash drifted between proposal and execute",
            evidence={
                "hash_at_proposal": at_proposal,
                "hash_now": current,
            },
        )

    return check
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/primitives/test_approval.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add compass/policy/primitives/approval.py tests/compass/policy/primitives/test_approval.py
git commit -m "feat(stage-5): approval primitives — silent modification + policy drift"
```

---

## Task 14: Audit-validation primitives

**Files:**
- Create: `compass/policy/primitives/audit.py`
- Create: `tests/compass/policy/primitives/test_audit.py`

- [ ] **Step 1: Write the failing test**

```python
"""audit_validation primitives: log_policy_version, log_data_sources_consulted."""

from __future__ import annotations

from compass.policy.primitives.audit import (
    log_data_sources_consulted,
    log_policy_version,
)


# ---- log_policy_version ----


async def test_policy_version_present_skips() -> None:
    pred = log_policy_version()
    assert await pred({"policy_hash": "abc123"}) is None


async def test_policy_version_missing_fires() -> None:
    pred = log_policy_version()
    v = await pred({})
    assert v is not None
    assert "policy_hash" in v.message


async def test_policy_version_empty_fires() -> None:
    pred = log_policy_version()
    v = await pred({"policy_hash": ""})
    assert v is not None


# ---- log_data_sources_consulted ----


async def test_tool_calls_present_skips() -> None:
    pred = log_data_sources_consulted()
    ctx = {"tool_calls": [{"tool_name": "list_customers"}]}
    assert await pred(ctx) is None


async def test_tool_calls_empty_fires() -> None:
    pred = log_data_sources_consulted()
    v = await pred({"tool_calls": []})
    assert v is not None


async def test_tool_calls_missing_fires() -> None:
    pred = log_data_sources_consulted()
    v = await pred({})
    assert v is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/compass/policy/primitives/test_audit.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `compass/policy/primitives/audit.py`**

```python
"""audit_validation primitives — completeness checks on the terminal row.

Both primitives fire only on workflow bugs (a terminal row without
policy_hash or without consulted tool calls). Production behavior is
that they never fire; they exist as defect detectors.

Phase: audit_validation.
"""

from __future__ import annotations

from typing import Any

from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("log_policy_version")
def log_policy_version():
    """Returns a predicate that fails if context has no non-empty policy_hash."""

    def check(ctx: dict[str, Any]) -> Violation | None:
        h = ctx.get("policy_hash")
        if not h:
            return Violation(
                rule_id="",
                message="audit candidate has no policy_hash",
                evidence={"policy_hash_present": False},
            )
        return None

    return check


@primitive("log_data_sources_consulted")
def log_data_sources_consulted():
    """Returns a predicate that fails when tool_calls is empty/missing."""

    def check(ctx: dict[str, Any]) -> Violation | None:
        calls = ctx.get("tool_calls") or []
        if not calls:
            return Violation(
                rule_id="",
                message="audit candidate has no tool_calls (agent queried nothing)",
                evidence={"tool_call_count": len(calls)},
            )
        return None

    return check
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/compass/policy/primitives/test_audit.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add compass/policy/primitives/audit.py tests/compass/policy/primitives/test_audit.py
git commit -m "feat(stage-5): audit-validation primitives"
```

---

## Task 15: `attach_to_agent` (wired empty at Stage 5)

**Files:**
- Create: `compass/policy/agent.py`
- Modify: `compass/policy/__init__.py`

No new tests at Stage 5 — `attach_to_agent` is called with zero input/output rules in send-invoice. Stage 6 (scope gate) is what exercises the non-empty path; defer test authorship to then.

- [ ] **Step 1: Implement `compass/policy/agent.py`**

```python
"""attach_to_agent — wire compass rules as OpenAI Agents SDK guardrails.

Stage 5: policies/send_invoice.py has zero rules at input_validation
or output_validation phases, so this function attaches no-op
callbacks. The mechanism is wired so Stage 6's scope-gate
input_validation rules drop in without further engine work.

When real rules land, the callback opens its own DB connection per
invocation (auto-wrapped activities don't share workflow-level state).
``sink_factory`` lets the caller customize sink construction; if None,
the callback uses NullSink and any rule firing surfaces only via the
OpenAI Agents SDK's tripwire exception.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from agents import (
    Agent,
    GuardrailFunctionOutput,
    InputGuardrailTripwireTriggered,
    OutputGuardrailTripwireTriggered,
    RunContextWrapper,
    input_guardrail,
    output_guardrail,
)

from compass.policy.engine import evaluate
from compass.policy.sink import NullSink, Sink
from compass.policy.types import Phase, Rule

T = TypeVar("T")


def attach_to_agent(
    agent: Agent[T],
    rules: Sequence[Rule],
    *,
    sink_factory: Callable[[], Awaitable[Sink]] | None = None,
) -> Agent[T]:
    """Bundle rules for input/output_validation as agent guardrails.

    Returns the agent for chaining; mutates it in place.

    No-op when ``rules`` has no entries at the relevant phases.
    """
    input_rules = [r for r in rules if r.phase is Phase.input_validation]
    output_rules = [r for r in rules if r.phase is Phase.output_validation]

    if input_rules:
        @input_guardrail  # type: ignore[misc]
        async def _input_gate(
            _ctx: RunContextWrapper[Any], _agent: Agent[T], input_value: Any,
        ) -> GuardrailFunctionOutput:
            sink: Sink = await sink_factory() if sink_factory else NullSink()
            decision = await evaluate(
                input_rules, Phase.input_validation,
                {"user_message": input_value}, sink=sink,
            )
            return GuardrailFunctionOutput(
                output_info={"rule_ids_fired": list(decision.rule_ids_fired)},
                tripwire_triggered=not decision.permit,
            )

        agent.input_guardrails = [*agent.input_guardrails, _input_gate]

    if output_rules:
        @output_guardrail  # type: ignore[misc]
        async def _output_gate(
            _ctx: RunContextWrapper[Any], _agent: Agent[T], output: Any,
        ) -> GuardrailFunctionOutput:
            sink: Sink = await sink_factory() if sink_factory else NullSink()
            ctx_dict = (
                output.model_dump() if hasattr(output, "model_dump")
                else {"output": output}
            )
            decision = await evaluate(
                output_rules, Phase.output_validation,
                {"proposal": ctx_dict}, sink=sink,
            )
            return GuardrailFunctionOutput(
                output_info={"rule_ids_fired": list(decision.rule_ids_fired)},
                tripwire_triggered=not decision.permit,
            )

        agent.output_guardrails = [*agent.output_guardrails, _output_gate]

    return agent


__all__ = [
    "InputGuardrailTripwireTriggered",
    "OutputGuardrailTripwireTriggered",
    "attach_to_agent",
]
```

- [ ] **Step 2: Export from `compass/policy/__init__.py`**

Add:

```python
from compass.policy.agent import attach_to_agent
```

And `"attach_to_agent"` to `__all__`.

- [ ] **Step 3: Verify no existing tests broke**

Run: `uv run pytest tests/compass/ -v`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add compass/policy/agent.py compass/policy/__init__.py
git commit -m "feat(stage-5): attach_to_agent wires input/output guardrails (empty at v0.1)"
```

---

## Task 16: App-specific primitives in `workflows/send_invoice/primitives.py`

The four Billing integrity primitives. These compose on top of the
context shape produced by the workflow's context builder, so test
fixtures use realistic shapes drawn from the spec.

**Files:**
- Create: `workflows/send_invoice/primitives.py`

The unit tests for these primitives live in
`tests/policies/test_send_invoice_rules.py` (Task 22) because they're
exercised via `RULES`. No standalone primitive test file — the contract
they implement is "given a policy context with this shape, fire/skip
correctly", and that's what the RULES tests assert.

- [ ] **Step 1: Implement `workflows/send_invoice/primitives.py`**

```python
"""Billing integrity primitives — application-specific to send_invoice.

Self-register at import via @primitive. Imported by
policies/send_invoice.py (which is itself imported by the
evaluate_policy activity) — that's the chain that populates the
registry before the first hash_rules() call.

All four primitives are pre_action_proposal phase, BLOCK severity.
They read the agent's resolved entities and proposal from the context
dict; the workflow's context.py module is responsible for projecting
those into the expected shape.
"""

from __future__ import annotations

from typing import Any

from compass.policy.paths import MISSING, resolve_dotted
from compass.policy.registry import primitive
from compass.policy.types import Violation


@primitive("require_amount_source")
def require_amount_source():
    """Every line item carries a valid source_type + non-empty source_refs.

    Domain-specific instantiation of require_evidence_citation. The
    valid source_type set matches workflows/send_invoice/types.py
    LineItemSourceType.
    """
    VALID = {"contract", "rate_card", "time_tracking", "user_specified"}

    def check(ctx: dict[str, Any]) -> Violation | None:
        lines = resolve_dotted(ctx, "proposal.line_items")
        if lines is MISSING or not isinstance(lines, list):
            return Violation(
                rule_id="",
                message="proposal.line_items missing or not a list",
                evidence={"reason": "missing_line_items"},
            )
        for i, line in enumerate(lines):
            stype = line.get("source_type")
            if stype not in VALID:
                return Violation(
                    rule_id="",
                    message=f"line {i} has invalid source_type {stype!r}",
                    evidence={"line_no": i, "source_type": stype, "valid": sorted(VALID)},
                )
            refs = line.get("source_refs") or []
            if not refs:
                return Violation(
                    rule_id="",
                    message=f"line {i} has empty source_refs",
                    evidence={"line_no": i},
                )
        return None

    return check


@primitive("contract_consistency_check")
def contract_consistency_check():
    """When a contract is resolved, proposal currency must match it.

    Stage 5 scope: currency comparison. Future scope: billing-structure
    match (flat-fee SOW vs. T&M etc.). The contract reference itself is
    optional — if no contract was queried, the rule skips.
    """

    def check(ctx: dict[str, Any]) -> Violation | None:
        contract = resolve_dotted(ctx, "resolved_entities.contract")
        if contract is MISSING or contract is None:
            return None
        proposal = resolve_dotted(ctx, "proposal")
        if proposal is MISSING:
            return Violation(
                rule_id="", message="proposal missing", evidence={},
            )
        proposal_currency = proposal.get("currency")
        contract_currency = contract.get("currency")
        if proposal_currency != contract_currency:
            return Violation(
                rule_id="",
                message=(
                    f"proposal currency {proposal_currency!r} does not match "
                    f"contract currency {contract_currency!r}"
                ),
                evidence={
                    "proposal_currency": proposal_currency,
                    "contract_currency": contract_currency,
                    "contract_id": contract.get("id"),
                },
            )
        return None

    return check


@primitive("prohibit_exceed_contract_cap")
def prohibit_exceed_contract_cap():
    """When the contract has a monthly_hour_cap, proposal hours must not exceed it.

    Sums ``quantity_micros / 1e6`` across time_tracking line items only.
    Other source types don't bill against the cap.
    """

    def check(ctx: dict[str, Any]) -> Violation | None:
        contract = resolve_dotted(ctx, "resolved_entities.contract")
        if contract is MISSING or contract is None:
            return None
        cap = contract.get("monthly_hour_cap")
        if cap is None:
            return None
        lines = resolve_dotted(ctx, "proposal.line_items")
        if lines is MISSING or not isinstance(lines, list):
            return None
        hours = sum(
            line.get("quantity_micros", 0) / 1_000_000
            for line in lines
            if line.get("source_type") == "time_tracking"
        )
        if hours > cap:
            return Violation(
                rule_id="",
                message=f"proposal hours {hours} exceed contract cap {cap}",
                evidence={
                    "proposal_hours": hours,
                    "contract_cap": cap,
                    "contract_id": contract.get("id"),
                },
            )
        return None

    return check


@primitive("currency_consistency_check")
def currency_consistency_check():
    """All line items must share the proposal's currency.

    Line items don't carry their own currency in InvoiceProposal at
    v0.1 — Pydantic enforces that the proposal has one currency for
    the whole invoice. This rule guards against a future regression
    where line-level currency is added and falls out of sync. Stage 5
    additionally checks: if any cited rate_card has a different
    currency, fire.
    """

    def check(ctx: dict[str, Any]) -> Violation | None:
        proposal = resolve_dotted(ctx, "proposal")
        if proposal is MISSING:
            return None
        proposal_currency = proposal.get("currency")
        rate_cards = resolve_dotted(ctx, "resolved_entities.rate_card_entries")
        if rate_cards is MISSING or not isinstance(rate_cards, list):
            return None
        mismatched = [
            rc.get("id") for rc in rate_cards
            if rc.get("currency") != proposal_currency
        ]
        if mismatched:
            return Violation(
                rule_id="",
                message=(
                    f"rate cards {mismatched} have currency != proposal "
                    f"currency {proposal_currency!r}"
                ),
                evidence={
                    "proposal_currency": proposal_currency,
                    "mismatched_rate_card_ids": mismatched,
                },
            )
        return None

    return check
```

- [ ] **Step 2: Verify the primitives register cleanly** (smoke test)

```bash
uv run python -c "
import workflows.send_invoice.primitives  # registers
from compass.policy.registry import list_primitives
names = set(list_primitives().keys())
required = {'require_amount_source', 'contract_consistency_check',
            'prohibit_exceed_contract_cap', 'currency_consistency_check'}
assert required.issubset(names), names - required
print('OK', sorted(names))
"
```

Expected: prints `OK [...names...]`.

- [ ] **Step 3: Commit**

```bash
git add workflows/send_invoice/primitives.py
git commit -m "feat(stage-5): billing integrity primitives — app-specific"
```

---

## Task 17: `workflows/send_invoice/context.py` — RunResult extractors

Pure functions that build the policy context dict from `Runner.run`'s
`RunResult`. No I/O, safe to call from workflow code.

**Files:**
- Create: `workflows/send_invoice/context.py`
- Create: `tests/workflows/send_invoice/test_context.py`

- [ ] **Step 1: Write the failing test `tests/workflows/send_invoice/test_context.py`**

```python
"""Pure-function tests for workflows/send_invoice/context.py.

No Temporal, no OpenAI, no MCP. Synthetic RunResult-shaped inputs.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from workflows.send_invoice.context import (
    extract_reasoning_text,
    extract_tool_calls,
    hash_proposal,
    project_resolved_entities,
)


# ---------------------------------------------------------------------
# Synthetic RunResult builders
# ---------------------------------------------------------------------


def _tool_call_item(name: str, args: dict[str, Any], output: Any) -> SimpleNamespace:
    """Build a SimpleNamespace shaped like the SDK's ToolCallOutputItem.

    The SDK exposes ``raw_item.name``, ``raw_item.arguments``, and
    ``raw_item.output`` (or similar) on tool-call items; the extractor
    code does duck-typed access and converts JSON strings if needed.
    """
    raw = SimpleNamespace(
        name=name,
        arguments=json.dumps(args),
        output=json.dumps(output) if not isinstance(output, str) else output,
    )
    return SimpleNamespace(type="tool_call_output_item", raw_item=raw)


def _message_item(role: str, text: str) -> SimpleNamespace:
    raw = SimpleNamespace(role=role, content=text)
    return SimpleNamespace(type="message_output_item", raw_item=raw)


def _run_result(items: list[SimpleNamespace]) -> SimpleNamespace:
    return SimpleNamespace(new_items=items)


# ---------------------------------------------------------------------
# extract_tool_calls
# ---------------------------------------------------------------------


def test_extract_tool_calls_returns_one_per_call() -> None:
    rr = _run_result([
        _tool_call_item("list_customers", {"name": "Acme"},
                        [{"id": "cust_alpha"}]),
        _tool_call_item("get_active_contract",
                        {"customer_id": "cust_alpha"},
                        {"id": "ct_alpha"}),
    ])
    calls = extract_tool_calls(rr)
    assert len(calls) == 2
    assert calls[0]["tool_name"] == "list_customers"
    assert calls[1]["tool_name"] == "get_active_contract"


def test_extract_tool_calls_strips_non_tool_items() -> None:
    rr = _run_result([
        _message_item("assistant", "thinking..."),
        _tool_call_item("list_customers", {}, []),
    ])
    calls = extract_tool_calls(rr)
    assert [c["tool_name"] for c in calls] == ["list_customers"]


def test_extract_tool_calls_handles_empty() -> None:
    assert extract_tool_calls(_run_result([])) == []


# ---------------------------------------------------------------------
# project_resolved_entities
# ---------------------------------------------------------------------


def test_project_customer_from_list_customers() -> None:
    calls = [
        {
            "tool_name": "list_customers",
            "args": {"name_q": "Acme"},
            "result": [{"id": "cust_alpha", "name": "Acme",
                        "kyc_status": "verified"}],
        },
    ]
    entities = project_resolved_entities(calls)
    assert entities["customer"]["id"] == "cust_alpha"
    assert entities["customer"]["kyc_status"] == "verified"


def test_project_customer_from_get_customer() -> None:
    calls = [
        {
            "tool_name": "get_customer",
            "args": {"customer_id": "cust_alpha"},
            "result": {"id": "cust_alpha", "kyc_status": "verified"},
        },
    ]
    entities = project_resolved_entities(calls)
    assert entities["customer"]["id"] == "cust_alpha"


def test_project_contract_from_get_active_contract() -> None:
    calls = [
        {
            "tool_name": "get_active_contract",
            "args": {"customer_id": "cust_alpha"},
            "result": {"id": "ct_alpha", "currency": "USD",
                       "monthly_hour_cap": 40},
        },
    ]
    entities = project_resolved_entities(calls)
    assert entities["contract"]["id"] == "ct_alpha"


def test_project_contract_absent_when_not_queried() -> None:
    entities = project_resolved_entities([])
    assert entities.get("contract") is None
    assert entities.get("customer") is None


def test_project_rate_cards_collected() -> None:
    calls = [
        {"tool_name": "get_rate_card", "args": {"role": "SA"},
         "result": {"id": "rc_sa", "list_amount_cents": 40000,
                    "currency": "USD"}},
        {"tool_name": "get_rate_card", "args": {"role": "PM"},
         "result": {"id": "rc_pm", "list_amount_cents": 25000,
                    "currency": "USD"}},
    ]
    entities = project_resolved_entities(calls)
    assert {rc["id"] for rc in entities["rate_card_entries"]} == {"rc_sa", "rc_pm"}


def test_project_time_entries_collected() -> None:
    calls = [
        {"tool_name": "list_time_entries", "args": {},
         "result": [{"id": "te_1", "hours_micros": 2_000_000},
                    {"id": "te_2", "hours_micros": 4_000_000}]},
    ]
    entities = project_resolved_entities(calls)
    assert [te["id"] for te in entities["time_entries"]] == ["te_1", "te_2"]


# ---------------------------------------------------------------------
# extract_reasoning_text
# ---------------------------------------------------------------------


def test_extract_reasoning_joins_assistant_messages() -> None:
    rr = _run_result([
        _message_item("assistant", "looking up customer"),
        _tool_call_item("list_customers", {}, []),
        _message_item("assistant", "found it"),
    ])
    text = extract_reasoning_text(rr)
    assert "looking up customer" in text
    assert "found it" in text


# ---------------------------------------------------------------------
# hash_proposal
# ---------------------------------------------------------------------


def test_hash_proposal_deterministic() -> None:
    p = {"customer_id": "x", "total_cents": 80000}
    assert hash_proposal(p) == hash_proposal(p)


def test_hash_proposal_key_order_invariant() -> None:
    p1 = {"a": 1, "b": 2}
    p2 = {"b": 2, "a": 1}
    assert hash_proposal(p1) == hash_proposal(p2)


def test_hash_proposal_sensitive_to_values() -> None:
    assert hash_proposal({"a": 1}) != hash_proposal({"a": 2})
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/workflows/send_invoice/test_context.py -v`
Expected: ImportError on `workflows.send_invoice.context`.

- [ ] **Step 3: Implement `workflows/send_invoice/context.py`**

```python
"""Pure functions that project Runner.run's RunResult into a policy context.

The OpenAI Agents SDK's RunResult exposes ``new_items`` — a list of
typed items including tool-call outputs and assistant messages. We
extract the bits the policy engine needs:

* tool_calls — for evidence-citation rules and audit_validation
* resolved_entities — derived from specific tool names (the
  workflow's MCP is closed-set; we know which tool returns which type)
* reasoning_text — concatenated assistant messages, for future
  reasoning-trace audit checks
* hash_proposal — sha256 of the canonical proposal JSON, captured by
  the workflow for drift detection at pre_execute

No I/O. Workflow code calls these directly between Runner.run and the
evaluate_policy activity invocation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def extract_tool_calls(run_result: Any) -> list[dict[str, Any]]:
    """Return [{tool_name, args, result}, ...] for each tool call."""
    out: list[dict[str, Any]] = []
    items = getattr(run_result, "new_items", None) or []
    for item in items:
        # The SDK uses ``type="tool_call_output_item"`` for tool results.
        if getattr(item, "type", None) != "tool_call_output_item":
            continue
        raw = getattr(item, "raw_item", item)
        name = getattr(raw, "name", None)
        if not name:
            continue
        args_raw = getattr(raw, "arguments", None)
        output_raw = getattr(raw, "output", None)
        out.append({
            "tool_name": name,
            "args": _maybe_json(args_raw),
            "result": _maybe_json(output_raw),
        })
    return out


def project_resolved_entities(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce tool calls into the resolved-entities snapshot the rules use.

    The mapping from tool name → entity slot is closed-set. Adding a
    new tool that should populate resolved_entities requires updating
    this function — that's intentional; the projection contract is
    workflow-specific.
    """
    entities: dict[str, Any] = {
        "customer": None,
        "contract": None,
        "rate_card_entries": [],
        "time_entries": [],
    }
    for call in tool_calls:
        name = call.get("tool_name")
        result = call.get("result")
        if name == "list_customers" and isinstance(result, list) and result:
            entities["customer"] = result[0]
        elif name == "get_customer" and isinstance(result, dict):
            entities["customer"] = result
        elif name == "get_active_contract" and isinstance(result, dict):
            entities["contract"] = result
        elif name == "get_rate_card" and isinstance(result, dict):
            entities["rate_card_entries"].append(result)
        elif name == "list_time_entries" and isinstance(result, list):
            entities["time_entries"].extend(result)
    return entities


def extract_reasoning_text(run_result: Any) -> str:
    """Concatenate every assistant message in the run's new_items."""
    parts: list[str] = []
    items = getattr(run_result, "new_items", None) or []
    for item in items:
        if getattr(item, "type", None) != "message_output_item":
            continue
        raw = getattr(item, "raw_item", item)
        if getattr(raw, "role", None) != "assistant":
            continue
        content = getattr(raw, "content", None)
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def hash_proposal(proposal: dict[str, Any]) -> str:
    """Stable sha256 hex of the proposal dict.

    Used as proposal_hash_at_proposal by the workflow, compared at
    pre_execute by prohibit_silent_modification_after_confirmation.
    """
    canon = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _maybe_json(value: Any) -> Any:
    """Decode strings that look like JSON; pass through other types."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/workflows/send_invoice/test_context.py -v`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add workflows/send_invoice/context.py tests/workflows/send_invoice/test_context.py
git commit -m "feat(stage-5): context.py — RunResult → policy context dict"
```

---

## Task 18: Update `workflows/send_invoice/types.py`

Extend `EvaluatePolicyInput` with `phase` + `context`. Rename the
existing `PolicyDecision` Pydantic model to `PolicyDecisionPayload`
and add the new fields.

**Files:**
- Modify: `workflows/send_invoice/types.py`

- [ ] **Step 1: Read the current file** so the Edit below has the right
  context.

Run: `uv run cat workflows/send_invoice/types.py`
(or view in the editor)

- [ ] **Step 2: Edit `workflows/send_invoice/types.py` — rename and extend**

At the bottom of the file, replace the `PolicyDecision` class:

```python
class PolicyDecisionPayload(BaseModel):
    """Activity return from ``evaluate_policy`` at Stage 5.

    Replaces the Stage-4 ``PolicyDecision`` stub. The compass
    ``Decision`` type lives in compass.policy.types; this is the
    activity-boundary serialization the workflow consumes.
    """

    model_config = ConfigDict(extra="forbid")

    permit: bool
    policy_hash: str
    rule_ids_fired: list[str] = []
    escalations: list[dict] = []
    next_sequence_no: int
```

Remove the old `PolicyDecision` class. Tests in `test_workflow.py`
import `PolicyDecision`; those tests are tagged Stage-4-only and the
Stage 5 workflow tests use `PolicyDecisionPayload` instead. The
Stage-4 tests in `tests/workflows/send_invoice/test_workflow.py` need
their import updated too — handle that here:

- [ ] **Step 3: Update import in existing Stage-4 tests**

Modify `tests/workflows/send_invoice/test_workflow.py` — change every
import of `PolicyDecision` to `PolicyDecisionPayload`. The existing
tests don't construct one directly, but the import must resolve.

If grep returns nothing, this step is a no-op:

```bash
grep -rn "PolicyDecision\b" tests/ workflows/ scripts/ | grep -v PolicyDecisionPayload
```

Patch any matches to use `PolicyDecisionPayload`.

- [ ] **Step 4: Verify existing tests still pass**

Run: `uv run pytest tests/workflows/send_invoice/test_workflow.py -v`
Expected: 4 passed (Stage-4 happy/declined/timeout/duplicate).

- [ ] **Step 5: Commit**

```bash
git add workflows/send_invoice/types.py tests/workflows/send_invoice/test_workflow.py
git commit -m "refactor(stage-5): rename PolicyDecision → PolicyDecisionPayload"
```

---

## Task 19: `policies/send_invoice.py` — the RULES list

**Files:**
- Create: `policies/__init__.py` (empty)
- Create: `policies/send_invoice.py`

- [ ] **Step 1: Create empty `policies/__init__.py`**

```python
```

- [ ] **Step 2: Create `policies/send_invoice.py`**

```python
"""Send-invoice policy at v0.1.

This module is the authoritative policy for SendInvoiceWorkflow.
``RULES`` is hashed once per evaluate_policy invocation and
snapshotted to policy_snapshots; every audit_log row carries the hash.

Rule ids are stable identifiers — they appear in audit_log.rule_id
and in historic queries. Renaming an in-use id breaks audit reads;
treat ids as append-only.

Twelve rules total: eight framework-core primitives plus four
app-specific Billing integrity primitives. Every Billing integrity
rule carries ``must_be_covered=True`` so Stage 10's CI gate catches
dead-code regressions in that family.
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
)

RULES: list[Rule] = [
    # ---- pre_action_proposal — bulk of policy load ----
    Rule(
        id="customer_must_exist",
        phase=Phase.pre_action_proposal,
        predicate=require_existing_entity(
            field="resolved_entities.customer", entity_type="customer",
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
```

- [ ] **Step 3: Smoke-test that RULES loads and hashes**

```bash
uv run python -c "
from policies.send_invoice import RULES
from compass.policy import hash_rules
assert len(RULES) == 12, len(RULES)
print('hash:', hash_rules(RULES))
print('count:', len(RULES))
"
```

Expected: prints `hash: <hex>` and `count: 12`.

- [ ] **Step 4: Commit**

```bash
git add policies/__init__.py policies/send_invoice.py
git commit -m "feat(stage-5): policies/send_invoice.py — 12-rule RULES list"
```

---

## Task 20: Update `workflows/send_invoice/activities.py` — `evaluate_policy` body + `audit_log` extension

Replace the Stage-4 stub. The new `evaluate_policy` switches on phase,
opens a psycopg connection, writes the snapshot, runs `evaluate`, maps
exceptions. The `audit_log` activity grows an `is_terminal_event`
kwarg.

**Files:**
- Modify: `workflows/send_invoice/activities.py`

- [ ] **Step 1: Replace the file**

Open `workflows/send_invoice/activities.py` and replace its contents
with:

```python
"""Side-effect activities for the SendInvoice workflow.

Stage 5:
* ``evaluate_policy`` runs compass.policy.evaluate at the requested
  phase, writes a policy_snapshots row, and maps exceptions to
  Temporal's retry semantics. Switches on phase (pre_action_proposal,
  pre_execute, audit_validation) — one activity, runtime arg.
* ``execute_send`` unchanged from Stage 4.
* ``audit_log`` grows ``is_terminal_event``: when True, runs
  evaluate_audit_validation against the candidate row before insert,
  and writes rule_fired events + the original row in one transaction.

All three remain idempotent under Temporal retries — see
docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md
§Activity failure semantics.
"""

import os
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.exceptions import ApplicationError

from compass.policy import (
    Phase,
    PolicyDecisionError,
    PolicyEngineError,
    Violation,
    evaluate,
    write_policy_snapshot,
)
from compass.policy.audit_sink import AuditLogSink, SequenceAllocator
from compass.policy.sink import InMemorySink
from policies.send_invoice import RULES
from workflows.send_invoice.context import hash_proposal
from workflows.send_invoice.types import (
    ApprovalDecision,
    InvoiceProposal,
    PolicyDecisionPayload,
)


def _dsn() -> str:
    dsn = os.environ.get("COMPASS_PG_DSN")
    if not dsn:
        raise RuntimeError(
            "workflows.send_invoice.activities: COMPASS_PG_DSN must be set."
        )
    return dsn


# ---------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------


@dataclass
class AuditEvent:
    workflow_run_id: str
    sequence_no: int
    phase: str
    event_kind: str
    payload: dict[str, Any]
    decision: str | None = None
    rule_id: str | None = None
    actor: dict[str, Any] | None = None
    # New at Stage 5. When True, evaluate_audit_validation runs against
    # this event's payload before the row is written. Used for the
    # final terminal audit row of the workflow.
    is_terminal_event: bool = False
    # Workflow's current policy_hash (captured at pre_action_proposal).
    # Required when is_terminal_event=True so the audit_validation
    # rules can check log_policy_version.
    policy_hash_for_validation: str | None = None
    # Tool calls + reasoning are passed through for the same reason.
    tool_calls_for_validation: list[dict[str, Any]] = field(default_factory=list)
    reasoning_text_for_validation: str = ""


async def _write_audit_row(
    cur: psycopg.AsyncCursor, event: AuditEvent, *, policy_hash: str | None,
) -> None:
    """Single-row INSERT into audit_log; idempotent via ON CONFLICT."""
    actor_param = Jsonb(event.actor) if event.actor is not None else None
    await cur.execute(
        """
        INSERT INTO audit_log (
            workflow_run_id, sequence_no, phase, event_kind, rule_id,
            policy_hash, decision, actor, payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (workflow_run_id, sequence_no) DO NOTHING
        """,
        (
            event.workflow_run_id,
            event.sequence_no,
            event.phase,
            event.event_kind,
            event.rule_id,
            policy_hash or "unknown",
            event.decision,
            actor_param,
            Jsonb(event.payload),
        ),
    )


@activity.defn
async def audit_log(event: AuditEvent) -> None:
    """Append one (or more) rows to audit_log.

    Non-terminal events: one row, simple insert.

    Terminal events: run evaluate_audit_validation against the
    candidate row in-memory; emit rule_fired events through an
    AuditLogSink that allocates sequence numbers starting at
    event.sequence_no + 1; then insert the original terminal row at
    event.sequence_no. All in one transaction — recursion-safe.

    No raise on audit_validation BLOCK — at Stage 5 those rules fire
    only on workflow defects, and we write the row regardless so the
    audit trail isn't lost. The rule_fired row stays in the log for
    later analysis.
    """
    async with await psycopg.AsyncConnection.connect(_dsn()) as conn:
        try:
            async with conn.cursor() as cur:
                if event.is_terminal_event:
                    # Run audit_validation rules against the candidate.
                    candidate = {
                        "phase": event.phase,
                        "event_kind": event.event_kind,
                        "payload": event.payload,
                    }
                    ctx = {
                        "audit_entry_candidate": candidate,
                        "policy_hash": event.policy_hash_for_validation,
                        "tool_calls": event.tool_calls_for_validation,
                        "reasoning_text": event.reasoning_text_for_validation,
                    }
                    allocator = SequenceAllocator(event.sequence_no + 1)
                    sink = AuditLogSink(
                        conn,
                        event.workflow_run_id,
                        allocator,
                        event.policy_hash_for_validation or "unknown",
                    )
                    try:
                        await evaluate(
                            RULES, Phase.audit_validation, ctx, sink=sink,
                        )
                    except PolicyEngineError as e:
                        raise ApplicationError(
                            str(e),
                            type="PolicyEngineError",
                            non_retryable=not e.retryable,
                        ) from e
                    # Terminal row written AFTER the rule_fired events
                    # so its sequence_no slot is reserved. We use the
                    # passed sequence_no as-is.
                    await _write_audit_row(
                        cur, event, policy_hash=event.policy_hash_for_validation,
                    )
                else:
                    await _write_audit_row(cur, event, policy_hash=None)
            await conn.commit()
        except psycopg.Error as e:
            raise ApplicationError(
                str(e), type="PolicyInfraError", non_retryable=False,
            ) from e


# ---------------------------------------------------------------------
# evaluate_policy
# ---------------------------------------------------------------------


@dataclass
class EvaluatePolicyInput:
    workflow_run_id: str
    starting_sequence_no: int
    phase: str  # Phase enum value as string (Temporal dataclass-friendly)
    context: dict[str, Any]


@activity.defn
async def evaluate_policy(args: EvaluatePolicyInput) -> PolicyDecisionPayload:
    """Run evaluate() at the requested phase; persist snapshot + audit.

    See spec §Workflow integration — evaluate_policy.
    """
    phase = Phase(args.phase)

    try:
        async with await psycopg.AsyncConnection.connect(_dsn()) as conn:
            policy_hash = await write_policy_snapshot(conn, "send_invoice", RULES)
            allocator = SequenceAllocator(args.starting_sequence_no)
            sink = AuditLogSink(
                conn, args.workflow_run_id, allocator, policy_hash,
            )

            # The drift-detection primitives compare hashes pulled from
            # the context dict. The workflow puts proposal_hash_at_proposal
            # in there; we add current_proposal_hash (recomputed here
            # from the proposal) and current_policy_hash (the hash we
            # just computed for the snapshot).
            ctx = dict(args.context)
            if "proposal" in ctx and ctx.get("proposal") is not None:
                ctx["current_proposal_hash"] = hash_proposal(ctx["proposal"])
            ctx["current_policy_hash"] = policy_hash

            try:
                decision = await evaluate(RULES, phase, ctx, sink=sink)
            except PolicyEngineError as e:
                raise ApplicationError(
                    str(e),
                    type="PolicyEngineError",
                    non_retryable=not e.retryable,
                ) from e

            await conn.commit()
    except psycopg.Error as e:
        raise ApplicationError(
            str(e), type="PolicyInfraError", non_retryable=False,
        ) from e

    if not decision.permit:
        raise ApplicationError(
            "policy blocked",
            type="PolicyDecisionError",
            non_retryable=True,
            details=[
                {
                    "phase": phase.value,
                    "rule_ids_fired": list(decision.rule_ids_fired),
                    "violations": [
                        {"rule_id": v.rule_id, "message": v.message,
                         "evidence": v.evidence}
                        for v in decision.violations
                    ],
                }
            ],
        )

    return PolicyDecisionPayload(
        permit=True,
        policy_hash=policy_hash,
        rule_ids_fired=list(decision.rule_ids_fired),
        escalations=[
            {"rule_id": v.rule_id, "message": v.message, "evidence": v.evidence}
            for v in decision.escalations
        ],
        next_sequence_no=allocator.peek(),
    )


# ---------------------------------------------------------------------
# execute_send  (unchanged from Stage 4)
# ---------------------------------------------------------------------


@dataclass
class ExecuteSendInput:
    workflow_run_id: str
    proposal: dict[str, Any]
    approval: dict[str, Any]


@activity.defn
async def execute_send(args: ExecuteSendInput) -> str:
    """Persist the approved invoice. Returns the invoice id."""
    proposal = InvoiceProposal.model_validate(args.proposal)
    approval = ApprovalDecision.model_validate(args.approval)
    invoice_id = f"inv-{args.workflow_run_id}"
    activity.logger.info(
        "execute_send: persisting %s for customer=%s total=%s%s approver=%s",
        invoice_id,
        proposal.customer_id,
        proposal.total_cents,
        proposal.currency,
        approval.approver_id,
    )

    async with await psycopg.AsyncConnection.connect(_dsn()) as conn, conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO invoices (
                id, customer_id, issued_at, due_at, total_cents, currency,
                status, source_type, contract_id
            )
            VALUES (%s, %s, now(), now() + (%s || ' days')::interval, %s, %s,
                    'sent', %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (
                invoice_id,
                proposal.customer_id,
                str(proposal.payment_terms_days),
                proposal.total_cents,
                proposal.currency,
                proposal.source_type,
                proposal.contract_id,
            ),
        )
        for line_no, line in enumerate(proposal.line_items, start=1):
            await cur.execute(
                """
                INSERT INTO invoice_line_items (
                    id, invoice_id, line_no, description, quantity_micros,
                    unit_amount_cents, line_total_cents, source_type,
                    source_refs, computation
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    f"{invoice_id}-li-{line_no:02d}",
                    invoice_id,
                    line_no,
                    line.description,
                    line.quantity_micros,
                    line.unit_amount_cents,
                    line.line_total_cents,
                    line.source_type,
                    Jsonb({"refs": line.source_refs}),
                    line.computation,
                ),
            )
        await conn.commit()
    return invoice_id
```

- [ ] **Step 2: Commit**

```bash
git add workflows/send_invoice/activities.py
git commit -m "feat(stage-5): evaluate_policy body + audit_log validation hook"
```

---

## Task 21: Update `workflows/send_invoice/workflow.py` — context build + pre_execute call

**Files:**
- Modify: `workflows/send_invoice/workflow.py`
- Modify: `workflows/send_invoice/sandbox.py` (add `compass` + `policies` to passthrough)

- [ ] **Step 1: Update `workflows/send_invoice/sandbox.py`**

Add `"compass"` and `"policies"` to `_PASSTHROUGH_MODULES`:

```python
_PASSTHROUGH_MODULES: tuple[str, ...] = (
    # FastMCP transitive deps that bite when re-imported in the sandbox.
    "beartype",
    "fastmcp",
    "mcp",
    # OpenAI Agents SDK + OTel + OpenInference are themselves heavy and
    # purely infrastructural; pass them through so the sandbox doesn't
    # try to re-execute their import-time side effects.
    "agents",
    "openai",
    "openinference",
    "opentelemetry",
    # Database driver is only used by activities, never workflow code,
    # but it sometimes shows up in sys.modules before the sandbox runs.
    "psycopg",
    "psycopg_pool",
    # Stage 5: compass.policy and policies.* import psycopg / openai / etc.
    # transitively. They're activity-only consumers but the modules
    # appear in sys.modules during worker import.
    "compass",
    "policies",
)
```

- [ ] **Step 2: Update `workflows/send_invoice/workflow.py`**

Replace its contents with:

```python
"""``SendInvoiceWorkflow`` — Stage 5: policy engine wired into the gate.

Per docs/build-plan.md §Stage 5 and
docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md:

  Runner.run(agent)  →  build context  →  evaluate_policy(pre_action_proposal)
       │                                            │
       │                                            ▼
       │                                   wait_condition(approved)
       │                                            │
       │                                            ▼
       │                          evaluate_policy(pre_execute)
       │                                            │
       │                                            ▼
       │                                       execute_send
       │                                            │
       │                                            ▼
       │                                       audit_log
       │                                       (is_terminal_event=True)
       │
       └────────── any block / decline / timeout → audit_log → END
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.contrib.openai_agents.workflow import stateful_mcp_server
from temporalio.exceptions import ApplicationError
from temporalio.workflow import ActivityConfig

with workflow.unsafe.imports_passed_through():
    from agents import Runner

    from compass.policy import Phase
    from workflows.send_invoice.activities import (
        AuditEvent,
        EvaluatePolicyInput,
        ExecuteSendInput,
        audit_log,
        evaluate_policy,
        execute_send,
    )
    from workflows.send_invoice.agents import build_main_agent
    from workflows.send_invoice.context import (
        extract_reasoning_text,
        extract_tool_calls,
        hash_proposal,
        project_resolved_entities,
    )
    from workflows.send_invoice.types import (
        ApprovalDecision,
        SendInvoiceRequest,
        WorkflowResult,
    )

_POLICY_DECISION_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="SendInvoiceWorkflow")
class SendInvoiceWorkflow:
    def __init__(self) -> None:
        self._approval: ApprovalDecision | None = None
        self._next_seq = 0
        self._proposal_hash: str | None = None
        self._policy_hash: str | None = None
        self._tool_calls: list[dict] = []
        self._reasoning_text: str = ""

    @workflow.signal(name="approve")
    async def approve(self, decision: ApprovalDecision) -> None:
        if self._approval is not None:
            await self._audit(
                phase="pre_execute",
                event_kind="duplicate_approval_signal",
                payload={"received": decision.model_dump()},
            )
            return
        self._approval = decision

    @workflow.run
    async def run(self, req: SendInvoiceRequest) -> WorkflowResult:
        run_id = workflow.info().workflow_id

        # ---- 1. agent loop --------------------------------------------------
        async with stateful_mcp_server(
            "bank",
            config=ActivityConfig(
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            ),
        ) as bank:
            agent = build_main_agent(bank)
            result = await Runner.run(agent, input=req.user_message, max_turns=10)

        proposal = result.final_output
        if proposal is None:
            await self._audit(
                phase="pre_action_proposal",
                event_kind="agent_no_output",
                payload={"user_message": req.user_message},
            )
            return WorkflowResult(
                outcome="policy_rejected",
                detail="Agent returned no structured proposal.",
            )

        # ---- 2. build policy context (pure workflow code) -------------------
        self._tool_calls = extract_tool_calls(result)
        self._reasoning_text = extract_reasoning_text(result)
        resolved_entities = project_resolved_entities(self._tool_calls)
        self._proposal_hash = hash_proposal(proposal.model_dump())

        proposal_ctx = {
            "proposal": proposal.model_dump(),
            "resolved_entities": resolved_entities,
            "tool_calls": self._tool_calls,
            "reasoning_text": self._reasoning_text,
            "workflow_run_id": run_id,
        }

        # ---- 3. pre_action_proposal policy gate -----------------------------
        try:
            payload = await workflow.execute_activity(
                evaluate_policy,
                EvaluatePolicyInput(
                    workflow_run_id=run_id,
                    starting_sequence_no=self._next_seq + 1,
                    phase=Phase.pre_action_proposal.value,
                    context=proposal_ctx,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_POLICY_DECISION_RETRY,
            )
        except ApplicationError as e:
            self._next_seq += 1  # the activity reserved at least one seq for an audit row
            await self._audit(
                phase="pre_action_proposal",
                event_kind="policy_rejected",
                payload={"error_type": e.type, "message": str(e)},
                decision="block",
            )
            return WorkflowResult(outcome="policy_rejected", detail=str(e))

        self._policy_hash = payload.policy_hash
        self._next_seq = payload.next_sequence_no - 1  # advance to last used

        # ---- 4. human approval wait -----------------------------------------
        try:
            await workflow.wait_condition(
                lambda: self._approval is not None,
                timeout=timedelta(seconds=req.approval_timeout_seconds),
            )
        except TimeoutError:
            await self._audit(
                phase="pre_execute",
                event_kind="declined",
                payload={"reason": "approval_timeout"},
                decision="block",
            )
            return WorkflowResult(outcome="timeout", detail="No approval within window.")

        approval = self._approval
        assert approval is not None
        await self._audit(
            phase="pre_execute",
            event_kind="approval_signal",
            payload={"approval": approval.model_dump()},
            decision="permit" if approval.approved else "block",
            actor={"user_id": approval.approver_id, "auth_method": "demo_cli"},
        )

        if not approval.approved:
            await self._audit(
                phase="pre_execute",
                event_kind="declined",
                payload={"notes": approval.notes},
                decision="block",
                actor={"user_id": approval.approver_id, "auth_method": "demo_cli"},
            )
            return WorkflowResult(outcome="declined", detail=approval.notes)

        # ---- 5. pre_execute policy gate -------------------------------------
        pre_exec_ctx = {
            **proposal_ctx,
            "approval": approval.model_dump(),
            "proposal_hash_at_proposal": self._proposal_hash,
            "policy_hash_at_proposal": self._policy_hash,
        }
        try:
            payload = await workflow.execute_activity(
                evaluate_policy,
                EvaluatePolicyInput(
                    workflow_run_id=run_id,
                    starting_sequence_no=self._next_seq + 1,
                    phase=Phase.pre_execute.value,
                    context=pre_exec_ctx,
                ),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_POLICY_DECISION_RETRY,
            )
            self._next_seq = payload.next_sequence_no - 1
        except ApplicationError as e:
            self._next_seq += 1
            await self._audit(
                phase="pre_execute",
                event_kind="policy_rejected",
                payload={"error_type": e.type, "message": str(e)},
                decision="block",
                actor={"user_id": approval.approver_id, "auth_method": "demo_cli"},
            )
            return WorkflowResult(outcome="policy_rejected", detail=str(e))

        # ---- 6. side effect -------------------------------------------------
        invoice_id = await workflow.execute_activity(
            execute_send,
            ExecuteSendInput(
                workflow_run_id=run_id,
                proposal=proposal.model_dump(),
                approval=approval.model_dump(),
            ),
            start_to_close_timeout=timedelta(seconds=30),
        )

        # ---- 7. terminal audit row with audit_validation --------------------
        await self._audit(
            phase="audit_validation",
            event_kind="executed",
            payload={
                "invoice_id": invoice_id,
                "total_cents": proposal.total_cents,
            },
            decision="permit",
            actor={"user_id": approval.approver_id, "auth_method": "demo_cli"},
            is_terminal_event=True,
        )
        return WorkflowResult(outcome="sent", invoice_id=invoice_id)

    # ---- helpers --------------------------------------------------------

    def _allocate_seq(self) -> int:
        self._next_seq += 1
        return self._next_seq

    async def _audit(
        self,
        *,
        phase: str,
        event_kind: str,
        payload: dict[str, object],
        decision: str | None = None,
        rule_id: str | None = None,
        actor: dict[str, object] | None = None,
        is_terminal_event: bool = False,
    ) -> None:
        seq = self._allocate_seq()
        await workflow.execute_activity(
            audit_log,
            AuditEvent(
                workflow_run_id=workflow.info().workflow_id,
                sequence_no=seq,
                phase=phase,
                event_kind=event_kind,
                payload=payload,
                decision=decision,
                rule_id=rule_id,
                actor=actor,
                is_terminal_event=is_terminal_event,
                policy_hash_for_validation=self._policy_hash,
                tool_calls_for_validation=self._tool_calls,
                reasoning_text_for_validation=self._reasoning_text,
            ),
            start_to_close_timeout=timedelta(seconds=15),
        )
        # Audit_validation may have written extra rule_fired rows past
        # our allocated seq. Advance our counter past them to avoid
        # collisions on the next write.
        if is_terminal_event:
            # Conservative bump — at Stage 5 there are 2 audit_validation
            # rules; even if both fire we won't collide. Production-grade
            # counter sync would have the activity return the new tip.
            self._next_seq += 4
```

- [ ] **Step 3: Verify Stage-4 workflow tests still pass**

Run: `uv run pytest tests/workflows/send_invoice/test_workflow.py -v`
Expected: 4 passed (Stage-4 tests use a happy proposal that satisfies
every rule).

If failures: they're real — investigate and fix before continuing.

- [ ] **Step 4: Commit**

```bash
git add workflows/send_invoice/workflow.py workflows/send_invoice/sandbox.py
git commit -m "feat(stage-5): wire policy engine into SendInvoiceWorkflow"
```

---

## Task 22: `tests/policies/test_send_invoice_rules.py`

RULES-driven tests. No Temporal; no DB; uses `InMemorySink`. One pass
case + one fail/escalate case per rule.

**Files:**
- Create: `tests/policies/__init__.py` (empty)
- Create: `tests/policies/conftest.py`
- Create: `tests/policies/test_send_invoice_rules.py`

- [ ] **Step 1: Create empty `tests/policies/__init__.py`**

```python
```

- [ ] **Step 2: Create `tests/policies/conftest.py`** (shared context builders)

```python
"""Shared fixtures for policies tests."""

from __future__ import annotations

from typing import Any

import pytest


def happy_proposal() -> dict[str, Any]:
    return {
        "customer_id": "cust_alpha",
        "currency": "USD",
        "total_cents": 80000,
        "payment_terms_days": 30,
        "source_type": "time_tracking",
        "contract_id": "ct_alpha_current",
        "line_items": [
            {
                "description": "Solutions Architect time",
                "quantity_micros": 2_000_000,
                "unit_amount_cents": 40000,
                "line_total_cents": 80000,
                "source_type": "time_tracking",
                "source_refs": ["te_001"],
                "computation": "2h * $400/hr per contract ct_alpha_current",
            }
        ],
        "notes": None,
    }


def happy_resolved_entities() -> dict[str, Any]:
    return {
        "customer": {"id": "cust_alpha", "name": "Acme",
                     "kyc_status": "verified"},
        "contract": {"id": "ct_alpha_current", "currency": "USD",
                     "monthly_hour_cap": 40},
        "rate_card_entries": [],
        "time_entries": [],
    }


def happy_tool_calls() -> list[dict[str, Any]]:
    return [{"tool_name": "list_customers", "args": {}, "result": []}]


def happy_pre_action_proposal_ctx() -> dict[str, Any]:
    return {
        "proposal": happy_proposal(),
        "resolved_entities": happy_resolved_entities(),
        "tool_calls": happy_tool_calls(),
        "reasoning_text": "OK",
        "workflow_run_id": "wf-test",
    }


@pytest.fixture
def base_ctx() -> dict[str, Any]:
    return happy_pre_action_proposal_ctx()
```

- [ ] **Step 3: Write `tests/policies/test_send_invoice_rules.py`**

```python
"""RULES drive evaluate() directly. No Temporal, no DB."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from compass.policy import Phase, evaluate
from compass.policy.sink import InMemorySink
from policies.send_invoice import RULES
from tests.policies.conftest import (
    happy_pre_action_proposal_ctx,
    happy_proposal,
)


# ---- pre_action_proposal: happy path ----


async def test_happy_proposal_permits_all_pre_action_proposal_rules(
    base_ctx: dict[str, Any],
) -> None:
    sink = InMemorySink()
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=sink,
    )
    assert decision.permit is True
    assert decision.rule_ids_fired == ()
    fired = [e for e in sink.events if e["event_kind"] == "rule_fired"]
    assert fired == []
    skipped = [e for e in sink.events if e["event_kind"] == "rule_skipped"]
    expected_ids = {
        "customer_must_exist", "customer_kyc_verified", "invoice_amount_cap",
        "require_amount_source", "require_evidence_citation",
        "contract_consistency", "prohibit_exceed_contract_cap",
        "currency_consistency",
    }
    assert {e["rule_id"] for e in skipped} == expected_ids


# ---- pre_action_proposal: per-rule fail cases ----


async def test_missing_customer_fires_customer_must_exist(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["resolved_entities"]["customer"] = None
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "customer_must_exist" in decision.rule_ids_fired


async def test_pending_kyc_fires_customer_kyc_verified(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["resolved_entities"]["customer"]["kyc_status"] = "pending"
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "customer_kyc_verified" in decision.rule_ids_fired


async def test_amount_above_cap_escalates_but_permits(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["proposal"]["total_cents"] = 15_000_000  # > $100k cap
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    # Escalation does not block — permit stays True (no BLOCK fired).
    assert decision.permit is True
    assert "invoice_amount_cap" in decision.rule_ids_fired
    assert len(decision.escalations) == 1


async def test_empty_source_refs_fires_require_evidence_citation(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["proposal"]["line_items"][0]["source_refs"] = []
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "require_evidence_citation" in decision.rule_ids_fired


async def test_invalid_source_type_fires_require_amount_source(
    base_ctx: dict[str, Any],
) -> None:
    # Bypass Pydantic by tweaking the raw dict directly.
    base_ctx["proposal"]["line_items"][0]["source_type"] = "made_up"
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "require_amount_source" in decision.rule_ids_fired


async def test_currency_mismatch_fires_contract_consistency(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["proposal"]["currency"] = "EUR"
    base_ctx["resolved_entities"]["contract"]["currency"] = "USD"
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "contract_consistency" in decision.rule_ids_fired


async def test_exceed_contract_cap_fires(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["resolved_entities"]["contract"]["monthly_hour_cap"] = 1
    # The line item is 2h; cap is 1h.
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "prohibit_exceed_contract_cap" in decision.rule_ids_fired


async def test_rate_card_currency_mismatch_fires_currency_consistency(
    base_ctx: dict[str, Any],
) -> None:
    base_ctx["resolved_entities"]["rate_card_entries"] = [
        {"id": "rc_other_ccy", "currency": "EUR"},
    ]
    decision = await evaluate(
        RULES, Phase.pre_action_proposal, base_ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "currency_consistency" in decision.rule_ids_fired


# ---- pre_execute ----


async def test_no_silent_modification_skips_when_hash_matches() -> None:
    ctx = {
        "proposal_hash_at_proposal": "h1",
        "current_proposal_hash": "h1",
        "policy_hash_at_proposal": "p1",
        "current_policy_hash": "p1",
    }
    decision = await evaluate(
        RULES, Phase.pre_execute, ctx, sink=InMemorySink(),
    )
    assert decision.permit is True
    assert decision.rule_ids_fired == ()


async def test_silent_modification_fires_block() -> None:
    ctx = {
        "proposal_hash_at_proposal": "h1",
        "current_proposal_hash": "h2",
        "policy_hash_at_proposal": "p1",
        "current_policy_hash": "p1",
    }
    decision = await evaluate(
        RULES, Phase.pre_execute, ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "no_silent_modification_after_confirmation" in decision.rule_ids_fired


async def test_policy_drift_fires_escalate() -> None:
    ctx = {
        "proposal_hash_at_proposal": "h1",
        "current_proposal_hash": "h1",
        "policy_hash_at_proposal": "p1",
        "current_policy_hash": "p2",
    }
    decision = await evaluate(
        RULES, Phase.pre_execute, ctx, sink=InMemorySink(),
    )
    # Escalation only — does not flip permit.
    assert decision.permit is True
    assert "no_policy_drift_after_confirmation" in decision.rule_ids_fired
    assert len(decision.escalations) == 1


# ---- audit_validation ----


async def test_audit_validation_skips_complete_candidate() -> None:
    ctx = {
        "audit_entry_candidate": {"phase": "audit_validation",
                                  "event_kind": "executed",
                                  "payload": {}},
        "policy_hash": "abc",
        "tool_calls": [{"tool_name": "list_customers", "args": {}, "result": []}],
        "reasoning_text": "ok",
    }
    decision = await evaluate(
        RULES, Phase.audit_validation, ctx, sink=InMemorySink(),
    )
    assert decision.permit is True


async def test_missing_policy_hash_fires_audit_has_policy_version() -> None:
    ctx = {
        "audit_entry_candidate": {},
        "policy_hash": "",
        "tool_calls": [{"x": 1}],
        "reasoning_text": "",
    }
    decision = await evaluate(
        RULES, Phase.audit_validation, ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "audit_has_policy_version" in decision.rule_ids_fired


async def test_empty_tool_calls_fires_audit_has_data_sources() -> None:
    ctx = {
        "audit_entry_candidate": {},
        "policy_hash": "abc",
        "tool_calls": [],
        "reasoning_text": "",
    }
    decision = await evaluate(
        RULES, Phase.audit_validation, ctx, sink=InMemorySink(),
    )
    assert decision.permit is False
    assert "audit_has_data_sources" in decision.rule_ids_fired
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest tests/policies/ -v`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/policies/__init__.py tests/policies/conftest.py tests/policies/test_send_invoice_rules.py
git commit -m "test(stage-5): RULES-driven happy + per-rule fail/escalate cases"
```

---

## Task 23: `tests/workflows/send_invoice/test_workflow_policy.py` — end-to-end

End-to-end workflow tests where canned `TestModel` proposals drive the
policy gate through pass / block / escalate paths.

**Files:**
- Create: `tests/workflows/send_invoice/test_workflow_policy.py`

- [ ] **Step 1: Write the test module**

```python
"""End-to-end workflow tests for the Stage-5 policy gate.

Each test parameterizes the TestModel's canned proposal to drive the
policy review gate through a specific path: pass, block, escalate.
The bank-data fixtures are inherited from tests/mcp_bank/conftest.py
(``cust_alpha`` has KYC verified and an active contract).
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row
from temporalio.client import Client
from temporalio.contrib.openai_agents.testing import ResponseBuilders, TestModel
from temporalio.worker import Worker

from tests.workflows.send_invoice.conftest import TASK_QUEUE, proposal_dict
from workflows.send_invoice.types import (
    ApprovalDecision,
    SendInvoiceRequest,
    WorkflowResult,
)
from workflows.send_invoice.workflow import SendInvoiceWorkflow


def _new_workflow_id() -> str:
    return f"test-stage5-{uuid.uuid4().hex[:8]}"


def _model_with_proposal(payload: dict[str, Any]) -> TestModel:
    return TestModel(
        lambda: ResponseBuilders.output_message(json.dumps(payload))
    )


def _dsn() -> str:
    return os.environ["COMPASS_PG_DSN"]


async def _fetch_audit(workflow_run_id: str) -> list[dict[str, Any]]:
    async with (
        await psycopg.AsyncConnection.connect(_dsn()) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            "SELECT * FROM audit_log WHERE workflow_run_id=%s ORDER BY sequence_no",
            (workflow_run_id,),
        )
        return await cur.fetchall()


async def _fetch_snapshot_count(policy_hash: str) -> int:
    async with (
        await psycopg.AsyncConnection.connect(_dsn()) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute(
            "SELECT count(*) FROM policy_snapshots WHERE policy_hash=%s",
            (policy_hash,),
        )
        row = await cur.fetchone()
        return row[0]


# -----------------------------------------------------------------------
# Test cases
# -----------------------------------------------------------------------


@pytest.fixture
def model_passing() -> TestModel:
    """A happy proposal that satisfies every Stage-5 rule."""
    return _model_with_proposal(proposal_dict())


async def test_passing_proposal_executes(
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001
) -> None:
    workflow_id = _new_workflow_id()
    handle = await temporal_client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message="invoice Acme for last quarter"),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    await handle.signal(
        SendInvoiceWorkflow.approve,
        ApprovalDecision(approved=True, approver_id="alice"),
    )
    result: WorkflowResult = await handle.result()
    assert result.outcome == "sent"
    rows = await _fetch_audit(workflow_id)
    # Every rule_skipped + approval_signal + executed
    skipped = [r for r in rows if r["event_kind"] == "rule_skipped"]
    assert len(skipped) >= 8  # 8 pre_action_proposal + 2 pre_execute
    # Every row carries the same non-stub policy_hash
    hashes = {r["policy_hash"] for r in rows if r["policy_hash"] != "stage-4-stub"}
    assert len(hashes) == 1
    policy_hash = hashes.pop()
    assert await _fetch_snapshot_count(policy_hash) == 1


@pytest.fixture
def model_missing_refs() -> TestModel:
    p = proposal_dict()
    p["line_items"][0]["source_refs"] = []
    return _model_with_proposal(p)


@pytest.mark.parametrize("model", ["model_missing_refs"], indirect=True)
async def test_missing_source_refs_blocks(
    request,
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001
) -> None:
    workflow_id = _new_workflow_id()
    handle = await temporal_client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message="invoice Acme"),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    result: WorkflowResult = await handle.result()
    assert result.outcome == "policy_rejected"
    rows = await _fetch_audit(workflow_id)
    fired = [r for r in rows if r["event_kind"] == "rule_fired"]
    assert any(r["rule_id"] == "require_evidence_citation" for r in fired)


@pytest.fixture
def model_currency_mismatch() -> TestModel:
    p = proposal_dict()
    p["currency"] = "EUR"
    return _model_with_proposal(p)


@pytest.mark.parametrize("model", ["model_currency_mismatch"], indirect=True)
async def test_currency_mismatch_blocks(
    request,
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001
) -> None:
    """Note: the bank-data fixture contract for cust_alpha is USD;
    proposing EUR triggers contract_consistency, not currency_consistency
    (currency_consistency targets rate_card mismatches)."""
    workflow_id = _new_workflow_id()
    handle = await temporal_client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message="invoice Acme"),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    result: WorkflowResult = await handle.result()
    assert result.outcome == "policy_rejected"
    rows = await _fetch_audit(workflow_id)
    fired = [r for r in rows if r["event_kind"] == "rule_fired"]
    fired_ids = {r["rule_id"] for r in fired}
    assert "contract_consistency" in fired_ids


@pytest.fixture
def model_high_amount() -> TestModel:
    p = proposal_dict()
    p["total_cents"] = 15_000_000  # > $100k cap → ESCALATE
    p["line_items"][0]["line_total_cents"] = 15_000_000
    p["line_items"][0]["unit_amount_cents"] = 7_500_000  # 2 hours × 75000
    return _model_with_proposal(p)


@pytest.mark.parametrize("model", ["model_high_amount"], indirect=True)
async def test_high_amount_escalates_but_executes(
    request,
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001
) -> None:
    workflow_id = _new_workflow_id()
    handle = await temporal_client.start_workflow(
        SendInvoiceWorkflow.run,
        SendInvoiceRequest(user_message="invoice Acme for huge engagement"),
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    await handle.signal(
        SendInvoiceWorkflow.approve,
        ApprovalDecision(approved=True, approver_id="alice"),
    )
    result: WorkflowResult = await handle.result()
    assert result.outcome == "sent"  # escalation does not block
    rows = await _fetch_audit(workflow_id)
    fired = [r for r in rows if r["event_kind"] == "rule_fired"]
    assert any(
        r["rule_id"] == "invoice_amount_cap" and r["decision"] == "escalate"
        for r in fired
    )


async def test_policy_snapshot_idempotent_across_runs(
    temporal_client: Client,
    worker: Worker,  # noqa: ARG001
) -> None:
    """Two runs with the same RULES → exactly one policy_snapshots row."""
    for _ in range(2):
        workflow_id = _new_workflow_id()
        handle = await temporal_client.start_workflow(
            SendInvoiceWorkflow.run,
            SendInvoiceRequest(user_message="invoice Acme"),
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        await handle.signal(
            SendInvoiceWorkflow.approve,
            ApprovalDecision(approved=True, approver_id="alice"),
        )
        await handle.result()

    # Fetch the most-recent policy_hash from audit_log and assert the
    # snapshot table has exactly one row for it.
    async with (
        await psycopg.AsyncConnection.connect(_dsn()) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            "SELECT DISTINCT policy_hash FROM audit_log "
            "WHERE policy_hash != 'stage-4-stub'"
        )
        hashes = [r["policy_hash"] for r in await cur.fetchall()]
    assert len(hashes) == 1
    assert await _fetch_snapshot_count(hashes[0]) == 1
```

- [ ] **Step 2: Update `tests/workflows/send_invoice/conftest.py`**

The existing conftest needs the `model` fixture to use the indirect
parametrize idiom from this test module. Add this fixture (at module
scope) — it lets parametrized tests pick a different model per case.

Locate the existing `model` fixture (a `@pytest.fixture` returning
`_proposal_response()`) in `tests/workflows/send_invoice/test_workflow.py`
and adapt it. The cleanest path is to move the default `model` fixture
into `conftest.py`, then individual test modules override with their
own parametrized `model` fixtures.

Add to `tests/workflows/send_invoice/conftest.py` at the end:

```python
@pytest.fixture
def model(request) -> TestModel:
    """Default: a happy proposal that satisfies every Stage-5 rule.

    Test modules can override with their own fixture (named ``model``)
    or use ``@pytest.mark.parametrize("model", [...], indirect=True)``
    to switch per test.
    """
    if hasattr(request, "param") and isinstance(request.param, str):
        return request.getfixturevalue(request.param)
    return TestModel(
        lambda: ResponseBuilders.output_message(
            __import__("json").dumps(proposal_dict())
        )
    )
```

And add `from temporalio.contrib.openai_agents.testing import ResponseBuilders, TestModel` to the imports.

If the existing `tests/workflows/send_invoice/test_workflow.py` already
defines a module-level `model` fixture: keep it; that overrides the
conftest one for that file. The conftest fixture only fires when no
module-level override exists.

- [ ] **Step 3: Run the new tests**

Run: `uv run pytest tests/workflows/send_invoice/test_workflow_policy.py -v`
Expected: 5 passed.

If they fail with "TEMPORAL_DB_NAME"/`compass_test` connection issues,
the local Postgres sidecar isn't running. Run: `docker compose up -d`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -v`
Expected: all tests pass (existing + new).

- [ ] **Step 5: Commit**

```bash
git add tests/workflows/send_invoice/test_workflow_policy.py tests/workflows/send_invoice/conftest.py
git commit -m "test(stage-5): end-to-end policy gate cases (pass/block/escalate)"
```

---

## Task 24: Final integration — dependency-direction check + lint + full suite

**Files:**
- (none modified — verification only)

- [ ] **Step 1: Run the dependency-direction check**

Run: `bash scripts/check_dependency_direction.sh`
Expected: `Dependency-direction check passed.`

If it fails: a `from workflows.…` or `from mcp_bank.…` import slipped
into `compass/`. Fix the import and re-run.

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check .`
Expected: all checks passed.

- [ ] **Step 3: Run pyright**

Run: `uv run pyright`
Expected: 0 errors. (Warnings are OK if they're pre-existing.)

- [ ] **Step 4: Run the full pytest suite**

Run: `uv run pytest -v`
Expected: every test passes — Stage-4 existing tests (4 workflow + N
MCP), Stage-5 new tests (compass.policy unit tests, policy rule tests,
workflow policy tests, context tests).

- [ ] **Step 5: Smoke-test the live worker boots**

Run, from one terminal:

```bash
docker compose up -d
temporal server start-dev  # in another terminal
uv run python -m workflows.send_invoice.worker  # in another terminal
```

Expected: worker connects and polls without errors. Stop with Ctrl-C
(no live workflow run needed at this stage; the existing manual
`scripts/start_workflow.py` + `scripts/approve_workflow.py` flow
continues to work).

- [ ] **Step 6: Final commit (if anything outstanding)**

```bash
git status
# If clean, no commit needed. Otherwise:
git add .
git commit -m "chore(stage-5): final cleanup after full-suite verification"
```

---

## Task 25: Update README + open PR

**Files:**
- Modify: `README.md` (add Stage 5 status if a stage list exists)

- [ ] **Step 1: Check the README's Stage status list**

Run: `uv run grep -n "Stage" README.md | head -20`

If there's a status list, append:

```markdown
- Stage 5 — Policy engine + primitive library (✅ shipped)
```

If there's no such list, skip this step.

- [ ] **Step 2: Commit the README change (if any)**

```bash
git add README.md
git commit -m "docs(stage-5): mark Stage 5 shipped in README"
```

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin stage-5-policy-engine
gh pr create --title "feat(stage-5): policy engine + primitive library" \
             --body "$(cat <<'EOF'
## Summary

- compass.policy engine + 12 framework-core/app-specific primitives
- policies/send_invoice.py with 12 rules across pre_action_proposal,
  pre_execute, audit_validation phases
- evaluate_policy activity rewritten to switch on phase, write
  policy_snapshots, route exceptions to Temporal retry semantics
- SendInvoiceWorkflow builds context from RunResult and gates on
  pre_action_proposal + pre_execute
- audit_log activity gains is_terminal_event hook for audit_validation

## Test plan

- [ ] tests/compass/policy/ — 40+ unit tests of engine, primitives, hashing
- [ ] tests/policies/test_send_invoice_rules.py — 15 RULES-driven cases
- [ ] tests/workflows/send_invoice/test_workflow_policy.py — 5 end-to-end
- [ ] Existing Stage-4 workflow tests still pass (4)
- [ ] scripts/check_dependency_direction.sh passes
- [ ] ruff + pyright clean
EOF
)"
```

Expected: PR URL printed.

---

## Spec coverage check

Every section in
`docs/superpowers/specs/2026-05-27-stage-5-policy-engine-design.md`
maps to a task:

| Spec section | Task |
|---|---|
| Types (`Phase`, `Severity`, `Predicate`, `Rule`, `Violation`, `Decision`) | 1 |
| Errors (positive `retryable`) | 1 |
| Path resolver | 2 |
| Primitive registry | 3 |
| Sink (protocol + InMemory/Null/Multi + register) | 4 |
| Engine (`evaluate` + phase wrappers) | 5 |
| Hashing | 6 |
| Snapshot writer | 7 |
| AuditLogSink | 8 |
| Framework-core primitives (8 of them) | 9-14 |
| `attach_to_agent` (wired empty) | 15 |
| App-specific primitives (4 Billing integrity) | 16 |
| Workflow context extractors | 17 |
| Types update (`PolicyDecisionPayload`) | 18 |
| `policies/send_invoice.py` RULES list | 19 |
| `evaluate_policy` activity body + `audit_log` extension | 20 |
| Workflow body + sandbox passthrough | 21 |
| `tests/policies/` | 22 |
| `tests/workflows/send_invoice/test_workflow_policy.py` | 23 |
| Activity failure semantics (idempotency contracts) | Verified by Task 23's policy_snapshot_idempotent test |
| Determinism / LLM-judge story | Documented in spec; not exercised at Stage 5 (no judge primitives ship) |
| Stringly-typed paths wart | Documented in spec as deferred; no task |
| `POLICY.md` autogeneration | Deferred to Stage 13 per spec |
| Final verification | 24 |
| README + PR | 25 |

