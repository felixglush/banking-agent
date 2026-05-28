# CLAUDE.md

Project rules for Claude / coding agents working in this repo.

## Rules

1. **Use the latest package versions and pin them exactly.** When adding a
   dependency to `pyproject.toml` (or any other manifest), look up the latest
   stable release and pin it with `==` (Python) or the equivalent exact-version
   operator for the ecosystem. No `>=`, no `~=`, no caret ranges. Lockfiles
   (`uv.lock`, etc.) are committed.

2. **Pyright passes.** CI runs `uv run pyright` and blocks on errors. Treat
   type errors like test failures — fix the cause, don't paper over them.
   `# pyright: ignore[<code>]` is acceptable only when the suppression itself
   is the documented intent (e.g. pytest's `@pytest.fixture(autouse=True)`
   triggering `reportUnusedFunction`, or legitimate private-state access from
   a test fixture). Include the specific rule code in every suppression.

3. **Name the shapes the code already understands.** When a `dict[str, Any]`
   or `list[dict[str, Any]]` represents a domain concept (a tool-call record,
   an audit payload, a sink event, a policy context), give it a name. Type
   aliases (`PolicyContext = Mapping[str, Any]`) for open dicts whose keys vary
   by call site; `TypedDict` for closed shapes the code owns; discriminated
   unions (`Foo | Bar` keyed on a `Literal` field) for variants. See
   `compass/policy/types.py` for the established vocabulary — extend that file
   when a new framework-core concept emerges; put workflow-specific shapes
   next to the code that produces them.

4. **`Mapping[str, Any]` for read-only consumers; `dict[str, Any]` for
   builders.** Function parameters that only read from a dict take
   `Mapping[str, Any]` (or a named alias over it — `PolicyContext`,
   `AuditPayload`, etc.). Return types and locally-mutated dicts use
   `dict[str, Any]`. This is what makes `PredicateFn` substitutable and keeps
   list/dict invariance friction out of call sites.

5. **Use `cast()` at narrowing boundaries pyright can't carry through.**
   When `isinstance(x, Mapping)` or `isinstance(x, list)` strips the type
   parameters, restore them with `cast(...)` — the cast names the assumption.
   Prefer `cast` over `# type: ignore`; the former still type-checks the rest
   of the expression, the latter silences everything on that line.
