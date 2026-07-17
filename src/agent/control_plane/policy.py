from __future__ import annotations

from agent.control_plane.models import AgentRunRecord, PolicyDecision, ToolRisk


TOOL_RISKS: dict[str, ToolRisk] = {
    "request_search_budget": "read_only",
    "submit_budget_decision": "read_only",
    "search_repository_datasets": "read_only",
    "search_repository_candidates": "read_only",
    "inspect_repository_candidates": "read_only",
    "submit_project_judgments": "bounded_write",
    "get_discovery_state": "read_only",
    "select_discovery_manifest": "bounded_write",
    "assess_task_readiness": "read_only",
    "rank_data_value": "read_only",
    "write_dataset_manifest": "bounded_write",
    "build_ai_ready_tables": "bounded_write",
    "make_dataset_recipe": "bounded_write",
    "generate_peaklist_and_retry": "bounded_write",
    "download_repository_file": "expensive",
    "convert_vendor_file": "expensive",
    "run_full_workflow": "expensive",
    "run_model_training": "expensive",
    "change_species": "biological",
    "change_database": "biological",
    "change_acquisition_mode": "biological",
    "change_enzyme": "biological",
    "change_ptm_interpretation": "biological",
    "run_shell_command": "forbidden",
    "execute_arbitrary_command": "forbidden",
}


def evaluate_tool_policy(tool_name: str, run: AgentRunRecord) -> PolicyDecision:
    risk = TOOL_RISKS.get(str(tool_name or "").strip(), "forbidden")
    if run.tool_call_count >= run.budget.max_tool_calls:
        return PolicyDecision(
            outcome="deny",
            risk=risk,
            reason="tool_call_budget_exhausted",
        )
    if risk == "forbidden":
        return PolicyDecision(
            outcome="deny",
            risk=risk,
            reason="tool_not_in_control_plane_allowlist",
        )
    if risk == "biological":
        return PolicyDecision(
            outcome="approval_required",
            risk=risk,
            reason="biological_interpretation_change_requires_human_review",
            requires_human=True,
        )
    if risk == "expensive":
        if run.expensive_action_count >= run.budget.max_expensive_actions:
            return PolicyDecision(
                outcome="deny",
                risk=risk,
                reason="expensive_action_budget_exhausted",
            )
        return PolicyDecision(
            outcome="approval_required",
            risk=risk,
            reason="expensive_action_requires_human_approval",
            requires_human=True,
        )
    return PolicyDecision(
        outcome="allow",
        risk=risk,
        reason="tool_allowed_by_control_plane_policy",
    )
