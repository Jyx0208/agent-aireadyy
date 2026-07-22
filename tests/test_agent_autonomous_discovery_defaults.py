from __future__ import annotations

import os
from pathlib import Path

from agent.control_plane.openai_agents import _quality_first_discovery_instructions
from agent.discovery.models import DatasetRequest
from agent.web import app as web_app


def test_default_discovery_mode_is_single_agent_without_budget_chain(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_DISCOVERY_MODE", raising=False)
    mode, budget, limits = web_app._agent_discovery_configuration({"max_projects": 20})
    assert mode == "single_agent"
    assert budget.max_discovery_rounds >= 1
    assert limits.max_query_units >= 60


def test_body_can_still_request_multi_agent_budget_chain(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_DISCOVERY_MODE", raising=False)
    mode, _budget, _limits = web_app._agent_discovery_configuration(
        {"max_projects": 20, "discovery_mode": "multi_agent"}
    )
    assert mode == "multi_agent"


def test_fast_time_preference_uses_a_smaller_but_still_auditable_budget(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_DISCOVERY_MODE", raising=False)
    _mode, fast_budget, fast_limits = web_app._agent_discovery_configuration(
        {"max_projects": 20, "time_budget_preference": "fast"}
    )
    _mode, normal_budget, normal_limits = web_app._agent_discovery_configuration(
        {"max_projects": 20, "time_budget_preference": "multi_round"}
    )

    assert 1 <= fast_budget.max_discovery_rounds < normal_budget.max_discovery_rounds
    assert fast_budget.max_turns < normal_budget.max_turns
    assert fast_limits.max_query_units < normal_limits.max_query_units
    assert fast_limits.max_repository_requests < normal_limits.max_repository_requests
    assert fast_limits.max_elapsed_seconds < normal_limits.max_elapsed_seconds


def test_broad_human_protein_peptide_prompt_is_not_locked_to_immunopeptidomics() -> None:
    request = web_app._clean_dataset_request(
        {
            "prompt": "尽可能多地寻找人类蛋白质组/肽组数据，越多越好",
            "goal": "人类蛋白肽数据",
            "repository": "pride",
            "max_projects": 50,
            # Simulated parser/LLM defaults that previously over-constrained the Agent.
            "acquisition_mode": "dda",
            "labeling_strategy": "label_free",
            "constraint_provenance": {
                "goal": "user_or_parsed",
                "acquisition_mode": "user_or_parsed",
                "labeling_strategy": "user_or_parsed",
            },
        }
    )
    assert request.goal == "general"
    assert request.immunopeptide_scope is None
    assert request.quantity_scope == "portfolio"
    assert request.portfolio_size_preference == "maximize_qualified_projects"
    assert "repository" in request.hard_constraint_fields
    assert "goal" not in request.hard_constraint_fields
    assert "acquisition_mode" not in request.hard_constraint_fields
    assert "labeling_strategy" not in request.hard_constraint_fields
    assert "human" in {item.lower() for item in request.species} or "human" in {
        item.lower() for item in request.canonical_species
    }


def test_explicit_user_immunopeptidomics_and_dda_remain_hard() -> None:
    request = web_app._clean_dataset_request(
        {
            "prompt": "Find human immunopeptidomics HLA ligandome projects using DDA label-free",
            "goal": "immunopeptidomics",
            "repository": "pride",
            "species": ["human"],
            "acquisition_mode": "dda",
            "labeling_strategy": "label_free",
            "hard_constraint_fields": [
                "repository",
                "goal",
                "species",
                "species_policy",
                "acquisition_mode",
                "labeling_strategy",
            ],
            "constraint_provenance": {
                "goal": "user",
                "species": "user",
                "acquisition_mode": "user",
                "labeling_strategy": "user",
            },
        }
    )
    assert request.goal == "immunopeptidomics"
    assert request.immunopeptide_scope is not None
    assert "goal" in request.hard_constraint_fields
    assert "acquisition_mode" in request.hard_constraint_fields
    assert "labeling_strategy" in request.hard_constraint_fields


def test_execution_contract_preserves_soft_labeling_and_open_ended_controls() -> None:
    request = web_app._clean_dataset_request(
        {
            "prompt": "Explore TMT DDA projects and review as many as possible.",
            "repository": "pride",
            "acquisition_mode": "dda",
            "labeling_strategy": "tmt",
            "labeling_hard": False,
            "mixed_acquisition_policy": "allow",
            "quota_flexibility": "open_ended",
            "quantity_scope": "portfolio",
            "portfolio_size_preference": "maximize_qualified_projects",
            "run_horizon": "candidates_reviewed",
            "time_budget_preference": "multi_round",
            "on_safety_ceiling": "auto_continue_within_safety",
            "hard_constraint_fields": ["repository", "acquisition_mode"],
            "constraint_provenance": {
                "repository": "user",
                "acquisition_mode": "user",
                "labeling_strategy": "user_preference",
            },
        }
    )

    assert request.labeling_strategy == "TMT"
    assert request.labeling_hard is False
    assert "labeling_strategy" not in request.hard_constraint_fields
    assert "acquisition_mode" in request.hard_constraint_fields
    assert request.mixed_acquisition_policy == "allow"
    assert request.quota_flexibility == "open_ended"
    assert request.quantity_scope == "portfolio"
    assert request.portfolio_size_preference == "maximize_qualified_projects"
    assert request.harvest_all_qualified is True
    assert request.run_horizon == "candidates_reviewed"
    assert request.time_budget_preference == "multi_round"
    assert request.on_safety_ceiling == "auto_continue_within_safety"


def test_plan_only_confirmation_never_authorizes_repository_search() -> None:
    rejection = web_app._discovery_confirmation_rejection(
        {"grill_confirmed": True, "run_horizon": "plan_only"}
    )

    assert rejection is not None
    assert rejection["code"] == "discovery_plan_only"


def test_unwired_downstream_horizons_fail_closed_instead_of_running_plain_discovery() -> None:
    for horizon in ("ai_ready_table", "pre_release", "full_release"):
        rejection = web_app._discovery_confirmation_rejection(
            {"grill_confirmed": True, "run_horizon": horizon}
        )

        assert rejection is not None
        assert rejection["code"] == "discovery_downstream_horizon_required"
        assert horizon in rejection["error"]


def test_dialogue_agent_is_told_the_real_execution_boundary() -> None:
    prompt = web_app._discovery_grill_turn_system_prompt()

    assert "capability-honest" in prompt
    assert "plan_only (without repository access)" in prompt
    assert "ai_ready_table, pre_release, and full_release" in prompt
    assert "separate executor" in prompt


def test_first_class_exclusion_and_structured_constraint_are_deduplicated() -> None:
    request = web_app._clean_dataset_request(
        {
            "prompt": "排除永生化细胞系",
            "exclude_rules": ["永生化细胞系"],
            "scientific_constraints": [
                {
                    "id": "exclude_immortalized_cell_lines",
                    "label": "排除永生化细胞系",
                    "dimension": "cell_line_type",
                    "operator": "exclude",
                    "value": "immortalized",
                    "strength": "hard",
                    "scope": "project",
                    "evidence_required": True,
                    "source": "user",
                }
            ],
        }
    )

    assert [item.id for item in request.scientific_constraints] == [
        "exclude_immortalized_cell_lines"
    ]


def test_quality_first_instructions_reserve_grade_three_for_complete_intent_matches() -> None:
    request = DatasetRequest(repository="pride", max_projects=10)

    text = _quality_first_discovery_instructions(
        request,
        task_type=None,
        dynamic_budget=True,
    )

    assert "Grade 3 requires direct evidence for every central user-intent dimension" in text
    assert "cap the project at grade 2" in text
    assert "technically usable but topically off-target" in text
    assert "Project-level assay labels do not automatically apply to every selected file" in text
    assert "laboratory reputation" in text
    assert "disease-inducing or toxic exposure is not a therapeutic intervention" in text
    assert "cap an insult-only mechanism study at grade 2" in text


def test_quality_first_instructions_explain_auditable_delivery_and_constraint_contract() -> None:
    request = DatasetRequest(repository="pride", max_projects=10)

    text = _quality_first_discovery_instructions(
        request,
        task_type=None,
        dynamic_budget=False,
    )

    assert "positive file size" in text
    assert "download URL" in text
    assert "known file role" in text
    assert "Project-only evidence cannot make a file deliverable" in text
    assert "machine-evaluable observed_value" in text
    assert "search-only stage leave constraint_assessments empty" in text
    assert "Copy observed_value as a literal" in text
    assert "exact selected file identifier" in text
    assert "per_project_min_files" in text
    assert "per_project_min_samples" in text
    assert "portfolio-scoped" in text


def test_autonomous_instructions_do_not_require_budget_grant_chain() -> None:
    request = DatasetRequest(
        repository="pride",
        goal="general",
        acquisition_mode="unknown",
        labeling_strategy="unknown",
        quantity_scope="portfolio",
        portfolio_size_preference="maximize_qualified_projects",
        hard_constraint_fields=["repository"],
        max_projects=30,
        max_candidate_projects=300,
    )
    text = _quality_first_discovery_instructions(
        request,
        task_type=None,
        dynamic_budget=False,
    )
    assert "request_search_budget" not in text
    assert "search_repository_candidates_with_grant" not in text
    assert "Autonomous budget mode" in text
    assert "硬" in text or "hard ceilings" in text or "hard server ceilings" in text
    assert "越多越好" in text or "as many as possible" in text
