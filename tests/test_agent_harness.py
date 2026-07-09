from __future__ import annotations

import json
from pathlib import Path

from agent.agent_core.harness import run_agent_harness
from agent.repositories.smoke import RepositorySmokeResult


def test_agent_harness_scores_deterministic_goal_cases(tmp_path: Path) -> None:
    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "rt",
                        "goal": "Find human DDA data for RT prediction",
                        "expected_task_type": "rt_prediction",
                        "expected_species": ["human"],
                        "expected_repository": "pride",
                        "expected_next_action_category": "discovery_plan",
                    },
                    {
                        "id": "recovery",
                        "goal": "Use partial outputs when full failed but usable partial outputs exist",
                        "expected_next_action_category": "recovery_plan",
                        "expected_blocker_recovery_class": "usable_partial_outputs",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_agent_harness(case_file=case_file, output_dir=tmp_path / "out", use_llm=False)

    assert result.status == "passed"
    assert result.passed == 2
    assert Path(result.files["agent_harness_summary_json"]).exists()
    assert Path(result.files["agent_decision_trace_json"]).exists()


def test_agent_harness_parses_generalized_metadata_goal(tmp_path: Path) -> None:
    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "rat_tmt_acetyl_fragment",
                        "goal": "Find rat TMT acetyl DDA data for fragment intensity with diversity",
                        "expected_task_type": "fragment_intensity_prediction",
                        "expected_species": ["rat"],
                        "expected_species_policy": "open",
                        "expected_ptm_type": "acetyl",
                        "expected_labeling_strategy": "TMT",
                        "expected_repository": "pride",
                        "expected_next_action_category": "discovery_plan",
                        "expected_hard_constraints": {
                            "ptm_type": "acetyl",
                            "labeling_strategy": "TMT",
                            "organism_taxon_id": ["10116"],
                        },
                    },
                    {
                        "id": "ecoli_ubiquitin_denovo",
                        "goal": "Find E. coli GlyGly DDA data for de novo",
                        "expected_task_type": "denovo",
                        "expected_species": ["e_coli"],
                        "expected_species_policy": "open",
                        "expected_ptm_type": "ubiquitin",
                        "expected_labeling_strategy": "label_free",
                    },
                    {
                        "id": "phosphotyrosine_semantic",
                        "goal": "Find phosphotyrosine antibody enrichment kinase signaling DDA data for RT prediction",
                        "expected_task_type": "rt_prediction",
                        "expected_ptm_type": "phospho",
                        "expected_species_policy": "open",
                        "expected_hard_constraints": {
                            "ptm_type": "phospho"
                        },
                    },
                    {
                        "id": "glyco_semantic",
                        "goal": "Find glycopeptide HILIC lectin enrichment DDA data for PSM scoring",
                        "expected_task_type": "psm_scoring",
                        "expected_ptm_type": "glyco",
                        "expected_species_policy": "open",
                    },
                    {
                        "id": "hla_ligandome_immunopeptidomics",
                        "goal": "Find HLA-A*02:01 class I HLA ligandome W6/32 immunoprecipitation data for de novo",
                        "expected_goal": "immunopeptidomics",
                        "expected_task_type": "denovo",
                        "expected_ptm_type": "unknown_ptm",
                        "expected_immunopeptide_scope": "immunopeptidomics",
                        "expected_species_policy": "open",
                        "expected_hard_constraints": {
                            "goal": "immunopeptidomics",
                            "immunopeptide_scope": "immunopeptidomics",
                            "hla_class": ["class_i"],
                            "hla_alleles": ["HLA-A*02:01"],
                        },
                    },
                    {
                        "id": "mouse_include_only",
                        "goal": "Find include only mouse DDA data for RT prediction",
                        "expected_task_type": "rt_prediction",
                        "expected_species": ["mouse"],
                        "expected_species_policy": "include_only",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_agent_harness(case_file=case_file, output_dir=tmp_path / "out", use_llm=False)

    assert result.status == "passed"
    assert result.passed == 6


def test_agent_harness_keeps_explicit_general_discovery_target_for_hla_drug_and_disease(tmp_path: Path) -> None:
    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "general_hla",
                        "goal": "General data search for HLA ligandome immunopeptidomics DDA datasets for de novo",
                        "expected_goal": "general",
                        "expected_task_type": "denovo",
                        "expected_ptm_type": "unknown_ptm",
                        "expected_species_policy": "open",
                        "expected_next_action_category": "discovery_plan",
                    },
                    {
                        "id": "general_drug",
                        "goal": "General discovery for drug treatment kinase inhibitor DDA data for fragment intensity",
                        "expected_goal": "general",
                        "expected_task_type": "fragment_intensity_prediction",
                        "expected_species_policy": "open",
                        "expected_next_action_category": "discovery_plan",
                    },
                    {
                        "id": "general_disease",
                        "goal": "Generic data search for disease cohort DDA proteomics data for PSM scoring",
                        "expected_goal": "general",
                        "expected_task_type": "psm_scoring",
                        "expected_species_policy": "open",
                        "expected_next_action_category": "discovery_plan",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_agent_harness(case_file=case_file, output_dir=tmp_path / "out", use_llm=False)

    assert result.status == "passed"
    assert result.passed == 3


def test_agent_harness_can_use_repository_smoke_runner(tmp_path: Path) -> None:
    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "massive",
                        "goal": "Smoke MassIVE known accession parameters",
                        "repository": "massive",
                        "input_value": "MSV000000001/raw/sample.mzML",
                        "expected_repository": "massive",
                        "expected_next_action_category": "repository_smoke",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_smoke_runner(**kwargs) -> RepositorySmokeResult:
        return RepositorySmokeResult(
            status="completed",
            repository="massive",
            requested_repository="massive",
            input_value=str(kwargs["input_value"]),
            mode="parameters",
            project_accession="MSV000000001",
        )

    result = run_agent_harness(
        case_file=case_file,
        output_dir=tmp_path / "out",
        use_llm=False,
        repository_smoke_runner=fake_smoke_runner,
    )

    assert result.status == "passed"
    assert result.case_results[0].repository_smoke is not None


def test_agent_harness_plans_cross_repository_discovery_and_blocker_recovery(tmp_path: Path) -> None:
    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "all_repositories",
                        "goal": "Search all repositories PRIDE MassIVE iProX for diverse DDA de novo data",
                        "repository": "auto",
                        "allowed_repositories": ["pride", "massive", "iprox"],
                        "expected_repository": "auto",
                        "expected_task_type": "denovo",
                        "expected_next_action_category": "discovery_plan",
                    },
                    {
                        "id": "refresh_iprox_index",
                        "goal": "Refresh iProX index to recover the iProX index missing repository blocker",
                        "repository": "iprox",
                        "expected_repository": "iprox",
                        "expected_next_action_category": "repository_blocker_recovery_plan",
                        "expected_blocker_recovery_class": "iprox_index_missing",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_agent_harness(case_file=case_file, output_dir=tmp_path / "out", use_llm=False)

    assert result.status == "passed"
    all_repo_case = next(row for row in result.case_results if row.id == "all_repositories")
    assert all(repository in all_repo_case.inferred["planned_repositories"] for repository in ["pride", "massive", "iprox"])
    refresh_case = next(row for row in result.case_results if row.id == "refresh_iprox_index")
    assert refresh_case.inferred["next_action_category"] == "repository_blocker_recovery_plan"


def test_agent_harness_routes_recipe_and_curation_goals(tmp_path: Path) -> None:
    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "leakage_split",
                        "goal": "Create a leakage-aware project split for this AI-ready recipe",
                        "expected_next_action_category": "leakage_aware_recipe_plan",
                    },
                    {
                        "id": "split_baseline",
                        "goal": "Compare agent-designed split against random split baseline for leakage",
                        "expected_next_action_category": "split_baseline_evaluation_plan",
                    },
                    {
                        "id": "hard_benchmark",
                        "goal": "Build a hard benchmark manifest for de novo and PSM scoring",
                        "expected_next_action_category": "hard_benchmark_plan",
                    },
                    {
                        "id": "counterfactual_benchmark",
                        "goal": "Build a counterfactual benchmark for positive, blocked, and decision boundary cases",
                        "expected_next_action_category": "counterfactual_benchmark_plan",
                    },
                    {
                        "id": "curation",
                        "goal": "Generate an active curation review queue for uncertain high-value candidates",
                        "expected_next_action_category": "active_curation_plan",
                    },
                    {
                        "id": "curation_memory_learning",
                        "goal": "Apply curation memory write back so future discovery can learn from manual review decisions",
                        "expected_next_action_category": "curation_memory_learning_plan",
                    },
                    {
                        "id": "data_value",
                        "goal": "Rank high value candidates worth processing before full workflow",
                        "expected_next_action_category": "data_value_selection_plan",
                    },
                    {
                        "id": "model_strategy_comparison",
                        "goal": "Compare agent-selected dataset strategy against random baseline model metrics",
                        "expected_next_action_category": "model_strategy_comparison_plan",
                    },
                    {
                        "id": "model_adapter_contract",
                        "goal": "Validate external model adapter contract before model-loop smoke",
                        "expected_next_action_category": "model_adapter_contract_plan",
                    },
                    {
                        "id": "model_gap",
                        "goal": "Use baseline model failure modes to plan model-informed dataset expansion",
                        "expected_next_action_category": "model_informed_expansion_plan",
                    },
                    {
                        "id": "model_discovery_request_review",
                        "goal": "Review model-informed discovery requests before running the next discovery round",
                        "expected_next_action_category": "model_informed_discovery_request_review",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_agent_harness(case_file=case_file, output_dir=tmp_path / "out", use_llm=False)

    assert result.status == "passed"
    assert result.passed == 11


def test_agent_harness_records_repository_smoke_blocker_class(tmp_path: Path) -> None:
    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "iprox_missing_index",
                        "goal": "Smoke iProX known accession parameters",
                        "repository": "iprox",
                        "input_value": "IPX0015463001/raw/sample.raw",
                        "expected_repository": "iprox",
                        "expected_next_action_category": "repository_smoke",
                        "expected_blocker_recovery_class": "iprox_index_missing",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_smoke_runner(**kwargs) -> RepositorySmokeResult:
        return RepositorySmokeResult(
            status="blocked",
            repository="iprox",
            requested_repository="iprox",
            input_value=str(kwargs["input_value"]),
            mode="parameters",
            blockers=["iprox_index_missing"],
        )

    result = run_agent_harness(
        case_file=case_file,
        output_dir=tmp_path / "out",
        use_llm=False,
        repository_smoke_runner=fake_smoke_runner,
    )

    assert result.status == "blocked"
    assert result.case_results[0].blockers == ["iprox_index_missing"]
    assert result.case_results[0].checks[-1]["passed"] is True


def test_agent_harness_blocks_llm_required_case_without_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    case_file = tmp_path / "cases.json"
    case_file.write_text(
        json.dumps({"cases": [{"id": "needs_llm", "goal": "Ambiguous proteomics goal", "requires_llm": True}]}),
        encoding="utf-8",
    )

    result = run_agent_harness(case_file=case_file, output_dir=tmp_path / "out", use_llm=True)

    assert result.status == "blocked"
    assert result.blocked == 1
    assert result.case_results[0].blockers == ["needs_llm"]
