from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.dataset_recipe import make_dataset_recipe
from agent.ai_ready.model_loop import _ptm_constraint, run_dataset_model_loop
from agent.cli import app


def _write_parquet(path: Path, peptide: str, *, charge: int = 2, modified: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "project_accession": "PXDLOOP001",
                "source_file": path.parent.name,
                "peptide_sequence": peptide,
                "modified_sequence": modified or peptide,
                "charge": charge,
                "retention_time": 12.5,
            }
        ]
    ).to_parquet(path, index=False)
    return path


def _write_batch_summary(batch_dir: Path, parquet_a: Path, parquet_b: Path) -> None:
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
                "full_status": "completed",
                "ai_ready_outcome": "completed",
                "task_statuses": {"denovo": "completed"},
                "rows_out": {"denovo": 1},
                "task_files": {"denovo": {"denovo_train_parquet": str(parquet_b)}},
                "blockers": [],
                "warnings": [],
            },
        ]
    }
    (batch_dir / "mini_e2e_batch_summary.json").write_text(json.dumps(payload), encoding="utf-8")


def _recipe_dir(tmp_path: Path) -> Path:
    batch_dir = tmp_path / "batch"
    parquet_a = _write_parquet(batch_dir / "01_run_a" / "task_runs" / "denovo" / "denovo_train.parquet", "PEPTIDEK")
    parquet_b = _write_parquet(batch_dir / "02_run_b" / "task_runs" / "denovo" / "denovo_train.parquet", "MODPEPTIDEK", charge=4, modified="M[Oxidation]ODPEPTIDEK")
    _write_batch_summary(batch_dir, parquet_a, parquet_b)
    recipe_dir = tmp_path / "recipe"
    make_dataset_recipe(batch_dir=batch_dir, output_dir=recipe_dir, split_strategy="file_disjoint")
    return recipe_dir


