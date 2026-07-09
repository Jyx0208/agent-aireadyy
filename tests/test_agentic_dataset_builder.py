from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from typer.testing import CliRunner

from agent.cli import app
from agent.discovery.agentic import AgenticDiscoveryPlan, AgenticDiscoveryPlanner
from agent.discovery.agentic_runner import AgenticDiscoveryRound
from agent.discovery.dataset_builder import (
    AgenticDatasetBuildResult,
    AgenticDatasetBuildSummary,
    DatasetBuildIntent,
    run_agentic_dataset_builder,
)
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject


class FakeBuilderLLM:
    def __init__(self, *, bad_constraints: bool = False) -> None:
        self.bad_constraints = bad_constraints

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        return {
            "task_spec": {
                "task_type": "rt_prediction",
                "target_ptm": "acetyl" if self.bad_constraints else "phospho",
                "species_include": ["mouse"] if self.bad_constraints else ["human"],
                "acquisition_mode": "dia" if self.bad_constraints else "dda",
                "diversity_objectives": ["instrument_family", "fragmentation_method"],
                "required_evidence_level": "mixed_or_file",
            },
            "queries": [{"query": "human phosphoproteomics DDA", "purpose": "baseline"}],
            "trace": [
                {
                    "step": "initial_query_plan",
                    "thought": "Plan discovery queries for RT model training.",
                    "action": "plan_queries",
                }
            ],
            "warnings": [],
        }


def _file(name: str = "sample.raw", *, validity_status: str = "valid") -> DiscoveredFile:
    return DiscoveredFile(
        project_accession="PXD000001",
        project_title="Human phosphoproteomics DDA",
        file_name=name,
        download_url=f"https://ftp.pride.ebi.ac.uk/{name}",
        file_type=Path(name).suffix.lower() or ".raw",
        file_role="raw_acquisition",
        species=["human"],
        acquisition_mode="dda",
        ptm_type="phospho",
        validity_status=validity_status,  # type: ignore[arg-type]
        evidence_level="mixed",
        sdrf_match_status="matched",
        trust_score=0.9,
        file_score=50.0,
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
        lc_gradient_minutes=90.0,
    )


def _manifest(request: DatasetRequest, *, files: list[DiscoveredFile] | None = None) -> DatasetManifest:
    files = files if files is not None else [_file()]
    return DatasetManifest(
        request=request,
        projects=[DiscoveredProject(project_accession="PXD000001", project_title="Human phosphoproteomics DDA")]
        if files
        else [],
        files=files,
        summary={
            "selected_projects": 1 if files else 0,
            "selected_files": len(files),
            "validity_status_counts": {"valid": len(files)} if files else {},
            "unknown_counts": {},
            "evidence_level_distribution": {"mixed": len(files)} if files else {},
            "instrument_family_distribution": {"orbitrap": len(files)} if files else {},
        },
    )


def _discover_with_files(files: list[DiscoveredFile]):
    def fake_discover(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        return _manifest(request, files=files)

    return fake_discover


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)
    return path


def _write_mgf(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "BEGIN IONS",
                "TITLE=scan=101",
                "SCANS=101",
                "PEPMASS=500.2",
                "CHARGE=2+",
                "98.06004 1000",
                "147.11280 500",
                "END IONS",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_agentic_dataset_builder_preserves_hard_constraints_and_blocks_without_search_results(tmp_path: Path):
    request = DatasetRequest(species=["human"], ptm_type="phospho", acquisition_mode="dda", max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM(bad_constraints=True))

    result = run_agentic_dataset_builder(
        prompt="Find data for RT prediction",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "needs_search_results"
    assert "needs_search_results" in result.summary.blockers
    assert result.intent.request.species == ["human"]
    assert result.intent.task_spec["species_include"] == ["human"]
    assert result.intent.task_spec["target_ptm"] == "phospho"
    assert result.intent.task_spec["acquisition_mode"] == "dda"
    assert (tmp_path / "builder" / "agentic_dataset_build_plan.json").exists()
    assert (tmp_path / "builder" / "agentic_dataset_build_trace.json").exists()
    assert (tmp_path / "builder" / "agentic_dataset_build_report.md").exists()


def test_agentic_dataset_builder_blocks_when_no_handoff_ready_files(tmp_path: Path):
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        discovery_func=_discover_with_files([]),
    )

    assert result.summary.status == "blocked"
    assert "no_ready_for_batch_parameters_files" in result.summary.blockers
    assert result.summary.handoff_ready_files == 0


def test_agentic_dataset_builder_exports_rt_when_search_results_are_supplied(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Modified Peptide": "PEPTIDEK",
                "Charge": 2,
                "Retention": 12.5,
                "Spectrum File": "sample.raw",
                "PSM Q-Value": 0.001,
                "PeptideProphet Probability": 0.95,
            }
        ],
    )
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for RT prediction",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        search_results=[search_result],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "completed"
    assert result.summary.rt_rows_out == 1
    assert result.summary.rt_peptide_rows_out == 1
    assert Path(result.output_files["rt_train_parquet"]).exists()
    assert Path(result.output_files["rt_train_peptide_parquet"]).exists()
    summary = json.loads((tmp_path / "builder" / "agentic_dataset_build_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "completed"
    assert "rt_train_parquet" in summary["files"]


def test_agentic_dataset_builder_blocks_fragment_intensity_without_peaklist(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for fragment intensity prediction",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="fragment_intensity_prediction",
        search_results=[search_result],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "blocked"
    assert "needs_peaklist" in result.summary.blockers


def test_agentic_dataset_builder_exports_fragment_intensity_when_peaklist_is_supplied(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for fragment intensity prediction",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="fragment_intensity_prediction",
        search_results=[search_result],
        peaklists=[peaklist],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "completed"
    assert result.summary.fragment_intensity_rows_out == 1
    assert Path(result.output_files["fragment_intensity_train_parquet"]).exists()


def test_agentic_dataset_builder_blocks_psm_scoring_without_target_decoy_labels(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "Hyperscore": 42.0}],
    )
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for PSM scoring",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="psm_scoring",
        search_results=[search_result],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "blocked"
    assert "needs_target_decoy_labels" in result.summary.blockers


def test_agentic_dataset_builder_exports_psm_scoring_when_target_decoy_exists(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "Hyperscore": 42.0, "Decoy": "false"}],
    )
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for PSM scoring",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="psm_scoring",
        search_results=[search_result],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "completed"
    assert result.summary.psm_scoring_rows_out == 1
    assert Path(result.output_files["psm_scoring_train_parquet"]).exists()


