from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.mini_e2e_batch import validate_agent_runs_ai_ready_batch
from agent.cli import app


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)
    return path


def _write_mgf(path: Path, *, scan: int = 101) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "BEGIN IONS",
                f"TITLE=scan={scan}",
                f"SCANS={scan}",
                "PEPMASS=500.2",
                "CHARGE=2+",
                "100.1 1000",
                "200.2 500",
                "END IONS",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _write_failed_upstream_log(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "runtime.log").write_text(
        "\n".join(
            [
                "Process 'PhilosopherFilter' finished, exit code: 2",
                "Process returned non-zero exit code",
            ]
        ),
        encoding="utf-8",
    )


def test_validate_agent_runs_ai_ready_batch_summarizes_completed_and_blocked_runs(tmp_path: Path):
    run_rt = tmp_path / "run_rt"
    _write_failed_upstream_log(run_rt)
    _write_tsv(
        run_rt / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5, "PSM Q-Value": 0.001}],
    )
    run_denovo = tmp_path / "run_denovo"
    _write_tsv(
        run_denovo / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    _write_mgf(run_denovo / "fragpipe" / "exp" / "spectra.mgf")
    run_blocked = tmp_path / "run_blocked"
    _write_tsv(
        run_blocked / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )

    result = validate_agent_runs_ai_ready_batch(
        agent_run_dirs=[run_rt, run_denovo, run_blocked],
        task_types=["rt_prediction", "denovo"],
        output_dir=tmp_path / "batch",
    )

    assert result.status == "completed"
    assert len(result.run_results) == 3
    assert result.status_counts["completed"] == 2
    assert result.status_counts["blocked"] == 1
    assert result.ai_ready_outcome_counts["completed_from_usable_partial_outputs"] == 1
    assert result.task_status_counts["denovo"]["completed"] == 1
    assert result.task_status_counts["denovo"]["blocked"] == 2
    assert result.recovery_issue_counts["missing_peaklist"] == 2
    assert result.upstream_recovery_issue_counts["partial_outputs_available"] == 1
    assert result.upstream_recovery_issue_counts["none"] == 2
    partial = next(item for item in result.run_results if item.agent_run_dir.endswith("run_rt"))
    assert partial.ai_ready_outcome == "completed_from_usable_partial_outputs"
    assert partial.upstream_workflow_outcome == "failed_with_usable_partial_outputs"
    assert partial.upstream_usable_partial_outputs is True
    clean = next(item for item in result.run_results if item.agent_run_dir.endswith("run_denovo"))
    assert clean.ai_ready_outcome == "completed_from_clean_or_existing_outputs"
    assert clean.sample_class == "clean_full_completed"
    blocked = next(item for item in result.run_results if item.agent_run_dir.endswith("run_blocked"))
    assert blocked.primary_issue == "missing_peaklist"
    assert blocked.recovery_actions[0]["action_type"] == "generate_peaklist_and_retry"
    assert blocked.recovery_actions[0]["status"] == "blocked"
    assert blocked.recovery_report_json is not None
    assert Path(blocked.recovery_report_json).exists()
    assert Path(result.summary_path).exists()
    assert Path(result.csv_path).exists()
    assert Path(result.report_path).exists()
    assert result.benchmark_summary_json_path is not None
    assert result.benchmark_failure_taxonomy_path is not None
    benchmark_summary = json.loads(Path(result.benchmark_summary_json_path).read_text(encoding="utf-8"))
    assert benchmark_summary["run_count"] == 3
    assert benchmark_summary["repository_counts"]["unknown"] == 3
    assert benchmark_summary["diversity_summary"]["distinct_project_count"] == 0
    assert benchmark_summary["diversity_summary"]["distinct_source_file_count"] == 3
    assert benchmark_summary["acceptance"]["has_three_distinct_projects"] is False
    assert benchmark_summary["acceptance"]["has_three_distinct_source_files"] is True
    assert benchmark_summary["acceptance"]["has_partial_output_recovery"] is True
    assert benchmark_summary["task_rows_total"]["rt_prediction"] == 1
    assert benchmark_summary["task_rows_by_repository"]["unknown"]["rt_prediction"] == 1
    failure_taxonomy = json.loads(Path(result.benchmark_failure_taxonomy_path).read_text(encoding="utf-8"))
    assert "missing_peaklist" in failure_taxonomy["issue_counts"]
    assert "msdt_feature_missing" in failure_taxonomy["recovery_recommendations"]
    assert Path(result.benchmark_sample_manifest_csv_path).exists()
    assert Path(result.benchmark_report_path).exists()
    csv_text = Path(result.csv_path).read_text(encoding="utf-8")
    assert "primary_issue" in csv_text
    assert "upstream_primary_issue" in csv_text
    assert "ai_ready_outcome" in csv_text
    assert "upstream_workflow_outcome" in csv_text
    assert "missing_peaklist" in csv_text


def test_validate_agent_runs_ai_ready_batch_propagates_semantic_metadata(tmp_path: Path):
    run_dir = tmp_path / "run_semantic"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5, "PSM Q-Value": 0.001}],
    )
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "repository": "pride",
                "project_accession": "PXDSEM001",
                "file_name": "semantic_sample.mzML",
                "metadata": {
                    "projectDescription": {
                        "value": "Human phosphotyrosine enrichment with Ti4+-IMAC for kinase signaling."
                    },
                    "dataProcessingProtocol": {
                        "value": "DDA LC-MS/MS on a Q Exactive HF using higher energy collision-induced dissociation."
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "attributes.json").write_text(
        json.dumps(
            {
                "species": {"value": "Homo sapiens"},
                "instrument_family": {"value": "orbitrap"},
                "labeling_strategy": {"value": "TMT10plex"},
                "variable_mods": {"value": ["Phospho (S/T/Y)"]},
            }
        ),
        encoding="utf-8",
    )

    result = validate_agent_runs_ai_ready_batch(
        agent_run_dirs=[run_dir],
        task_types=["rt_prediction"],
        output_dir=tmp_path / "batch_semantic",
    )

    run = result.run_results[0]
    assert run.repository == "pride"
    assert run.project_accession == "PXDSEM001"
    assert run.source_file == "semantic_sample.mzML"
    assert run.labeling_strategy == "TMT"
    assert run.ptm_type == "phospho"
    assert run.semantic_metadata_confidence > 0
    assert "human" in run.canonical_species
    assert "9606" in run.organism_taxon_id
    assert "orbitrap" in run.instrument_families
    assert "HCD" in run.fragmentation_methods
    assert any("Ti4+-IMAC" in item for item in run.ptm_enrichment_methods)
    assert "species:human" in run.diversity_tags
    assert "labeling:TMT" in run.diversity_tags

    sample_manifest = json.loads(Path(result.benchmark_sample_manifest_json_path).read_text(encoding="utf-8"))
    row = sample_manifest["samples"][0]
    assert row["labeling_strategy"] == "TMT"
    assert row["ptm_type"] == "phospho"
    assert row["canonical_species"] == ["human"]
    assert "HCD" in row["fragmentation_methods"]


def test_validate_agent_runs_ai_ready_batch_cli_writes_summary(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5, "PSM Q-Value": 0.001}],
    )
    output_dir = tmp_path / "batch"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "validate-agent-runs-ai-ready-batch",
            "--agent-run-dir",
            str(run_dir),
            "--task-type",
            "rt_prediction",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "completed"
    assert payload["run_count"] == 1
    assert "recovery_issue_counts" in payload
    assert "upstream_recovery_issue_counts" in payload
    assert (output_dir / "mini_e2e_batch_summary.json").exists()
    assert (output_dir / "mini_e2e_batch_summary.csv").exists()
    assert (output_dir / "mini_e2e_batch_report.md").exists()
    assert (output_dir / "benchmark_summary.json").exists()
    assert (output_dir / "benchmark_report.md").exists()
