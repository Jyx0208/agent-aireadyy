from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.data_scientist_loop import run_data_scientist_agent_loop
from agent.cli import app


def _write_parquet(path: Path, peptide: str, *, charge: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "project_accession": "PXDLOOP001",
                "source_file": path.parent.name,
                "peptide_sequence": peptide,
                "modified_sequence": peptide,
                "charge": charge,
                "retention_time": 12.5,
            }
        ]
    ).to_parquet(path, index=False)
    return path


def _write_batch(batch_dir: Path) -> None:
    parquet_a = _write_parquet(batch_dir / "01_run_a" / "task_runs" / "denovo" / "denovo_train.parquet", "PEPTIDEK")
    parquet_b = _write_parquet(batch_dir / "02_run_b" / "task_runs" / "denovo" / "denovo_train.parquet", "HARDPEPTIDEK", charge=4)
    batch_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_results": [
            {
                "run_name": "run_a",
                "output_dir": str(batch_dir / "01_run_a"),
                "project_accession": "PXDLOOP001",
                "source_file": "a.mzML",
                "full_status": "completed",
                "ai_ready_outcome": "completed",
                "species_policy": "open",
                "canonical_species": ["human"],
                "organism_taxon_id": ["9606"],
                "acquisition_mode": "dda",
                "ptm_type": "phospho",
                "ptm_subtype": "pTyr",
                "ptm_evidence_terms": ["phosphotyrosine enrichment", "kinase signaling"],
                "ptm_enrichment_methods": ["Ti4+-IMAC", "anti-phosphotyrosine antibody enrichment"],
                "semantic_metadata_confidence": 0.91,
                "modification_scope": "phospho",
                "labeling_strategy": "TMT10plex",
                "instrument_families": ["orbitrap"],
                "fragmentation_methods": ["HCD"],
                "diversity_tags": ["species:human", "instrument:orbitrap", "fragmentation:HCD", "ptm:phospho", "labeling:TMT"],
                "task_statuses": {"denovo": "completed"},
                "rows_out": {"denovo": 1},
                "task_files": {"denovo": {"denovo_train_parquet": str(parquet_a)}},
                "blockers": [],
                "warnings": [],
            },
            {
                "run_name": "run_b",
                "output_dir": str(batch_dir / "02_run_b"),
                "project_accession": "PXDLOOP002",
                "source_file": "b.mzML",
                "full_status": "failed_with_usable_partial_outputs",
                "ai_ready_outcome": "completed_from_usable_partial_outputs",
                "task_statuses": {"denovo": "completed"},
                "rows_out": {"denovo": 1},
                "task_files": {"denovo": {"denovo_train_parquet": str(parquet_b)}},
                "blockers": [],
                "warnings": ["partial_output_recovery"],
            },
            {
                "run_name": "run_c",
                "output_dir": str(batch_dir / "03_run_c"),
                "project_accession": "PXDLOOP003",
                "source_file": "c.mzML",
                "full_status": "failed",
                "ai_ready_outcome": "blocked",
                "task_statuses": {"denovo": "blocked"},
                "rows_out": {"denovo": 0},
                "task_files": {"denovo": {}},
                "blockers": ["zero_psm"],
                "warnings": [],
            },
        ]
    }
    (batch_dir / "mini_e2e_batch_summary.json").write_text(json.dumps(payload), encoding="utf-8")
    (batch_dir / "benchmark_summary.json").write_text(
        json.dumps(
            {
                "status": "benchmark_complete",
                "run_count": 3,
                "acceptance": {
                    "benchmark_complete": True,
                    "has_clean_full_completed": True,
                    "has_partial_output_recovery": True,
                    "has_blocked_or_review_case": True,
                },
            }
        ),
        encoding="utf-8",
    )


