"""Stage 7 reusability surface — four Protocols that compass.eval's
suites and orchestrator consume."""

from typing import Protocol, runtime_checkable

from compass.eval.types import Case, CaseResult


@runtime_checkable
class WorkflowRunner(Protocol):
    """Drives one case through the workflow under test."""

    async def run_case(self, case: Case) -> CaseResult: ...


@runtime_checkable
class RuleFireSource(Protocol):
    """Read side of the policy-compliance assertion."""

    async def rule_ids_fired(self, workflow_run_id: str) -> set[str]: ...


@runtime_checkable
class ScoreSink(Protocol):
    """Per-case score storage."""

    async def write_score(
        self,
        *,
        run_id: str,
        item_id: str,
        name: str,
        value: float,
        comment: str | None,
    ) -> None: ...


@runtime_checkable
class EvalRunStore(Protocol):
    """Harness-control state for a run."""

    async def allocate_run(
        self,
        *,
        git_sha: str,
        mode: str,
        holdout_justification: str | None,
        policy_enabled: bool,
        suite_names: list[str],
        host_git_dirty: bool,
    ) -> str: ...

    async def link_pair(self, run_id: str, paired_with: str) -> None: ...

    async def finalize(self, run_id: str) -> None: ...
