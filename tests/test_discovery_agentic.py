from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agent.cli import app
from agent.discovery.agentic import (
    AgenticDiscoveryPlanner,
    build_agentic_self_check,
    default_discovery_llm_client,
)
from agent.discovery.agentic_runner import run_agentic_discovery
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject
from agent.discovery.task_readiness import annotate_manifest_task_readiness
from agent.discovery.task_profiles import get_task_profile, list_task_profiles


class FakeDiscoveryLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        return {
            "task_spec": {
                "task_type": "ptm_discovery",
                "target_ptm": "phospho",
                "species_include": ["human"],
                "acquisition_mode": "dda",
                "diversity_objectives": ["instrument_family", "fragmentation_method"],
                "required_evidence_level": "mixed_or_file",
            },
            "queries": [
                {"query": "human phosphoproteomics DDA", "purpose": "species and acquisition"},
                {"query": "TiO2 IMAC phosphopeptide", "purpose": "phospho enrichment"},
            ],
            "trace": [
                {
                    "step": "initial_query_plan",
                    "thought": "Use phospho enrichment and DDA terms.",
                    "action": "plan_queries",
                }
            ],
            "warnings": [],
        }


def test_agentic_planner_keeps_hard_constraints_and_adds_baseline_queries():
    request = DatasetRequest(species=["human"], max_candidate_projects=20)
    planner = AgenticDiscoveryPlanner(FakeDiscoveryLLM())

    plan = planner.plan(prompt="Find human phospho DDA data for model training", request=request)

    assert plan.request == request
    assert plan.task_spec.species_include == ["human"]
    assert plan.task_spec.target_ptm == "phospho"
    assert "human phosphoproteomics DDA" in plan.queries
    assert "phosphoproteomics" in plan.queries
    assert plan.trace[0].step == "initial_query_plan"


def test_agentic_planner_uses_task_profile_defaults():
    request = DatasetRequest(species=["human"], max_candidate_projects=20)
    planner = AgenticDiscoveryPlanner(FakeDiscoveryLLM())

    plan = planner.plan(
        prompt="Find human phospho DDA data for RT prediction",
        request=request,
        task_profile=get_task_profile("rt_prediction"),
    )

    assert plan.task_spec.task_type == "rt_prediction"
    assert plan.task_spec.required_labels == ["retention_time_labels"]
    assert "lc_gradient" in plan.task_spec.required_metadata
    assert "rt_train.parquet" == plan.task_spec.ai_ready_target_schema
    assert plan.task_spec.task_profile_status == "active"


def test_agentic_self_check_suggests_followup_queries_for_weak_summary():
    request = DatasetRequest(species=["human"])
    planner = AgenticDiscoveryPlanner(FakeDiscoveryLLM())
    plan = planner.plan(prompt="Find human phospho DDA data", request=request)
    manifest = DatasetManifest(
        request=request,
        summary={
            "selected_projects": 2,
            "selected_files": 10,
            "unknown_counts": {"fragmentation_method": 8},
            "evidence_level_distribution": {"project": 5, "mixed": 5},
            "instrument_family_distribution": {"orbitrap": 10},
            "validity_status_counts": {"weak_keep": 10},
        },
    )

    checked = build_agentic_self_check(plan, manifest)

    assert "fragmentation_diversity_or_metadata_weak" in checked.warnings
    assert "project_level_evidence_overrepresented" in checked.warnings
    assert "instrument_diversity_low" in checked.warnings
    assert "phosphoproteomics CID" in checked.suggested_next_queries
    assert checked.trace[-1].step == "post_discovery_self_check"