def test_run_dataset_model_loop_generates_metrics_failures_and_gap_plan(tmp_path: Path):
    recipe_dir = _recipe_dir(tmp_path)

    result = run_dataset_model_loop(
        recipe_dir=recipe_dir,
        task_type="denovo",
        mode="smoke",
        output_dir=tmp_path / "model_loop",
    )

    assert result.status == "completed"
    assert result.metric_status == "available"
    assert result.failure_mode_count > 0
    assert Path(result.files["model_eval_summary_json"]).exists()
    assert Path(result.files["model_adapter_contract_json"]).exists()
    assert Path(result.files["model_adapter_input_manifest_json"]).exists()
    assert Path(result.files["model_failure_modes_json"]).exists()
    assert Path(result.files["model_loop_report_md"]).exists()
    assert Path(result.files["model_informed_gap_report_json"]).exists()
    assert Path(result.files["model_informed_discovery_requests_json"]).exists()
    assert Path(result.files["model_informed_discovery_requests_csv"]).exists()
    assert Path(result.files["model_informed_discovery_requests_md"]).exists()
    assert Path(result.files["model_informed_discovery_payloads_json"]).exists()
    assert Path(result.files["model_informed_discovery_payloads_csv"]).exists()
    assert Path(result.files["model_informed_discovery_payloads_md"]).exists()
    assert Path(result.files["model_informed_discovery_payload_queue_json"]).exists()
    assert Path(result.files["model_informed_discovery_payload_queue_csv"]).exists()
    assert Path(result.files["model_informed_discovery_payload_queue_md"]).exists()
    assert Path(result.files["model_informed_curation_queue_json"]).exists()
    assert Path(result.files["model_informed_curation_queue_csv"]).exists()
    assert Path(result.files["model_informed_curation_queue_md"]).exists()
    summary = json.loads(Path(result.files["model_eval_summary_json"]).read_text(encoding="utf-8"))
    assert summary["metrics"]["total_rows"] == 2
    contract = json.loads(Path(result.files["model_adapter_contract_json"]).read_text(encoding="utf-8"))
    assert contract["schema_version"] == "model-adapter-contract/v1"
    assert "AGENT_MODEL_ADAPTER_INPUT" in contract["environment"]
    adapter_input = json.loads(Path(result.files["model_adapter_input_manifest_json"]).read_text(encoding="utf-8"))
    assert adapter_input["schema_version"] == "model-adapter-input/v1"
    assert adapter_input["summary"]["selected_count"] == 2
    failures = json.loads(Path(result.files["model_failure_modes_json"]).read_text(encoding="utf-8"))
    assert "low_training_rows" in failures["failure_mode_counts"]
    expansion = json.loads(Path(result.files["model_informed_expansion_plan_json"]).read_text(encoding="utf-8"))
    assert expansion["actions"]
    discovery_requests = json.loads(Path(result.files["model_informed_discovery_requests_json"]).read_text(encoding="utf-8"))
    assert discovery_requests["schema_version"] == "model-informed-discovery-requests/v1"
    assert discovery_requests["request_count"] == len(discovery_requests["requests"])
    assert discovery_requests["requests"]
    first_request = discovery_requests["requests"][0]
    assert first_request["action"] == "discover_dataset"
    assert first_request["requires_user_confirmation"] is True
    assert set(first_request["repositories"]) >= {"pride", "massive", "iprox"}
    assert first_request["constraints"]["species_policy"] == "open"
    assert first_request["constraints"]["max_file_size_mb"] == 500
    assert "discover-dataset" in first_request["suggested_cli"]
    discovery_payloads = json.loads(Path(result.files["model_informed_discovery_payloads_json"]).read_text(encoding="utf-8"))
    assert discovery_payloads["schema_version"] == "model-informed-discovery-payloads/v1"
    assert discovery_payloads["payload_count"] == discovery_requests["request_count"]
    first_payload = discovery_payloads["payloads"][0]["payload"]
    assert first_payload["source"] == "remote"
    assert first_payload["repository"] in {"auto", "pride", "massive", "iprox"}
    assert set(first_payload["planned_repositories"]) >= {"pride", "massive", "iprox"}
    assert first_payload["repository_strategy"] == "multi_repository"
    assert first_payload["species_policy"] == "open"
    assert first_payload["requires_user_confirmation"] is True
    queue = json.loads(Path(result.files["model_informed_discovery_payload_queue_json"]).read_text(encoding="utf-8"))
    assert queue["schema_version"] == "model-informed-discovery-payload-queue/v1"
    assert queue["item_count"] == discovery_payloads["payload_count"]
    assert queue["ready_count"] + queue["review_count"] + queue["blocked_count"] == queue["item_count"]
    assert set(queue["items"][0]["planned_repositories"]) >= {"pride", "massive", "iprox"}
    assert queue["items"][0]["recommended_action"] in {"review_and_run_discovery", "run_discovery_after_user_confirmation"}
    curation = json.loads(Path(result.files["model_informed_curation_queue_json"]).read_text(encoding="utf-8"))
    assert curation["schema_version"] == "model-informed-curation-queue/v1"
    assert curation["row_count"] == discovery_requests["request_count"]
    assert curation["rows"][0]["curation_type"] == "review_model_informed_discovery_request"
    assert curation["rows"][0]["action"] in {"confirm_and_run_discovery", "review_before_discovery", "fix_request_before_discovery"}
    assert set(curation["rows"][0]["planned_repositories"]) >= {"pride", "massive", "iprox"}
    assert curation["rows"][0]["repository_strategy"] == "multi_repository"


def test_model_informed_ptm_constraint_keeps_generic_modified_peptides_broad() -> None:
    assert _ptm_constraint("modified_peptides") == "any_ptm"
    assert _ptm_constraint("phosphotyrosine") == "phospho"


