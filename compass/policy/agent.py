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
from typing import Any

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


def attach_to_agent[T](
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
            _ctx: RunContextWrapper[Any],
            _agent: Agent[T],
            input_value: Any,
        ) -> GuardrailFunctionOutput:
            sink: Sink = await sink_factory() if sink_factory else NullSink()
            decision = await evaluate(
                input_rules,
                Phase.input_validation,
                {"user_message": input_value},
                sink=sink,
            )
            return GuardrailFunctionOutput(
                output_info={"rule_ids_fired": list(decision.rule_ids_fired)},
                tripwire_triggered=not decision.permit,
            )

        agent.input_guardrails = [*agent.input_guardrails, _input_gate]

    if output_rules:

        @output_guardrail  # type: ignore[misc]
        async def _output_gate(
            _ctx: RunContextWrapper[Any],
            _agent: Agent[T],
            output: Any,
        ) -> GuardrailFunctionOutput:
            sink: Sink = await sink_factory() if sink_factory else NullSink()
            ctx_dict = output.model_dump() if hasattr(output, "model_dump") else {"output": output}
            decision = await evaluate(
                output_rules,
                Phase.output_validation,
                {"proposal": ctx_dict},
                sink=sink,
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