def test_agentic_runner_executes_second_round_for_weak_summary():
    request = DatasetRequest(species=["human"], max_projects=2, max_files=10, max_candidate_projects=20)
    planner = AgenticDiscoveryPlanner(FakeDiscoveryLLM())
    calls: list[list[str]] = []

    def fake_discover(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        calls.append(list(queries or []))
        return DatasetManifest(
            request=request,
            summary={
                "selected_projects": 2,
                "selected_files": 10,
                "unknown_counts": {"fragmentation_method": 8},
                "evidence_level_distribution": {"project": 5, "mixed": 5},
                "instrument_family_distribution": {"orbitrap": 10},
                "validity_status_counts": {"weak_keep": 10},
            },
        )

    result = run_agentic_discovery(
        request=request,
        planner=planner,
        prompt="Find human phospho DDA data",
        max_rounds=2,
        discovery_func=fake_discover,
    )

    assert len(result.rounds) == 2
    assert len(calls) == 2
    assert "phosphoproteomics CID" in calls[1]
    assert result.plan.request == request


def test_agentic_runner_does_not_execute_second_round_for_good_summary():
    request = DatasetRequest(species=["human"], max_projects=2, max_files=10, max_candidate_projects=20)
    planner = AgenticDiscoveryPlanner(FakeDiscoveryLLM())
    calls = 0

    def fake_discover(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        nonlocal calls
        calls += 1
        return DatasetManifest(
            request=request,
            summary={
                "selected_projects": 2,
                "selected_files": 10,
                "unknown_counts": {"fragmentation_method": 0},
                "evidence_level_distribution": {"mixed": 10},
                "instrument_family_distribution": {"orbitrap": 5, "timsTOF": 5},
                "validity_status_counts": {"valid": 10},
            },
        )

    result = run_agentic_discovery(
        request=request,
        planner=planner,
        prompt="Find human phospho DDA data",
        max_rounds=2,
        discovery_func=fake_discover,
    )

    assert calls == 1
    assert len(result.rounds) == 1
    assert result.plan.suggested_next_queries == []


def test_task_readiness_marks_rt_candidate_weak_ready_with_downstream_label_note():
    request = DatasetRequest(species=["human"])
    file = DiscoveredFile(
        project_accession="PXD000001",
        file_name="HeLa.raw",
        file_type=".raw",
        file_role="raw_acquisition",
        species=["human"],
        acquisition_mode="dda",
        validity_status="valid",
        evidence_level="mixed",
        instrument_families=["orbitrap"],
        lc_gradient_minutes=90.0,
    )
    manifest = annotate_manifest_task_readiness(
        DatasetManifest(request=request, files=[file]),
        "rt_prediction",
    )

    annotated = manifest.files[0]
    assert annotated.task_type == "rt_prediction"
    assert annotated.task_profile == "Retention time prediction"
    assert annotated.task_readiness_status == "weak_ready"
    assert "requires_downstream_search_export_for_rt_labels" in annotated.task_readiness_reasons
    assert "retention_time_labels" in annotated.missing_task_requirements
    assert annotated.label_source_status == "requires_downstream_generation"
    assert annotated.spectra_requirement_status == "satisfied"
    assert annotated.metadata_requirement_status == "partial"
    assert annotated.ai_ready_target_schema == "rt_train.parquet"
    assert "rt_export" in annotated.next_pipeline_steps


def test_task_readiness_degrades_fragment_intensity_when_fragmentation_unknown():
    request = DatasetRequest(species=["human"])
    file = DiscoveredFile(
        project_accession="PXD000001",
        file_name="HeLa.mzML",
        file_type=".mzML",
        file_role="raw_acquisition",
        species=["human"],
        acquisition_mode="dda",
        validity_status="valid",
        evidence_level="mixed",
        instrument_families=["orbitrap"],
    )
    manifest = annotate_manifest_task_readiness(
        DatasetManifest(request=request, files=[file]),
        "fragment_intensity_prediction",
    )

    annotated = manifest.files[0]
    assert annotated.task_readiness_status == "weak_ready"
    assert "fragmentation_unknown" in annotated.task_readiness_reasons
    assert annotated.metadata_requirement_status == "partial"
    assert annotated.ai_ready_target_schema == "fragment_intensity_train.parquet"


def test_task_readiness_never_marks_raw_psm_scoring_ready_without_labels():
    request = DatasetRequest(species=["human"])
    file = DiscoveredFile(
        project_accession="PXD000001",
        file_name="HeLa.raw",
        file_type=".raw",
        file_role="raw_acquisition",
        species=["human"],
        acquisition_mode="dda",
        validity_status="valid",
        evidence_level="mixed",
    )
    manifest = annotate_manifest_task_readiness(
        DatasetManifest(request=request, files=[file]),
        "psm_scoring",
    )

    annotated = manifest.files[0]
    assert annotated.task_readiness_status == "weak_ready"
    assert "target_decoy_psm_labels" in annotated.missing_task_requirements
    assert annotated.label_source_status == "requires_downstream_generation"
    assert annotated.ai_ready_target_schema == "psm_scoring_train.parquet"


def test_task_profiles_register_active_and_planned_tasks():
    profiles = {profile.task_type: profile for profile in list_task_profiles()}

    assert profiles["rt_prediction"].implementation_status == "active"
    assert profiles["fragment_intensity_prediction"].ai_ready_target_schema == "fragment_intensity_train.parquet"
    assert profiles["psm_scoring"].required_labels == ["target_decoy_psm_labels"]
    assert profiles["denovo"].implementation_status == "active"
    assert profiles["ptm_denovo"].implementation_status == "active"
    assert profiles["chimeric_interpretation"].implementation_status == "active"
    assert get_task_profile("de-novo").task_type == "denovo"


def test_chimeric_task_profile_is_active_but_not_ready_without_isolation_or_labels():
    request = DatasetRequest(species=["human"])
    file = DiscoveredFile(
        project_accession="PXD000001",
        file_name="HeLa.raw",
        file_type=".raw",
        file_role="raw_acquisition",
        species=["human"],
        acquisition_mode="dda",
        validity_status="valid",
        evidence_level="mixed",
    )

    manifest = annotate_manifest_task_readiness(DatasetManifest(request=request, files=[file]), "chimeric_interpretation")

    annotated = manifest.files[0]
    assert annotated.task_type == "chimeric_interpretation"
    assert annotated.task_readiness_status == "not_ready"
    assert "requires_spectrum_level_isolation_window" in annotated.task_readiness_reasons
    assert "multi_peptide_spectrum_labels" in annotated.missing_task_requirements
    assert annotated.ai_ready_target_schema == "chimeric_train.parquet"


def test_denovo_task_profile_is_active_and_weak_ready_before_export():
    request = DatasetRequest(species=["human"])
    file = DiscoveredFile(
        project_accession="PXD000001",
        file_name="HeLa.raw",
        file_type=".raw",
        file_role="raw_acquisition",
        species=["human"],
        acquisition_mode="dda",
        validity_status="valid",
        evidence_level="mixed",
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
    )

    manifest = annotate_manifest_task_readiness(DatasetManifest(request=request, files=[file]), "denovo")

    annotated = manifest.files[0]
    assert annotated.task_type == "denovo"
    assert annotated.task_readiness_status == "weak_ready"
    assert "peptide_sequence_labels" in annotated.missing_task_requirements
    assert "requires_downstream_search_export_for_peptide_sequence_labels" in annotated.task_readiness_reasons
    assert annotated.ai_ready_target_schema == "denovo_train.parquet"


def test_default_discovery_llm_reads_deepseek_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    client = default_discovery_llm_client()

    assert client is not None


def test_discovery_plan_cli_writes_plan(monkeypatch, tmp_path: Path):
    fake_planner = AgenticDiscoveryPlanner(FakeDiscoveryLLM())
    monkeypatch.setattr("agent.cli.default_agentic_discovery_planner", lambda: fake_planner)
    output_json = tmp_path / "plan.json"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "discovery-plan",
            "--prompt",
            "Find human phospho DDA data",
            "--max-candidate-projects",
            "20",
            "--output-json",
            str(output_json),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert "human phosphoproteomics DDA" in payload["queries"]
    assert payload["task_spec"]["species_include"] == ["human"]


def test_discover_dataset_cli_agentic_writes_trace(monkeypatch, tmp_path: Path):
    fake_planner = AgenticDiscoveryPlanner(FakeDiscoveryLLM())
    monkeypatch.setattr("agent.cli.default_agentic_discovery_planner", lambda: fake_planner)

    def fake_discover(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        project = DiscoveredProject(project_accession="PXD000001", project_title="Human phosphoproteomics")
        file = DiscoveredFile(
            project_accession="PXD000001",
            project_title="Human phosphoproteomics",
            file_name="HeLa_phospho_DDA.raw",
            download_url="https://ftp.pride.ebi.ac.uk/HeLa_phospho_DDA.raw",
            file_type=".raw",
            file_role="raw_acquisition",
            species=["human"],
            acquisition_mode="dda",
            ptm_type="phospho",
            validity_status="valid",
            validity_reasons=["strong_ptm_evidence"],
            evidence_level="mixed",
        )
        return DatasetManifest(
            request=request,
            projects=[project],
            files=[file],
            summary={
                "selected_projects": 1,
                "selected_files": 1,
                "validity_status_counts": {"valid": 1},
                "unknown_counts": {},
                "evidence_level_distribution": {"mixed": 1},
                "instrument_family_distribution": {},
                "queries": queries or [],
            },
        )

    monkeypatch.setattr("agent.cli.discover_pride_dataset", fake_discover)
    runner = CliRunner()
    output_dir = tmp_path / "agentic_discovery"

    result = runner.invoke(
        app,
        [
            "discover-dataset",
            "--agentic",
            "--prompt",
            "Find human phospho DDA data",
            "--max-projects",
            "1",
            "--max-files",
            "1",
            "--max-candidate-projects",
            "20",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert (output_dir / "agentic_plan.json").exists()
    assert (output_dir / "agentic_rounds.json").exists()
    assert (output_dir / "dataset_manifest.json").exists()
    payload = json.loads((output_dir / "agentic_plan.json").read_text(encoding="utf-8"))
    assert payload["trace"][-1]["step"] == "post_discovery_self_check"
    manifest = json.loads((output_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["summary"]["agentic"]["enabled"] is True
    assert manifest["summary"]["agentic"]["rounds"] == 1


def test_discover_dataset_cli_task_type_writes_task_ready_exports(monkeypatch, tmp_path: Path):
    def fake_discover(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        file = DiscoveredFile(
            project_accession="PXD000001",
            file_name="HeLa_01.raw",
            file_type=".raw",
            file_role="raw_acquisition",
            species=["human"],
            acquisition_mode="dda",
            validity_status="valid",
            evidence_level="mixed",
            instrument_families=["orbitrap"],
            lc_gradient_minutes=90.0,
        )
        return DatasetManifest(
            request=request,
            files=[file],
            summary={"selected_projects": 1, "selected_files": 1},
        )

    monkeypatch.setattr("agent.cli.discover_pride_dataset", fake_discover)
    runner = CliRunner()
    output_dir = tmp_path / "task_ready_discovery"

    result = runner.invoke(
        app,
        [
            "discover-dataset",
            "--task-type",
            "rt_prediction",
            "--max-projects",
            "1",
            "--max-files",
            "1",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    manifest = json.loads((output_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["summary"]["task_readiness"]["status_counts"]["weak_ready"] == 1
    assert manifest["files"][0]["task_readiness_status"] == "weak_ready"
    assert (output_dir / "dataset_manifest_task_ready.csv").exists()
    assert (output_dir / "batch_inputs_task_ready.txt").read_text(encoding="utf-8").strip() == "HeLa_01.raw"


def test_normalize_task_type_browse_only_is_optional():
    from agent.discovery.task_profiles import normalize_task_type

    assert normalize_task_type("browse_only") is None
    assert normalize_task_type("") is None
    assert normalize_task_type(None) is None
    assert normalize_task_type("rt_prediction") == "rt_prediction"
    assert normalize_task_type("de-novo") == "denovo"
