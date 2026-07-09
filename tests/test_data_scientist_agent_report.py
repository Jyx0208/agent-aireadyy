from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.curation_memory import apply_curation_decisions_to_memory
from agent.ai_ready.data_scientist_report import make_data_scientist_agent_report
from agent.ai_ready.dataset_recipe import make_dataset_recipe
from agent.ai_ready.model_loop import run_dataset_model_loop
from agent.ai_ready.model_strategy_comparison import compare_dataset_model_strategies
from agent.cli import app


def _write_parquet(path: Path, peptide: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "peptide_sequence": peptide,
                "modified_sequence": peptide,
                "protein_accession": "P_TEST",
                "charge": 2,
                "retention_time": 11.0,
                "q_value": 0.009,
                "target_decoy": "target",
            }
        ]
    ).to_parquet(path, index=False)
    return path


def _write_batch(batch_dir: Path) -> Path:
    parquet = _write_parquet(batch_dir / "01_run" / "task_runs" / "denovo" / "denovo_train.parquet", "PEPTIDEK")
    payload = {
        "run_results": [
            {
                "run_name": "run_1",
                "agent_run_dir": str(batch_dir / "run_1"),
                "output_dir": str(batch_dir / "01_run"),
                "repository": "pride",
                "project_accession": "PXDREPORT",
                "source_file": "report.mzML",
                "sample_name": "sample_1",
                "condition": "control",
                "lab": "lab_a",
                "full_status": "completed",
                "ai_ready_outcome": "completed",
                "metadata_quality": "available",
                "task_statuses": {"denovo": "completed"},
                "rows_out": {"denovo": 1},
                "task_files": {"denovo": {"denovo_train_parquet": str(parquet)}},
                "blockers": [],
                "warnings": [],
            }
        ]
    }
    batch_dir.mkdir(parents=True, exist_ok=True)
    path = batch_dir / "mini_e2e_batch_summary.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_make_data_scientist_agent_report_summarizes_recipe_and_model_loop(tmp_path: Path) -> None:
    batch_dir = tmp_path / "batch"
    _write_batch(batch_dir)
    recipe_dir = tmp_path / "recipe"
    make_dataset_recipe(batch_dir=batch_dir, output_dir=recipe_dir)
    apply_curation_decisions_to_memory(
        curation_queue=recipe_dir / "curation_queue.json",
        output_dir=recipe_dir,
        memory_dir=tmp_path / "memory",
        default_decision="needs_review",
        run_id="report_curation",
    )
    model_loop_dir = tmp_path / "model_loop"
    run_dataset_model_loop(recipe_dir=recipe_dir, task_type="denovo", output_dir=model_loop_dir)
    strategy_case = tmp_path / "strategy_case.json"
    strategy_case.write_text(
        json.dumps(
            {
                "primary_metric": "accuracy",
                "strategies": [
                    {"strategy": "agent_data_value", "metrics": {"heldout_project": {"accuracy": 0.8}}},
                    {"strategy": "random_baseline", "metrics": {"heldout_project": {"accuracy": 0.7}}},
                ],
            }
        ),
        encoding="utf-8",
    )
    compare_dataset_model_strategies(case_file=strategy_case, output_dir=model_loop_dir)
    discovery_dir = tmp_path / "discovery"
    discovery_dir.mkdir()
    discovery_manifest = discovery_dir / "dataset_manifest.json"
    discovery_manifest.write_text(
        json.dumps({"files": [{"project_accession": "PXDREPORT", "file_name": "report.mzML"}]}),
        encoding="utf-8",
    )
    (discovery_dir / "task_ai_readiness_matrix.json").write_text(
        json.dumps(
            {
                "task_types": ["rt_prediction", "denovo"],
                "rows": [
                    {"task_type": "rt_prediction", "task_ai_readiness_band": "ready"},
                    {"task_type": "denovo", "task_ai_readiness_band": "weak_ready"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (discovery_dir / "data_value_ranking.json").write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "project_accession": "PXDREPORT",
                        "file_name": "report.mzML",
                        "task_type": "denovo",
                        "data_value_score": 0.9,
                        "data_value_action": "process",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (discovery_dir / "data_value_strategy_eval.json").write_text(
        json.dumps(
            {
                "interpretation": "agent_data_value_selection_outperforms_proxy_baselines",
                "agent_minus_best_baseline": 0.12,
                "best_baseline_strategy": "manual_rule_baseline",
            }
        ),
        encoding="utf-8",
    )
    (discovery_dir / "repository_audit.json").write_text(
        json.dumps(
            {
                "requested_repository": "auto",
                "repositories_attempted": ["pride", "massive", "iprox"],
                "repository_counts": {"pride": 1},
                "rows": [
                    {
                        "repository": "pride",
                        "status": "completed",
                        "support_status": "remote_discovery_v1",
                        "selected_files": 1,
                        "next_step": "send_selected_to_batch_or_ai_ready_build",
                    },
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
    guidance_dir = tmp_path / "guidance_alignment"
    guidance_dir.mkdir()
    (guidance_dir / "guidance_alignment_report.json").write_text(
        json.dumps(
            {
                "status": "mostly_aligned",
                "summary": {"achieved": 11, "partial": 2, "missing": 0},
                "requirements": [
                    {"name": "semantic_metadata_interpretation_and_policy", "status": "achieved", "remaining_work": []},
                    {
                        "name": "real_3_5_sample_benchmark_evidence",
                        "status": "partial",
                        "remaining_work": ["Add one clean full completed small sample."],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = make_data_scientist_agent_report(
        recipe_dir=recipe_dir,
        model_loop_dir=model_loop_dir,
        benchmark_dir=batch_dir,
        discovery_manifest=discovery_manifest,
        guidance_alignment_dir=guidance_dir,
        output_dir=tmp_path / "final_report",
    )

    assert result.status == "ready"
    assert result.selected_count == 1
    assert result.model_loop_status in {"completed", "warning"}
    assert result.guidance_alignment_status == "mostly_aligned"
    summary = json.loads(Path(result.files["real_data_scientist_agent_summary_json"]).read_text(encoding="utf-8"))
    assert summary["selected_count"] == 1
    assert summary["guidance_alignment"]["status"] == "mostly_aligned"
    assert summary["guidance_alignment"]["achieved_count"] == 11
    assert summary["guidance_alignment"]["partial_count"] == 2
    assert summary["guidance_alignment"]["partial_or_missing_requirements"][0]["name"] == "real_3_5_sample_benchmark_evidence"
    assert summary["discovery"]["candidate_count"] == 1
    assert summary["discovery"]["task_types"] == ["rt_prediction", "denovo"]
    assert summary["discovery"]["data_value_strategy_eval"]["interpretation"] == "agent_data_value_selection_outperforms_proxy_baselines"
    assert summary["discovery"]["repository_audit"]["status_counts"]["completed"] == 1
    assert summary["discovery"]["repository_audit"]["status_counts"]["blocked"] == 1
    assert summary["discovery"]["repository_audit"]["blocked_repositories"][0]["repository"] == "iprox"
    assert summary["model_loop"]["failure_mode_count"] >= 0
    assert summary["model_loop"]["adapter_contract"]["available"] is True
    assert summary["model_loop"]["adapter_contract"]["schema_version"] == "model-adapter-contract/v1"
    assert summary["model_loop"]["adapter_contract"]["input_selected_count"] == 1
    assert summary["model_loop"]["model_informed_discovery_request_count"] >= 1
    assert summary["model_loop"]["model_informed_discovery_payload_count"] == summary["model_loop"]["model_informed_discovery_request_count"]
    assert summary["model_loop"]["model_informed_payload_queue_count"] == summary["model_loop"]["model_informed_discovery_payload_count"]
    assert summary["model_loop"]["model_informed_payload_queue_ready_count"] + summary["model_loop"]["model_informed_payload_queue_review_count"] + summary["model_loop"]["model_informed_payload_queue_blocked_count"] == summary["model_loop"]["model_informed_payload_queue_count"]
    assert set(summary["model_loop"]["model_informed_repository_plan"]["planned_repositories"]) >= {"pride", "massive", "iprox"}
    assert summary["model_loop"]["model_informed_repository_plan"]["repository_strategy"] == "multi_repository"
    assert summary["model_loop"]["model_informed_curation_item_count"] == summary["model_loop"]["model_informed_discovery_request_count"]
    assert summary["model_loop"]["model_informed_curation_queue"]
    assert set(summary["model_loop"]["model_informed_curation_queue"][0]["planned_repositories"]) >= {"pride", "massive", "iprox"}
    assert summary["active_curation"]["model_informed_row_count"] >= 1
    assert summary["active_curation"]["row_count"] >= summary["active_curation"]["recipe_row_count"]
    assert any(item["curation_type"] == "review_model_informed_discovery_request" for item in summary["active_curation"]["top_items"])
    assert summary["evidence_graph"]["model_informed_request_node_count"] == summary["model_loop"]["model_informed_discovery_request_count"]
    assert summary["split_baseline_evaluation"]["status"] == "ready"
    assert "agent_minus_random_leakage" in summary["split_baseline_evaluation"]
    assert summary["model_strategy_comparison"]["interpretation"] == "agent_selected_dataset_outperforms_best_baseline_on_heldout_metrics"
    assert summary["counterfactual_benchmark"]["row_count"] >= 1
    assert "positive_training_case" in summary["counterfactual_benchmark"]["case_type_counts"]
    assert "review_reduction_rate" in summary["active_curation"]["efficiency"]
    assert summary["active_curation"]["memory_update"]["status"] == "updated"
    assert summary["active_curation"]["memory_update"]["imported_decision_count"] >= 1
    report = Path(result.files["real_data_scientist_agent_report_md"]).read_text(encoding="utf-8")
    assert "Real Data Scientist Agent Report" in report
    assert "Guidance Alignment" in report
    assert "mostly_aligned" in report
    assert "Remaining Alignment Work" in report
    assert "Discovery And Data Value" in report
    assert "Split And Leakage" in report
    assert "Split baseline" in report
    assert "Counterfactual Benchmark" in report
    assert "Model Loop" in report
    assert "Adapter contract" in report
    assert "Model-informed discovery requests" in report
    assert "Ready discovery payloads" in report
    assert "Discovery payload queue" in report
    assert "Model-informed repositories" in report
    assert "pride, massive, iprox" in report
    assert "Repository audit" in report
    assert "iprox_index_missing" in report
    assert "review_model_informed_discovery_request" in report
    assert "Model-informed request nodes" in report
    assert "Strategy comparison" in report
    assert "Review reduction rate" in report
    assert "Memory write-back" in report


def test_make_data_scientist_agent_report_cli(tmp_path: Path) -> None:
    batch_dir = tmp_path / "batch"
    _write_batch(batch_dir)
    recipe_dir = tmp_path / "recipe"
    make_dataset_recipe(batch_dir=batch_dir, output_dir=recipe_dir)
    model_loop_dir = tmp_path / "model_loop"
    run_dataset_model_loop(recipe_dir=recipe_dir, task_type="denovo", output_dir=model_loop_dir)
    guidance_dir = tmp_path / "guidance_alignment"
    guidance_dir.mkdir()
    (guidance_dir / "guidance_alignment_report.json").write_text(
        json.dumps({"status": "aligned", "summary": {"achieved": 13, "partial": 0, "missing": 0}, "requirements": []}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "cli_report"

    result = CliRunner().invoke(
        app,
        [
            "make-data-scientist-agent-report",
            "--recipe-dir",
            str(recipe_dir),
            "--model-loop-dir",
            str(model_loop_dir),
            "--benchmark-dir",
            str(batch_dir),
            "--guidance-alignment-dir",
            str(guidance_dir),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ready"
    assert payload["guidance_alignment_status"] == "aligned"
    assert "real_data_scientist_agent_report_md" in payload["files"]
    assert (output_dir / "real_data_scientist_agent_report.md").exists()
