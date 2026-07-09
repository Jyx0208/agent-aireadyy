from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.dataset_recipe import make_dataset_recipe
from agent.cli import app


def _write_parquet(path: Path, peptide: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "project_accession": "PXDTEST001",
                "source_file": path.parent.name,
                "peptide_sequence": peptide,
                "modified_sequence": peptide,
                "charge": 2,
                "retention_time": 12.5,
                "q_value": 0.009,
                "target_decoy": "target",
                "score": 42.0,
            }
        ]
    ).to_parquet(path, index=False)
    return path


def _write_context_parquet(path: Path, peptide: str, protein: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "project_accession": "PXDTESTCTX",
                "source_file": path.parent.name,
                "peptide_sequence": peptide,
                "modified_sequence": peptide,
                "protein_accession": protein,
                "charge": 2,
                "retention_time": 10.0,
                "q_value": 0.008,
                "target_decoy": "target",
                "score": 50.0,
            }
        ]
    ).to_parquet(path, index=False)
    return path


def _write_batch_summary(batch_dir: Path, rt_a: Path, rt_b: Path) -> Path:
    batch_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_results": [
            {
                "run_name": "run_a",
                "agent_run_dir": str(batch_dir / "run_a"),
                "output_dir": str(batch_dir / "01_run_a"),
                "project_accession": "PXDTEST001",
                "source_file": "a.mzML",
                "sample_class": "clean_full_completed",
                "full_status": "completed",
                "ai_ready_outcome": "completed",
                "metadata_quality": "available",
                "task_statuses": {"rt_prediction": "completed", "denovo": "blocked"},
                "rows_out": {"rt_prediction": 1, "denovo": 0},
                "task_files": {"rt_prediction": {"rt_train_parquet": str(rt_a)}, "denovo": {}},
                "blockers": [],
                "warnings": [],
            },
            {
                "run_name": "run_b",
                "agent_run_dir": str(batch_dir / "run_b"),
                "output_dir": str(batch_dir / "02_run_b"),
                "project_accession": "PXDTEST001",
                "source_file": "b.mzML",
                "sample_class": "partial_output_recovery",
                "full_status": "failed_with_usable_partial_outputs",
                "ai_ready_outcome": "completed_from_usable_partial_outputs",
                "metadata_quality": "minimal",
                "task_statuses": {"rt_prediction": "completed"},
                "rows_out": {"rt_prediction": 1},
                "task_files": {"rt_prediction": {"rt_train_parquet": str(rt_b)}},
                "blockers": [],
                "warnings": ["partial"],
            },
            {
                "run_name": "run_blocked",
                "agent_run_dir": str(batch_dir / "run_blocked"),
                "output_dir": str(batch_dir / "03_run_blocked"),
                "project_accession": "PXDTEST002",
                "source_file": "blocked.mzML",
                "sample_class": "blocked_or_review_case",
                "full_status": "blocked",
                "ai_ready_outcome": "blocked",
                "metadata_quality": "minimal",
                "data_value_score": 0.8,
                "task_ai_readiness_score": 0.7,
                "semantic_metadata_confidence": 0.2,
                "task_statuses": {"rt_prediction": "blocked"},
                "rows_out": {"rt_prediction": 0},
                "task_files": {"rt_prediction": {}},
                "blockers": ["zero_psm"],
                "warnings": [],
            },
        ]
    }
    path = batch_dir / "mini_e2e_batch_summary.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_make_dataset_recipe_selects_completed_outputs_and_checks_leakage(tmp_path: Path):
    batch_dir = tmp_path / "batch"
    rt_a = _write_parquet(batch_dir / "01_run_a" / "task_runs" / "rt_prediction" / "rt_train.parquet", "PEPTIDEK")
    rt_b = _write_parquet(batch_dir / "02_run_b" / "task_runs" / "rt_prediction" / "rt_train.parquet", "PEPTIDEK")
    _write_batch_summary(batch_dir, rt_a, rt_b)

    result = make_dataset_recipe(batch_dir=batch_dir, output_dir=tmp_path / "recipe")

    assert result.status == "ready"
    assert result.selected_count == 2
    assert result.excluded_count == 2
    assert result.split_level == "file"
    assert result.leakage_status == "leakage_detected"
    for path in result.files.values():
        assert Path(path).exists()
    selected = Path(result.files["selected_files_csv"]).read_text(encoding="utf-8")
    assert "completed_task_with_rows" in selected
    assert "pride" in selected
    recipe = json.loads(Path(result.files["dataset_recipe_json"]).read_text(encoding="utf-8"))
    assert recipe["repository_summary"]["selected_counts"]["pride"] == 2
    assert recipe["split_policy"] == "file_disjoint"
    assert recipe["split_strategy_resolved"] == "file_disjoint"
    assert "coverage_gap_report" in recipe
    assert "agent_expansion_plan" in recipe
    assert "split_baseline_evaluation" in recipe
    leakage = json.loads(Path(result.files["leakage_check_report_json"]).read_text(encoding="utf-8"))
    assert leakage["peptide_charge_leak_count"] == 1
    leakage_risk = json.loads(Path(result.files["leakage_risk_report_json"]).read_text(encoding="utf-8"))
    assert leakage_risk["status"] == "fail"
    split_eval = json.loads(Path(result.files["split_baseline_evaluation_json"]).read_text(encoding="utf-8"))
    assert {row["strategy"] for row in split_eval["strategy_rows"]} >= {"agent_designed_split", "random_row_split"}
    assert Path(result.files["split_baseline_evaluation_md"]).exists()
    hard = json.loads(Path(result.files["hard_benchmark_manifest_json"]).read_text(encoding="utf-8"))
    assert hard["rows"]
    assert all(row.get("hard_case_evidence_status") in {"available", "missing"} for row in hard["rows"])
    counterfactual = json.loads(Path(result.files["counterfactual_benchmark_manifest_json"]).read_text(encoding="utf-8"))
    assert counterfactual["rows"]
    assert counterfactual["case_type_counts"]["positive_training_case"] >= 1
    assert counterfactual["case_type_counts"]["negative_or_blocked_case"] >= 1
    assert Path(result.files["counterfactual_benchmark_report_md"]).exists()
    gaps = json.loads(Path(result.files["coverage_gap_report_json"]).read_text(encoding="utf-8"))
    assert gaps["gaps"]
    assert Path(result.files["agent_expansion_plan_json"]).exists()
    assert Path(result.files["evidence_graph_json"]).exists()
    assert Path(result.files["split_rationale_md"]).exists()
    assert Path(result.files["evidence_graph_summary_md"]).exists()
    assert Path(result.files["curation_queue_json"]).exists()
    assert Path(result.files["curation_efficiency_report_json"]).exists()
    graph = json.loads(Path(result.files["evidence_graph_json"]).read_text(encoding="utf-8"))
    assert {"split", "parquet", "curation_item", "counterfactual_case"} <= {node["type"] for node in graph["nodes"]}
    curation = Path(result.files["curation_queue_csv"]).read_text(encoding="utf-8")
    assert "potential_leakage_risk" in curation
    curation_json = json.loads(Path(result.files["curation_queue_json"]).read_text(encoding="utf-8"))
    assert curation_json["rows"][0]["priority_score"] >= curation_json["rows"][-1]["priority_score"]
    assert all(row.get("curation_id") for row in curation_json["rows"])
    efficiency = json.loads(Path(result.files["curation_efficiency_report_json"]).read_text(encoding="utf-8"))
    assert efficiency["manual_only_review_count"] == result.selected_count + result.excluded_count
    assert efficiency["agent_assisted_review_count"] == result.curation_queue_count
    assert 0 <= efficiency["review_reduction_rate"] <= 1
    assert Path(result.files["curation_efficiency_report_md"]).exists()


