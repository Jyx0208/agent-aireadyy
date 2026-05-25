from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.agent_core.models import AgentAuditArtifactPaths, AgentDecisionRecord, AgentDecisionTrace, AgentObservation, AgentPlanSummary
from agent.agent_core.decision_trace import build_agent_decision_trace
from agent.agent_core.observation import build_agent_observation
from agent.agent_core.plan import build_agent_plan_summary
from agent.models import PridePlanResult
from agent.utils import write_json


def write_agent_audit_artifacts(
    output_dir: str | Path,
    observation: AgentObservation,
    plan: AgentPlanSummary,
    decision_trace: list[AgentDecisionRecord],
) -> AgentAuditArtifactPaths:
    output_dir = Path(output_dir)
    observation_path = write_json(output_dir / "agent_observation.json", observation)
    plan_path = write_json(output_dir / "agent_plan.json", plan)
    trace_path = write_json(output_dir / "agent_decision_trace.json", AgentDecisionTrace(decisions=decision_trace))
    return AgentAuditArtifactPaths(
        observation=observation_path,
        plan=plan_path,
        decision_trace=trace_path,
    )


def write_agent_audit_for_result(
    output_dir: str | Path,
    result: PridePlanResult | Any,
    *,
    resource_state: dict[str, Any] | None = None,
) -> AgentAuditArtifactPaths:
    observation = build_agent_observation(
        getattr(result.context, "file_name", getattr(result.plan, "source_file_name", "")),
        result.resolution,
        result.context,
        asset=result.asset,
        resource_state=resource_state,
    )
    decisions = build_agent_decision_trace(result.resolution, result.attributes, asset=result.asset, plan=result.plan)
    plan = build_agent_plan_summary(result.plan, result.attributes, decisions=decisions)
    return write_agent_audit_artifacts(output_dir, observation, plan, decisions)
