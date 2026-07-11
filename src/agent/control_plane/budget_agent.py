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
    result = await sdk["Runner"].run(
        starting_agent=agent,
        input=_budget_input(proposal, metrics),
        context=context,
        max_turns=max_turns,
        run_config=sdk["RunConfig"](
            workflow_name="proteomics_discovery_budget_review",
            tracing_disabled=True,
        ),
    )
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    if usage is not None:
        governor.store.increment_model_usage(
            governor.run_id,
            requests=int(getattr(usage, "requests", 0) or 0),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        )
    if context.result is None:
        reason = "budget_decision_invalid" if context.invalid_attempts else "budget_agent_did_not_submit_decision"
        raise ValueError(reason)
    return context.result


def _budget_instructions() -> str:
    return (
        "Objective: allocate additional Discovery search budget when it has credible expected "
        "scientific value. Quality takes priority over saving requests within the hard limits. "
        "Review only the supplied SearchProposal and deterministic RoundMetrics. Grant all queries, "
        "grant a true subset, request replanning, or stop. Favor proposals that target unresolved "
        "semantic coverage, hard-constraint evidence, or metadata gaps with materially novel queries. "
        "Repeated queries, high duplicate rate, consecutive no-gain actions, or proposals without a "
        "specific expected quality gain should be replanned or stopped. Never invent, rewrite, or "
        "execute queries and never change species, acquisition mode, labeling, task type, PTM scope, "
        "or repository policy. Submit one valid decision with a concise public reasoning_summary. "
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