def test_run_data_scientist_agent_loop_chains_recipe_model_report_and_alignment(tmp_path: Path) -> None:
    batch_dir = tmp_path / "batch"
    _write_batch(batch_dir)

    result = run_data_scientist_agent_loop(
        batch_dir=batch_dir,
        output_dir=tmp_path / "loop",
        task_type="denovo",
        split_strategy="file_disjoint",
    )

    assert result.status in {"completed", "completed_with_alignment_gaps"}
    assert result.recipe_status == "ready"
    assert result.model_loop_status == "completed"
    assert Path(result.files["data_scientist_agent_loop_summary_json"]).exists()
    assert Path(result.files["recipe:counterfactual_benchmark_manifest_json"]).exists()
    assert Path(result.files["final_report:real_data_scientist_agent_report_md"]).exists()
    assert Path(result.files["guidance:guidance_alignment_report_json"]).exists()
    summary = json.loads(Path(result.files["data_scientist_agent_loop_summary_json"]).read_text(encoding="utf-8"))
    assert summary["selected_count"] == 2
    assert summary["model_failure_mode_count"] >= 0
    assert summary["curation_memory_update_status"] == "not_run"
    assert summary["curation_memory_update_reason"] == "awaiting_human_decisions"
    assert set(summary["model_informed_repository_plan"]["planned_repositories"]) >= {"pride", "massive", "iprox"}
    assert summary["model_informed_repository_plan"]["repository_strategy"] == "multi_repository"
    guidance = json.loads(Path(result.files["guidance:guidance_alignment_report_json"]).read_text(encoding="utf-8"))
    semantic = next(item for item in guidance["requirements"] if item["name"] == "semantic_metadata_interpretation_and_policy")
    assert semantic["status"] == "achieved"
    report = Path(result.files["data_scientist_agent_loop_report_md"]).read_text(encoding="utf-8")
    assert "Model-informed Repository Plan" in report
    assert "pride, massive, iprox" in report


