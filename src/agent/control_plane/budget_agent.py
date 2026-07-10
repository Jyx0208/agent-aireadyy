from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

try:
    from agents import RunContextWrapper
except ImportError:  # pragma: no cover - optional runtime dependency
    RunContextWrapper = Any  # type: ignore[assignment,misc]

from agent.control_plane.budget_governor import BudgetGovernor
from agent.control_plane.models import (
    BudgetDecision,
    BudgetDecisionInput,
    BudgetReviewResult,
    RoundMetrics,
    SearchProposalRecord,
)


@dataclass
class BudgetAgentContext:
    governor: BudgetGovernor
    result: BudgetReviewResult | None = None
    invalid_attempts: int = 0


def submit_budget_decision(
    wrapper: RunContextWrapper[BudgetAgentContext],
    decision: BudgetDecisionInput,
) -> str:
    """Submit one structured budget decision for the supplied proposal.

    Args:
        decision: The grant, shrink, replan, or stop decision to validate.
    """
    try:
        wrapper.context.governor.authorize_tool("submit_budget_decision")
    except ValueError as exc:
        return json.dumps({"status": "denied", "reason": str(exc)})
    try:
        validated = BudgetDecision.model_validate(decision.model_dump(mode="json"))
    except ValidationError as exc:
        wrapper.context.invalid_attempts += 1
        wrapper.context.governor.store.append_event(
            wrapper.context.governor.run_id,
            "budget_decision_invalid",
            {"proposal_id": decision.proposal_id, "error": str(exc)},
        )
        return json.dumps(
            {
                "status": "invalid",
                "reason": "budget_decision_invalid",
                "attempt": wrapper.context.invalid_attempts,
            }
        )
    result = wrapper.context.governor.apply_decision(validated)
    wrapper.context.result = result
    return result.model_dump_json()


async def run_budget_agent_review(
    *,
    sdk: dict[str, Any],
    model: Any,
    proposal: SearchProposalRecord,
    metrics: RoundMetrics,
    governor: BudgetGovernor,
    max_turns: int,
) -> BudgetReviewResult:
    context = BudgetAgentContext(governor=governor)
    tool = sdk["function_tool"](submit_budget_decision)
    agent = sdk["Agent"][BudgetAgentContext](
        name="Discovery Budget Agent",
        instructions=_budget_instructions(),
        model=model,
        tools=[tool],
        model_settings=sdk["ModelSettings"](parallel_tool_calls=False),
    )
    await sdk["Runner"].run(
        starting_agent=agent,
        input=_budget_input(proposal, metrics),
        context=context,
        max_turns=max_turns,
        run_config=sdk["RunConfig"](
            workflow_name="proteomics_discovery_budget_review",
            tracing_disabled=True,
        ),
    )
    if context.result is None:
        reason = "budget_decision_invalid" if context.invalid_attempts else "budget_agent_did_not_submit_decision"
        raise ValueError(reason)
    return context.result


def _budget_instructions() -> str:
    return (
        "You are the Discovery Budget Agent. Review only the supplied SearchProposal and "
        "deterministic RoundMetrics. You may approve all proposal indexes, approve a true "
        "subset, request replanning, or stop. Never invent, rewrite, or execute queries. "
        "Never change species, acquisition mode, task type, PTM scope, or repository policy. "
        "Submit one final valid decision with a concise public reasoning_summary; if the tool "
        "returns budget_decision_invalid, correct it once. "
        "A stop decision must include unresolved_gaps, unexplored_strategies, and why_not_continue."
    )


def _budget_input(proposal: SearchProposalRecord, metrics: RoundMetrics) -> str:
    return json.dumps(
        {
            "proposal": proposal.model_dump(mode="json"),
            "metrics": metrics.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