def test_run_dataset_model_loop_cli(tmp_path: Path):
    recipe_dir = _recipe_dir(tmp_path)
    output_dir = tmp_path / "model_loop_cli"

    result = CliRunner().invoke(
        app,
        [
            "run-dataset-model-loop",
            "--recipe-dir",
            str(recipe_dir),
            "--task-type",
            "denovo",
            "--mode",
            "smoke",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "completed"
    assert payload["metric_status"] == "available"
    assert (output_dir / "model_loop_report.md").exists()


def test_run_dataset_model_loop_accepts_precomputed_metrics(tmp_path: Path):
    recipe_dir = _recipe_dir(tmp_path)
    metrics_file = tmp_path / "metrics.json"
    metrics_file.write_text(json.dumps({"total_rows": 5000, "charge_4plus_rows": 10, "modified_rows": 2}), encoding="utf-8")

    result = run_dataset_model_loop(
        recipe_dir=recipe_dir,
        task_type="denovo",
        mode="smoke",
        metrics_file=metrics_file,
        output_dir=tmp_path / "model_loop_metrics",
    )

    assert result.adapter == "metrics_file"
    summary = json.loads(Path(result.files["model_eval_summary_json"]).read_text(encoding="utf-8"))
    assert summary["adapter"] == "metrics_file"
    assert summary["metrics"]["total_rows"] == 5000


def test_run_dataset_model_loop_diagnoses_external_model_metrics(tmp_path: Path):
    recipe_dir = _recipe_dir(tmp_path)
    metrics_file = Path("tests/fixtures/model_loop_real_metrics_denovo.json")

    result = run_dataset_model_loop(
        recipe_dir=recipe_dir,
        task_type="denovo",
        mode="smoke",
        metrics_file=metrics_file,
        output_dir=tmp_path / "model_loop_real_metrics",
    )

    assert result.status == "completed"
    summary = json.loads(Path(result.files["model_eval_summary_json"]).read_text(encoding="utf-8"))
    assert summary["metrics"]["model_metric_schema_version"] == "model_eval_metrics_v2"
    assert summary["metrics"]["primary_metric_value"] == 0.62
    failures = json.loads(Path(result.files["model_failure_modes_json"]).read_text(encoding="utf-8"))
    counts = failures["failure_mode_counts"]
    assert counts["model_primary_metric_below_threshold"] == 1
    assert counts["model_generalization_gap"] == 1
    assert counts["model_slice_underperformance:phosphotyrosine"] == 1
    assert counts["model_slice_underperformance:high_charge"] == 1
    expansion = json.loads(Path(result.files["model_informed_expansion_plan_json"]).read_text(encoding="utf-8"))
    targets = {(row["dimension"], row["target"]) for row in expansion["actions"]}
    assert ("ptm", "phosphotyrosine") in targets
    assert ("charge", "high_charge") in targets
    discovery_requests = json.loads(Path(result.files["model_informed_discovery_requests_json"]).read_text(encoding="utf-8"))
    request_targets = {(row["dimension"], row["target"]) for row in discovery_requests["requests"]}
    assert ("ptm", "phosphotyrosine") in request_targets
    assert any(row["constraints"].get("modification_scope") == "phospho" for row in discovery_requests["requests"])


def test_run_dataset_model_loop_accepts_xuanjinovo_tsv_metrics(tmp_path: Path):
    recipe_dir = _recipe_dir(tmp_path)
    metrics_file = Path("tests/fixtures/xuanjinovo_eval_metrics.tsv")

    result = run_dataset_model_loop(
        recipe_dir=recipe_dir,
        task_type="denovo",
        mode="smoke",
        adapter="xuanjinovo_template",
        metrics_file=metrics_file,
        output_dir=tmp_path / "model_loop_xuanjinovo_tsv",
    )

    assert result.status == "completed"
    assert result.adapter == "metrics_file"
    summary = json.loads(Path(result.files["model_eval_summary_json"]).read_text(encoding="utf-8"))
    assert summary["metrics"]["metric_adapter_template"] == "xuanjinovo_eval"
    assert summary["metrics"]["model_metric_schema_version"] == "model_eval_metrics_v2"
    assert summary["metrics"]["primary_metric"] == "sequence_accuracy"
    failures = json.loads(Path(result.files["model_failure_modes_json"]).read_text(encoding="utf-8"))
    assert failures["failure_mode_counts"]["model_primary_metric_below_threshold"] == 1
    assert failures["failure_mode_counts"]["model_slice_underperformance:phosphotyrosine"] == 1
    expansion = json.loads(Path(result.files["model_informed_expansion_plan_json"]).read_text(encoding="utf-8"))
    assert any(row["target"] == "phosphotyrosine" for row in expansion["actions"])
    discovery_requests = json.loads(Path(result.files["model_informed_discovery_requests_json"]).read_text(encoding="utf-8"))
    assert any("phosphotyrosine" in row["query"] for row in discovery_requests["requests"])


def test_run_dataset_model_loop_external_adapter_uses_contract_env(tmp_path: Path):
    recipe_dir = _recipe_dir(tmp_path)
    output_dir = tmp_path / "model_loop_external_adapter"
    script = Path("tests/fixtures/write_external_metrics_adapter.py").resolve()
    command = f"python {script}"

    result = run_dataset_model_loop(
        recipe_dir=recipe_dir,
        task_type="denovo",
        mode="smoke",
        adapter="xuanjinovo_template",
        adapter_command=command,
        output_dir=output_dir,
    )

    assert result.status == "completed"
    assert result.adapter == "external_command"
    assert result.warnings == []
    summary = json.loads(Path(result.files["model_eval_summary_json"]).read_text(encoding="utf-8"))
    assert summary["adapter"] == "external_command"
    assert summary["metrics"]["adapter_selected_count"] == 2
    assert summary["metrics"]["model_metric_schema_version"] == "model_eval_metrics_v2"
    assert summary["adapter_contract_warnings"] == []
    assert (output_dir / "external_model_metrics.json").exists()
    assert (output_dir / "model_adapter.log").exists()