def test_run_data_scientist_agent_loop_surfaces_repository_blocker_actions(tmp_path: Path) -> None:
    batch_dir = tmp_path / "batch"
    _write_batch(batch_dir)
    discovery_dir = tmp_path / "discovery"
    discovery_dir.mkdir()
    discovery_manifest = discovery_dir / "dataset_manifest.json"
    discovery_manifest.write_text(
        json.dumps(
            {
                "files": [
                    {"repository": "pride", "project_accession": "PXDLOOP001", "file_name": "a.mzML"},
                    {"repository": "pride", "project_accession": "PXDLOOP002", "file_name": "b.mzML"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (discovery_dir / "repository_audit.json").write_text(
        json.dumps(
            {
                "requested_repository": "auto",
                "repositories_attempted": ["pride", "iprox"],
                "repository_counts": {"pride": 2},
                "rows": [
                    {"repository": "pride", "status": "completed", "selected_files": 2},
                    {
                        "repository": "iprox",
                        "status": "blocked",
                        "support_status": "blocked",
                        "selected_files": 0,
                        "blocker": "iprox_index_missing",
                        "next_step": "refresh_iprox_index_or_set_agent_iprox_index_xlsx",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_data_scientist_agent_loop(
        batch_dir=batch_dir,
        output_dir=tmp_path / "loop_with_repo_audit",
        task_type="denovo",
        discovery_manifest=discovery_manifest,
    )

    summary = json.loads(Path(result.files["data_scientist_agent_loop_summary_json"]).read_text(encoding="utf-8"))
    assert summary["repository_blocker_action_count"] == 1
    assert any(
        action["action"] == "refresh_iprox_index_or_set_agent_iprox_index_xlsx"
        and action["repository"] == "iprox"
        for action in summary["repository_blocker_actions"]
    )
    report = Path(result.files["data_scientist_agent_loop_report_md"]).read_text(encoding="utf-8")
    assert "Repository blocker actions" in report
    assert "refresh_iprox_index_or_set_agent_iprox_index_xlsx" in report


def test_run_data_scientist_agent_loop_uses_repository_smoke_dirs_for_audit(tmp_path: Path) -> None:
    batch_dir = tmp_path / "batch"
    _write_batch(batch_dir)
    massive_smoke = tmp_path / "massive_smoke"
    massive_smoke.mkdir()
    (massive_smoke / "repository_smoke_summary.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "repository": "massive",
                "requested_repository": "massive",
                "project_accession": "MSV000000001",
                "native_accession": "MSV000000001",
                "matched_file": "raw/sample.raw",
                "download_url": "https://example.test/sample.raw",
                "transfer_method": "https",
                "next_step": "run_one_click_parameters_or_prepare_when_ready",
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    iprox_smoke = tmp_path / "iprox_smoke"
    iprox_smoke.mkdir()
    (iprox_smoke / "repository_smoke_summary.json").write_text(
        json.dumps(
            {
                "status": "blocked",
                "repository": "iprox",
                "requested_repository": "iprox",
                "next_step": "set_agent_iprox_index_xlsx",
                "blockers": ["iprox_index_missing"],
            }
        ),
        encoding="utf-8",
    )

    result = run_data_scientist_agent_loop(
        batch_dir=batch_dir,
        output_dir=tmp_path / "loop_with_smoke_audit",
        task_type="denovo",
        repository_smoke_dirs=[massive_smoke, iprox_smoke],
    )

    summary = json.loads(Path(result.files["data_scientist_agent_loop_summary_json"]).read_text(encoding="utf-8"))
    assert "repository_audit:repository_audit_json" in summary["files"]
    guidance = json.loads(Path(result.files["guidance:guidance_alignment_report_json"]).read_text(encoding="utf-8"))
    multi_repo = next(item for item in guidance["requirements"] if item["name"] == "multi_repository_discovery_and_audit")
    assert multi_repo["status"] == "achieved"
    recipe = json.loads(Path(result.files["recipe:dataset_recipe_json"]).read_text(encoding="utf-8"))
    audit = recipe["repository_audit"]
    assert audit["repositories_attempted"] == ["pride", "massive", "iprox"]
    assert any(row["repository"] == "iprox" and row["blocker"] == "iprox_index_missing" for row in audit["rows"])


def test_run_data_scientist_agent_loop_can_write_curation_memory_with_explicit_decision(tmp_path: Path) -> None:
    batch_dir = tmp_path / "batch"
    _write_batch(batch_dir)

    result = run_data_scientist_agent_loop(
        batch_dir=batch_dir,
        output_dir=tmp_path / "loop_with_curation_memory",
        task_type="denovo",
        curation_default_decision="needs_review",
        curation_memory_dir=tmp_path / "discovery_memory",
    )

    summary = json.loads(Path(result.files["data_scientist_agent_loop_summary_json"]).read_text(encoding="utf-8"))
    assert summary["curation_memory_update_status"] == "updated"
    assert summary["curation_memory_imported_decision_count"] >= 1
    assert Path(result.files["curation_memory:curation_memory_update_json"]).exists()
    guidance = json.loads(Path(result.files["guidance:guidance_alignment_report_json"]).read_text(encoding="utf-8"))
    memory_loop = next(item for item in guidance["requirements"] if item["name"] == "curation_memory_feedback_loop")
    assert memory_loop["status"] == "achieved"
    assert any("curation_memory_imported_decisions=" in item for item in memory_loop["evidence"])
    ranking_evidence = next(item for item in memory_loop["evidence"] if item.startswith("ranking_rows_with_memory_action="))
    assert int(ranking_evidence.split("=", 1)[1]) > 0


def test_run_data_scientist_agent_loop_cli(tmp_path: Path) -> None:
    batch_dir = tmp_path / "batch"
    _write_batch(batch_dir)
    output_dir = tmp_path / "cli_loop"

    result = CliRunner().invoke(
        app,
        [
            "run-data-scientist-agent-loop",
            "--batch-dir",
            str(batch_dir),
            "--output-dir",
            str(output_dir),
            "--task-type",
            "denovo",
            "--curation-default-decision",
            "needs_review",
            "--curation-memory-dir",
            str(tmp_path / "cli_memory"),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["recipe_status"] == "ready"
    assert "curation_memory:curation_memory_update_json" in payload["files"]
    assert (output_dir / "data_scientist_agent_loop_summary.json").exists()