def test_agentic_dataset_builder_blocks_chimeric_without_search_results(tmp_path: Path):
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for chimeric spectrum interpretation",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="chimeric_interpretation",
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "blocked"
    assert "needs_search_results" in result.summary.blockers


def test_agentic_dataset_builder_blocks_chimeric_without_peaklist(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001},
            {"Peptide": "SECONDK", "Charge": 3, "Spectrum": "scan=101", "PSM Q-Value": 0.001},
        ],
    )
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for chimeric spectrum interpretation",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="chimeric_interpretation",
        search_results=[search_result],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "blocked"
    assert "needs_peaklist" in result.summary.blockers


def test_agentic_dataset_builder_exports_chimeric_when_multi_peptide_labels_exist(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [
            {"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001},
            {"Peptide": "SECONDK", "Charge": 3, "Spectrum": "scan=101", "PSM Q-Value": 0.001},
        ],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for chimeric spectrum interpretation",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="chimeric_interpretation",
        search_results=[search_result],
        peaklists=[peaklist],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "completed"
    assert result.summary.chimeric_rows_out == 1
    assert Path(result.output_files["chimeric_train_parquet"]).exists()


def test_agentic_dataset_builder_blocks_chimeric_without_multi_peptide_labels(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for chimeric spectrum interpretation",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="chimeric_interpretation",
        search_results=[search_result],
        peaklists=[peaklist],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "blocked"
    assert result.summary.chimeric_rows_out == 0
    assert "no_multi_peptide_assignment" in result.summary.blockers


def test_agentic_dataset_builder_blocks_denovo_without_search_results(tmp_path: Path):
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for de novo sequencing",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="denovo",
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "needs_search_results"
    assert "needs_search_results" in result.summary.blockers


def test_agentic_dataset_builder_blocks_denovo_without_peaklist(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for de novo sequencing",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="denovo",
        search_results=[search_result],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "blocked"
    assert "needs_peaklist" in result.summary.blockers


def test_agentic_dataset_builder_exports_denovo_when_peaklist_is_supplied(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for de novo sequencing",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="denovo",
        search_results=[search_result],
        peaklists=[peaklist],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "completed"
    assert result.summary.denovo_rows_out == 1
    assert Path(result.output_files["denovo_train_parquet"]).exists()


def test_agentic_dataset_builder_can_auto_locate_inputs_from_search_dir(tmp_path: Path):
    search_dir = tmp_path / "search"
    search_dir.mkdir()
    _write_tsv(
        search_dir / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    _write_mgf(search_dir / "spectra.mgf")
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for de novo sequencing",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="denovo",
        search_dir=search_dir,
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "completed"
    assert result.summary.denovo_rows_out == 1
    assert "ai_ready_input_locations_json" in result.output_files
    assert Path(result.output_files["denovo_train_parquet"]).exists()


def test_agentic_dataset_builder_can_auto_locate_inputs_from_agent_run_dir(tmp_path: Path):
    run_dir = tmp_path / "agent_run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5, "PSM Q-Value": 0.001}],
    )
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for RT prediction",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="rt_prediction",
        agent_run_dir=run_dir,
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "completed"
    assert result.summary.rt_rows_out == 1
    assert "agent_run_input_locations_json" in result.output_files
    assert Path(result.output_files["rt_train_parquet"]).exists()


def test_agentic_dataset_builder_prefers_explicit_search_result_over_agent_run_dir(tmp_path: Path):
    run_dir = tmp_path / "agent_run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "RUNPEP", "Charge": 2, "Retention": 12.5, "PSM Q-Value": 0.001}],
    )
    explicit = _write_tsv(
        tmp_path / "explicit.tsv",
        [{"Peptide": "EXPLICIT", "Charge": 2, "Retention": 13.5, "PSM Q-Value": 0.001}],
    )
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for RT prediction",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="rt_prediction",
        search_results=[explicit],
        agent_run_dir=run_dir,
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "completed"
    assert "agent_run_input_locations_json" not in result.output_files
    preview = pd.read_csv(Path(result.output_files["rt_train_preview_csv"]))
    assert preview.loc[0, "peptide_sequence"] == "EXPLICIT"


def test_agentic_dataset_builder_blocks_ptm_denovo_without_search_results(tmp_path: Path):
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for PTM-aware de novo sequencing",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="ptm_denovo",
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "needs_search_results"
    assert "needs_search_results" in result.summary.blockers


def test_agentic_dataset_builder_blocks_ptm_denovo_without_peaklist(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEP[+80]TIDEK", "Charge": 2, "Spectrum": "scan=101"}],
    )
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for PTM-aware de novo sequencing",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="ptm_denovo",
        search_results=[search_result],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "blocked"
    assert "needs_peaklist" in result.summary.blockers


def test_agentic_dataset_builder_blocks_ptm_denovo_without_modified_labels(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101"}],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for PTM-aware de novo sequencing",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="ptm_denovo",
        search_results=[search_result],
        peaklists=[peaklist],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "blocked"
    assert "needs_modified_sequence_labels" in result.summary.blockers
    assert result.summary.ptm_denovo_rows_out == 0


def test_agentic_dataset_builder_exports_ptm_denovo_when_modified_labels_and_peaklist_are_supplied(tmp_path: Path):
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEP[+80]TIDEK", "Charge": 2, "Spectrum": "scan=101"}],
    )
    peaklist = _write_mgf(tmp_path / "spectra.mgf")
    request = DatasetRequest(max_projects=1, max_files=1)
    planner = AgenticDiscoveryPlanner(FakeBuilderLLM())

    result = run_agentic_dataset_builder(
        prompt="Find human phospho DDA data for PTM-aware de novo sequencing",
        request=request,
        output_dir=tmp_path / "builder",
        planner=planner,
        task_type="ptm_denovo",
        search_results=[search_result],
        peaklists=[peaklist],
        discovery_func=_discover_with_files([_file()]),
    )

    assert result.summary.status == "completed"
    assert result.summary.ptm_denovo_rows_out == 1
    assert Path(result.output_files["ptm_denovo_train_parquet"]).exists()
    assert (tmp_path / "builder" / "agentic_dataset_build_recommendations.json").exists()


def test_agentic_build_dataset_cli_reports_builder_summary(monkeypatch, tmp_path: Path):
    request = DatasetRequest(max_projects=1, max_files=1)
    discovery_plan = AgenticDiscoveryPlan(request=request, task_spec={}, queries=["human phosphoproteomics DDA"])
    fake_result = AgenticDatasetBuildResult(
        intent=DatasetBuildIntent(prompt="Find data", request=request, task_type="rt_prediction"),
        discovery_plan=discovery_plan,
        discovery_rounds=[AgenticDiscoveryRound(round_index=1, queries=["human phosphoproteomics DDA"])],
        summary=AgenticDatasetBuildSummary(
            status="ready_for_handoff",
            run_id="run-builder",
            task_type="rt_prediction",
            output_dir=str(tmp_path),
            next_step="submit_or_run_batch_parameters",
            selected_files=1,
            task_candidate_files=1,
            handoff_ready_files=1,
            files={"agentic_dataset_build_summary": str(tmp_path / "summary.json")},
        ),
        output_files={"agentic_dataset_build_summary": str(tmp_path / "summary.json")},
    )

    monkeypatch.setattr("agent.cli.default_agentic_discovery_planner", lambda: AgenticDiscoveryPlanner(FakeBuilderLLM()))
    monkeypatch.setattr("agent.cli.run_agentic_dataset_builder", lambda **kwargs: fake_result)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "agentic-build-dataset",
            "--prompt",
            "Find human phospho DDA data for RT prediction",
            "--output-dir",
            str(tmp_path / "builder"),
            "--max-projects",
            "1",
            "--max-files",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ready_for_handoff"
    assert payload["next_step"] == "submit_or_run_batch_parameters"
    assert payload["handoff_ready_files"] == 1