def test_make_dataset_recipe_adds_repository_audit_to_evidence_graph(tmp_path: Path):
    batch_dir = tmp_path / "batch"
    rt = _write_parquet(batch_dir / "01_run_a" / "task_runs" / "rt_prediction" / "rt_train.parquet", "PEPTIDEK")
    _write_batch_summary(batch_dir, rt, rt)
    discovery_dir = tmp_path / "discovery"
    discovery_dir.mkdir()
    discovery_manifest = discovery_dir / "dataset_manifest.json"
    discovery_manifest.write_text(
        json.dumps({"files": [{"repository": "pride", "project_accession": "PXDTEST001", "file_name": "a.mzML"}]}),
        encoding="utf-8",
    )
    (discovery_dir / "repository_audit.json").write_text(
        json.dumps(
            {
                "requested_repository": "auto",
                "repositories_attempted": ["pride", "iprox"],
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

    result = make_dataset_recipe(
        batch_dir=batch_dir,
        output_dir=tmp_path / "recipe",
        discovery_manifest=discovery_manifest,
    )

    recipe = json.loads(Path(result.files["dataset_recipe_json"]).read_text(encoding="utf-8"))
    assert recipe["repository_audit"]["rows"][1]["blocker"] == "iprox_index_missing"
    assert any(
        action["action"] == "refresh_iprox_index_or_set_agent_iprox_index_xlsx"
        and action["repository"] == "iprox"
        for action in recipe["agent_expansion_plan"]["actions"]
    )
    gaps = json.loads(Path(result.files["coverage_gap_report_json"]).read_text(encoding="utf-8"))
    assert gaps["repository_blockers"][0]["blocker"] == "iprox_index_missing"
    curation = json.loads(Path(result.files["curation_queue_json"]).read_text(encoding="utf-8"))
    assert any(row["curation_type"] == "review_repository_blocker" for row in curation["rows"])
    graph = json.loads(Path(result.files["evidence_graph_json"]).read_text(encoding="utf-8"))
    node_types = {node["type"] for node in graph["nodes"]}
    assert "repository_attempt" in node_types
    assert any(node["type"] == "repository_attempt" and node["repository"] == "iprox" for node in graph["nodes"])
    assert any(edge["relation"] == "blocks" and edge["target"] == "repository_attempt:iprox" for edge in graph["edges"])
    summary = Path(result.files["evidence_graph_summary_md"]).read_text(encoding="utf-8")
    assert "Repository Audit Evidence" in summary
    assert "iprox_index_missing" in summary


def test_make_dataset_recipe_supports_project_disjoint_strategy(tmp_path: Path):
    batch_dir = tmp_path / "batch"
    parquets = [
        _write_parquet(batch_dir / f"0{index}_run" / "task_runs" / "rt_prediction" / "rt_train.parquet", f"PEPTIDE{index}K")
        for index in range(1, 4)
    ]
    batch_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_results": [
            {
                "run_name": f"run_{index}",
                "agent_run_dir": str(batch_dir / f"run_{index}"),
                "output_dir": str(batch_dir / f"0{index}_run"),
                "project_accession": f"PXDTEST00{index}",
                "source_file": f"{index}.mzML",
                "full_status": "completed",
                "ai_ready_outcome": "completed",
                "task_statuses": {"rt_prediction": "completed"},
                "rows_out": {"rt_prediction": 1},
                "task_files": {"rt_prediction": {"rt_train_parquet": str(parquets[index - 1])}},
                "blockers": [],
                "warnings": [],
            }
            for index in range(1, 4)
        ]
    }
    (batch_dir / "mini_e2e_batch_summary.json").write_text(json.dumps(payload), encoding="utf-8")

    result = make_dataset_recipe(
        batch_dir=batch_dir,
        output_dir=tmp_path / "recipe",
        split_strategy="project_disjoint",
    )

    assert result.split_policy == "project_disjoint"
    assert result.split_level == "project"
    plan = json.loads(Path(result.files["dataset_split_plan_json"]).read_text(encoding="utf-8"))
    assert plan["split_rationale"]["resolved_strategy"] == "project_disjoint"
    assert set(result.split_counts) == {"train", "val", "test"}


def test_make_dataset_recipe_compares_agent_split_against_random_baseline(tmp_path: Path):
    batch_dir = tmp_path / "batch"
    rt_a = _write_parquet(batch_dir / "01_run_a" / "task_runs" / "rt_prediction" / "rt_train.parquet", "SHAREDPEPTIDEK")
    rt_b = _write_parquet(batch_dir / "02_run_b" / "task_runs" / "rt_prediction" / "rt_train.parquet", "SHAREDPEPTIDEK")
    _write_batch_summary(batch_dir, rt_a, rt_b)

    result = make_dataset_recipe(
        batch_dir=batch_dir,
        output_dir=tmp_path / "recipe",
        split_strategy="project_disjoint",
    )

    split_eval = json.loads(Path(result.files["split_baseline_evaluation_json"]).read_text(encoding="utf-8"))
    rows = {row["strategy"]: row for row in split_eval["strategy_rows"]}
    assert rows["agent_designed_split"]["total_leakage_issue_count"] == 0
    assert rows["random_row_split"]["total_leakage_issue_count"] > 0
    assert split_eval["interpretation"] == "agent_split_reduces_leakage_vs_random_baseline"
    report = Path(result.files["split_baseline_evaluation_md"]).read_text(encoding="utf-8")
    assert "Split Baseline Evaluation" in report


def test_make_dataset_recipe_supports_lab_split_protein_leakage_and_context_graph(tmp_path: Path):
    batch_dir = tmp_path / "batch"
    parquets = [
        _write_context_parquet(
            batch_dir / f"0{index}_run" / "task_runs" / "rt_prediction" / "rt_train.parquet",
            f"PEPTIDE{index}K",
            "P_SHARED" if index < 3 else "P_UNIQUE",
        )
        for index in range(1, 4)
    ]
    payload = {
        "run_results": [
            {
                "run_name": f"run_{index}",
                "agent_run_dir": str(batch_dir / f"run_{index}"),
                "output_dir": str(batch_dir / f"0{index}_run"),
                "project_accession": f"PXDCTX00{index}",
                "source_file": f"{index}.mzML",
                "sample_name": f"sample_{index}",
                "condition": "treated" if index == 1 else "control",
                "lab": f"lab_{index}",
                "enzyme": "trypsin",
                "database": "uniprot_human",
                "workflow": "fragpipe",
                "search_engine": "msfragger",
                "acquisition_mode": "DDA",
                "full_status": "completed",
                "ai_ready_outcome": "completed",
                "metadata_quality": "available",
                "task_statuses": {"rt_prediction": "completed"},
                "rows_out": {"rt_prediction": 1},
                "task_files": {"rt_prediction": {"rt_train_parquet": str(parquets[index - 1])}},
                "blockers": [],
                "warnings": [],
            }
            for index in range(1, 4)
        ]
    }
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "mini_e2e_batch_summary.json").write_text(json.dumps(payload), encoding="utf-8")

    result = make_dataset_recipe(
        batch_dir=batch_dir,
        output_dir=tmp_path / "recipe",
        split_strategy="lab_disjoint",
    )

    assert result.split_policy == "lab_disjoint"
    assert result.split_level == "lab"
    leakage = json.loads(Path(result.files["leakage_check_report_json"]).read_text(encoding="utf-8"))
    assert leakage["protein_leak_count"] == 1
    leakage_risk = json.loads(Path(result.files["leakage_risk_report_json"]).read_text(encoding="utf-8"))
    assert leakage_risk["issue_counts"]["protein"] == 1
    assert "inspect_protein_level_overlap_or_use_protein_disjoint_split_for_protein_family_benchmarks" in leakage_risk["recommendations"]
    graph = json.loads(Path(result.files["evidence_graph_json"]).read_text(encoding="utf-8"))
    node_types = {node["type"] for node in graph["nodes"]}
    assert {"lab", "protein", "condition", "enzyme", "database", "workflow", "acquisition", "qc", "decision"} <= node_types
    assert any(edge["relation"] == "selected_because" for edge in graph["edges"])


