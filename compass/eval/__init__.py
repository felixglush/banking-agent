"""Stage 7 eval harness — see
docs/superpowers/specs/2026-05-28-stage-7-eval-harness-design.md."""

from compass.eval.orchestrator import EvalReport, run_eval
from compass.eval.protocols import (
    EvalRunStore,
    RuleFireSource,
    ScoreSink,
    WorkflowRunner,
)
from compass.eval.runner import TemporalWorkflowRunner
from compass.eval.sources.audit_log import PostgresAuditLogSource
from compass.eval.sources.eval_runs import HoldoutCapExceeded, PostgresEvalRunStore
from compass.eval.sources.langfuse_scores import LangfuseDatasetScoreSink
from compass.eval.types import Case, CaseResult, Mode, Outcome

__all__ = [
    "Case",
    "CaseResult",
    "EvalReport",
    "EvalRunStore",
    "HoldoutCapExceeded",
    "LangfuseDatasetScoreSink",
    "Mode",
    "Outcome",
    "PostgresAuditLogSource",
    "PostgresEvalRunStore",
    "RuleFireSource",
    "ScoreSink",
    "TemporalWorkflowRunner",
    "WorkflowRunner",
    "run_eval",
]