def test_make_dataset_recipe_marks_missing_hard_case_evidence(tmp_path: Path):
    batch_dir = tmp_path / "batch"
    rt = _write_parquet(batch_dir / "01_run_a" / "task_runs" / "rt_prediction" / "rt_train.parquet", "PEPTIDEK")
    _write_batch_summary(batch_dir, rt, rt)

    result = make_dataset_recipe(batch_dir=batch_dir, output_dir=tmp_path / "recipe")

    hard = json.loads(Path(result.files["hard_benchmark_manifest_json"]).read_text(encoding="utf-8"))
    denovo_rows = [row for row in hard["rows"] if row["task_type"] == "denovo"]
    assert denovo_rows
    assert any("hard_case_evidence_missing" in ";".join(row["tags"]) for row in denovo_rows)


def test_make_dataset_recipe_cli(tmp_path: Path):
    batch_dir = tmp_path / "batch"
    rt = _write_parquet(batch_dir / "01_run_a" / "task_runs" / "rt_prediction" / "rt_train.parquet", "PEPTIDEK")
    _write_batch_summary(batch_dir, rt, rt)
    runner = CliRunner()
    output_dir = tmp_path / "recipe"

    result = runner.invoke(
        app,
        [
            "make-dataset-recipe",
            "--batch-dir",
            str(batch_dir),
            "--output-dir",
            str(output_dir),
            "--split-strategy",
            "file_disjoint",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ready"
    assert payload["selected_count"] == 2
    assert (output_dir / "dataset_recipe.json").exists()
    assert (output_dir / "dataset_split_manifest.csv").exists()
    assert (output_dir / "dataset_split_plan.json").exists()
    assert (output_dir / "split_rationale.md").exists()
    assert (output_dir / "coverage_gap_report.md").exists()
