from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pandas as pd

import agent.web.app as web_app
from agent.agent_core.harness import AgentHarnessCaseResult, AgentHarnessResult
from agent.ai_ready.data_scientist_loop import DataScientistAgentLoopResult
from agent.ai_ready.data_scientist_report import DataScientistAgentReportResult
from agent.ai_ready.dataset_recipe import DatasetRecipeResult
from agent.ai_ready.guidance_alignment import GuidanceAlignmentResult
from agent.ai_ready.model_loop import DatasetModelLoopResult
from agent.repositories.smoke import RepositorySmokeResult


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)
    return path


def test_web_ai_ready_profile_inputs_writes_profile(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    search_result = _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "Retention": 12.5}],
    )

    result = asyncio.run(
        web_app.profile_ai_ready_inputs_api(
            {
                "search_result": [str(search_result)],
                "task_type": ["rt_prediction"],
                "build_id": "profile_test",
            }
        )
    )

    assert result["status"] == "completed"
    assert result["build_id"] == "profile_test"
    assert result["task_cards"][0]["task_type"] == "rt_prediction"
    assert "input_profile_json" in result["downloads"]
    assert (tmp_path / "ai_ready_builds" / "profile_test" / "ai_ready_input_profile.json").exists()


def test_web_ai_ready_validate_build_returns_report_downloads(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    build_dir = tmp_path / "ai_ready_builds" / "build_test"
    parquet = build_dir / "rt_ai_ready" / "rt_train.parquet"
    parquet.parent.mkdir(parents=True)
    parquet.write_text("placeholder", encoding="utf-8")
    (build_dir / "discovery_task_build_plan.json").write_text(
        json.dumps({"required_labels": ["retention_time_labels"], "summary": {}}),
        encoding="utf-8",
    )
    (build_dir / "rt_ai_ready" / "rt_export_report.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "rows_in": 1,
                "rows_out": 1,
                "rows_filtered": 0,
                "filter_counts": {},
                "warnings": [],
                "outputs": {"rt_train_parquet": str(parquet)},
            }
        ),
        encoding="utf-8",
    )

    result = asyncio.run(
        web_app.validate_ai_ready_build_api(
            {"build_id": "build_test", "task_type": "rt_prediction"}
        )
    )

    assert result["status"] == "completed"
    assert result["task_cards"][0]["rows_out"] == 1
    assert "validation_report_json" in result["downloads"]
    assert "build_report_md" in result["downloads"]


def test_web_apply_curation_decisions_updates_discovery_memory(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    queue = tmp_path / "curation_queue.json"
    queue.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "curation_id": "curation:web",
                        "repository": "pride",
                        "repository_strategy": "multi_repository",
                        "planned_repositories": ["pride", "massive", "iprox"],
                        "project_accession": "PXDWEB001",
                        "source_file": "web_sample.mzML",
                        "task_type": "denovo",
                        "curation_type": "check_leakage_risk",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = asyncio.run(
        web_app.apply_curation_decisions_api(
            {
                "build_id": "curation_web",
                "curation_queue": str(queue),
                "default_decision": "needs_review",
                "run_id": "web_curation",
            }
        )
    )

    assert result["status"] == "updated"
    assert result["curation_memory_update"]["imported_decision_count"] == 1
    assert result["curation_memory_update"]["imported_decisions"][0]["planned_repositories"] == ["pride", "massive", "iprox"]
    assert result["task_cards"][0]["task_type"] == "active_curation_memory"
    assert "curation_memory_update_json" in result["downloads"]
    memory_file = tmp_path / "discovery_memory" / "review_decisions.jsonl"
    assert memory_file.exists()
    memory_text = memory_file.read_text(encoding="utf-8")
    assert "PXDWEB001" in memory_text
    assert "planned_repositories=pride,massive,iprox" in memory_text


def test_web_ai_ready_locate_inputs_from_search_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    search_dir = tmp_path / "search"
    search_dir.mkdir()
    _write_tsv(
        search_dir / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "Retention": 12.5}],
    )

    result = asyncio.run(
        web_app.locate_ai_ready_inputs_api(
            {
                "search_dir": str(search_dir),
                "build_id": "locator_test",
            }
        )
    )

    assert result["status"] == "completed"
    assert result["input_locations"]["summary"]["search_result_count"] == 1
    assert "input_locations_json" in result["downloads"]


def test_web_ai_ready_real_smoke_returns_task_cards(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    search_dir = tmp_path / "search"
    search_dir.mkdir()
    _write_tsv(
        search_dir / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5, "PSM Q-Value": 0.001}],
    )

    result = asyncio.run(
        web_app.run_ai_ready_real_smoke_api(
            {
                "search_dir": str(search_dir),
                "task_type": ["rt_prediction"],
                "build_id": "smoke_test",
            }
        )
    )

    assert result["status"] == "completed"
    assert result["task_cards"][0]["task_type"] == "rt_prediction"
    assert result["task_cards"][0]["rows_out"] == 1
    assert "real_smoke_summary_json" in result["downloads"]


def test_web_repository_smoke_returns_report_downloads(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)

    def fake_repository_smoke(**kwargs) -> RepositorySmokeResult:
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        result = RepositorySmokeResult(
            status="completed",
            repository="massive",
            requested_repository="massive",
            input_value=str(kwargs["input_value"]),
            mode="parameters",
            project_accession="MSV000000001",
            native_accession="MSV000000001",
            download_url="https://example.test/sample.raw",
            transfer_method="https",
        )
        (output_dir / "repository_smoke_summary.json").write_text(result.model_dump_json(), encoding="utf-8")
        (output_dir / "repository_smoke_report.md").write_text("# Repository Smoke Report\n", encoding="utf-8")
        return result

    monkeypatch.setattr(web_app, "run_repository_smoke", fake_repository_smoke)
    result = asyncio.run(
        web_app.run_repository_smoke_api(
            {
                "repository": "massive",
                "input_value": "MSV000000001/raw/sample.raw",
                "build_id": "repository_smoke_test",
            }
        )
    )

    assert result["status"] == "completed"
    assert result["task_cards"][0]["task_type"] == "repository:massive"
    assert "repository_smoke_summary_json" in result["downloads"]
    assert "repository_smoke_report_md" in result["downloads"]


def test_web_repository_smoke_accepts_iprox_index_dir(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    seen_kwargs = {}

    def fake_repository_smoke(**kwargs) -> RepositorySmokeResult:
        seen_kwargs.update(kwargs)
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        result = RepositorySmokeResult(
            status="completed",
            repository="iprox",
            requested_repository="iprox",
            input_value=str(kwargs["input_value"]),
            mode="parameters",
            project_accession="IPX0015463001",
            native_accession="IPX0015463001",
            download_url="https://download.iprox.cn/IPX0015463000/IPX0015463001/sample.raw",
            transfer_method="https",
        )
        (output_dir / "repository_smoke_summary.json").write_text(result.model_dump_json(), encoding="utf-8")
        return result

    monkeypatch.setattr(web_app, "run_repository_smoke", fake_repository_smoke)
    result = asyncio.run(
        web_app.run_repository_smoke_api(
            {
                "repository": "iprox",
                "input_value": "IPX0015463001/sample.raw",
                "iprox_index_dir": str(tmp_path / "iprox_index"),
                "build_id": "iprox_repository_smoke_test",
            }
        )
    )

    assert seen_kwargs["registry"] is not None
    assert result["status"] == "completed"
    assert result["task_cards"][0]["task_type"] == "repository:iprox"


def test_web_refresh_iprox_index_returns_downloads(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    seen_kwargs = {}

    def fake_refresh_iprox_index(**kwargs):
        seen_kwargs.update(kwargs)
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "status": "ready",
            "years": kwargs["years"],
            "requested_projects": kwargs["project_ids"],
            "project_count": 1,
            "file_count": 2,
            "failures": [],
            "next_step": "set_AGENT_IPROX_INDEX_DIR_or_pass_index_path",
        }
        (output_dir / "iprox_index_summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (output_dir / "iprox_project_index.jsonl").write_text("{}\n", encoding="utf-8")
        (output_dir / "iprox_file_index.jsonl").write_text("{}\n", encoding="utf-8")
        return summary

    monkeypatch.setattr(web_app, "refresh_public_iprox_index", fake_refresh_iprox_index)
    result = asyncio.run(
        web_app.refresh_iprox_index_api(
            {
                "projects": "IPX0015463000",
                "years": "2020,2024",
                "build_id": "iprox_index_test",
            }
        )
    )

    assert seen_kwargs["project_ids"] == ["IPX0015463000"]
    assert seen_kwargs["years"] == [2020, 2024]
    assert result["status"] == "ready"
    assert result["task_cards"][0]["task_type"] == "iprox_index"
    assert "iprox_index_summary_json" in result["downloads"]
    assert "iprox_file_index_jsonl" in result["downloads"]


def test_web_agent_harness_returns_report_downloads(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)

    def fake_agent_harness(**kwargs) -> AgentHarnessResult:
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        case = AgentHarnessCaseResult(
            id="case_a",
            status="passed",
            goal="Find human DDA data for RT prediction",
            inferred={"task_type": "rt_prediction", "repository": "pride", "next_action_category": "discovery_plan"},
        )
        result = AgentHarnessResult(
            status="passed",
            output_dir=str(output_dir),
            total_cases=1,
            passed=1,
            case_results=[case],
        )
        (output_dir / "agent_harness_summary.json").write_text(result.model_dump_json(), encoding="utf-8")
        (output_dir / "agent_harness_report.md").write_text("# Agent Harness Report\n", encoding="utf-8")
        return result

    monkeypatch.setattr(web_app, "run_agent_harness", fake_agent_harness)
    result = asyncio.run(
        web_app.run_agent_harness_api(
            {
                "case_file": "tests/fixtures/agent_harness_cases.json",
                "build_id": "agent_harness_test",
                "use_llm": False,
            }
        )
    )

    assert result["status"] == "passed"
    assert result["task_cards"][0]["task_type"] == "harness:case_a"
    assert "agent_harness_summary_json" in result["downloads"]
    assert "agent_harness_report_md" in result["downloads"]


def test_web_make_dataset_recipe_returns_v2_downloads(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    seen_kwargs = {}

    def fake_make_dataset_recipe(**kwargs) -> DatasetRecipeResult:
        seen_kwargs.update(kwargs)
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "dataset_recipe_json": str(output_dir / "dataset_recipe.json"),
            "dataset_recipe_md": str(output_dir / "dataset_recipe.md"),
            "coverage_gap_report_json": str(output_dir / "coverage_gap_report.json"),
            "coverage_gap_report_md": str(output_dir / "coverage_gap_report.md"),
            "evidence_graph_json": str(output_dir / "evidence_graph.json"),
            "evidence_graph_summary_md": str(output_dir / "evidence_graph_summary.md"),
            "curation_queue_csv": str(output_dir / "curation_queue.csv"),
            "curation_queue_json": str(output_dir / "curation_queue.json"),
            "split_rationale_md": str(output_dir / "split_rationale.md"),
        }
        for name, path in files.items():
            target = Path(path)
            if name == "dataset_recipe_json":
                target.write_text(
                    json.dumps(
                        {
                            "status": "ready",
                            "selected_files": [{}],
                            "split_policy": "file_disjoint",
                            "split_strategy_resolved": "file_disjoint",
                        }
                    ),
                    encoding="utf-8",
                )
            elif name == "curation_queue_json":
                target.write_text(json.dumps({"rows": [{"curation_type": "check_leakage_risk"}], "row_count": 1}), encoding="utf-8")
            elif target.suffix == ".json":
                target.write_text(json.dumps({"file": name}), encoding="utf-8")
            else:
                target.write_text("status\n", encoding="utf-8")
        return DatasetRecipeResult(
            status="ready",
            batch_dir=str(kwargs["batch_dir"]),
            output_dir=str(output_dir),
            selected_count=1,
            split_policy="file_disjoint",
            split_strategy="auto",
            hard_benchmark_count=0,
            curation_queue_count=1,
            files=files,
        )

    monkeypatch.setattr(web_app, "make_dataset_recipe", fake_make_dataset_recipe)
    result = asyncio.run(
        web_app.make_dataset_recipe_api(
            {
                "batch_dir": str(tmp_path / "mini_batch"),
                "build_id": "recipe_test",
                "repository_audit": str(tmp_path / "repository_audit.json"),
                "split_strategy": "file_disjoint",
            }
        )
    )

    assert seen_kwargs["repository_audit"] == tmp_path / "repository_audit.json"
    assert result["status"] == "ready"
    assert result["task_cards"][0]["task_type"] == "dataset_recipe"
    assert "dataset_recipe_json" in result["downloads"]
    assert "coverage_gap_report_json" in result["downloads"]
    assert "evidence_graph_json" in result["downloads"]
    assert "evidence_graph_summary_md" in result["downloads"]
    assert "curation_queue_json" in result["downloads"]
    assert "split_rationale_md" in result["downloads"]
    assert "curation_queue:1" in result["task_cards"][0]["warnings"]


def test_web_run_dataset_model_loop_returns_reports(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)

    def fake_model_loop(**kwargs) -> DatasetModelLoopResult:
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "model_adapter_contract_json": str(output_dir / "model_adapter_contract.json"),
            "model_adapter_contract_md": str(output_dir / "model_adapter_contract.md"),
            "model_adapter_input_manifest_json": str(output_dir / "model_adapter_input_manifest.json"),
            "model_adapter_input_manifest_csv": str(output_dir / "model_adapter_input_manifest.csv"),
            "external_model_metrics_json": str(output_dir / "external_model_metrics.json"),
            "model_adapter_log": str(output_dir / "model_adapter.log"),
            "model_eval_summary_json": str(output_dir / "model_eval_summary.json"),
            "model_failure_modes_json": str(output_dir / "model_failure_modes.json"),
            "model_loop_report_md": str(output_dir / "model_loop_report.md"),
            "model_informed_gap_report_json": str(output_dir / "model_informed_gap_report.json"),
            "model_informed_gap_report_md": str(output_dir / "model_informed_gap_report.md"),
            "model_informed_expansion_plan_json": str(output_dir / "model_informed_expansion_plan.json"),
            "model_informed_discovery_requests_json": str(output_dir / "model_informed_discovery_requests.json"),
            "model_informed_discovery_requests_csv": str(output_dir / "model_informed_discovery_requests.csv"),
            "model_informed_discovery_requests_md": str(output_dir / "model_informed_discovery_requests.md"),
            "model_informed_discovery_payloads_json": str(output_dir / "model_informed_discovery_payloads.json"),
            "model_informed_discovery_payloads_csv": str(output_dir / "model_informed_discovery_payloads.csv"),
            "model_informed_discovery_payloads_md": str(output_dir / "model_informed_discovery_payloads.md"),
            "model_informed_discovery_payload_queue_json": str(output_dir / "model_informed_discovery_payload_queue.json"),
            "model_informed_discovery_payload_queue_csv": str(output_dir / "model_informed_discovery_payload_queue.csv"),
            "model_informed_discovery_payload_queue_md": str(output_dir / "model_informed_discovery_payload_queue.md"),
            "model_informed_curation_queue_json": str(output_dir / "model_informed_curation_queue.json"),
            "model_informed_curation_queue_csv": str(output_dir / "model_informed_curation_queue.csv"),
            "model_informed_curation_queue_md": str(output_dir / "model_informed_curation_queue.md"),
        }
        Path(files["model_eval_summary_json"]).write_text(
            json.dumps(
                {
                    "status": "completed",
                    "task_type": "denovo",
                    "adapter": "dry_run",
                    "metric_status": "available",
                    "adapter_status": "completed",
                    "adapter_contract_warnings": ["external_adapter_metrics_schema_incomplete"],
                    "metrics": {"total_rows": 12},
                    "validation": {"blockers": [], "warnings": ["low_training_rows"]},
                }
            ),
            encoding="utf-8",
        )
        Path(files["model_adapter_contract_json"]).write_text(
            json.dumps({"schema_version": "model-adapter-contract/v1"}),
            encoding="utf-8",
        )
        Path(files["model_adapter_contract_md"]).write_text("# Contract\n", encoding="utf-8")
        Path(files["model_adapter_input_manifest_json"]).write_text(
            json.dumps({"schema_version": "model-adapter-input/v1", "summary": {"selected_count": 1, "total_rows_out": 12}}),
            encoding="utf-8",
        )
        Path(files["model_adapter_input_manifest_csv"]).write_text("index,task_type\n1,denovo\n", encoding="utf-8")
        Path(files["external_model_metrics_json"]).write_text(json.dumps({"primary_metric": "accuracy", "accuracy": 0.9}), encoding="utf-8")
        Path(files["model_adapter_log"]).write_text("ok\n", encoding="utf-8")
        Path(files["model_failure_modes_json"]).write_text(
            json.dumps({"failure_modes": [{"failure_mode": "low_training_rows"}]}),
            encoding="utf-8",
        )
        Path(files["model_informed_gap_report_json"]).write_text(
            json.dumps({"gaps": [{"dimension": "label_yield"}]}),
            encoding="utf-8",
        )
        Path(files["model_loop_report_md"]).write_text("# Model Loop\n", encoding="utf-8")
        Path(files["model_informed_gap_report_md"]).write_text("# Gap\n", encoding="utf-8")
        Path(files["model_informed_expansion_plan_json"]).write_text(json.dumps({"actions": []}), encoding="utf-8")
        Path(files["model_informed_discovery_requests_json"]).write_text(
            json.dumps({"request_count": 1, "requests": [{"request_id": "model_gap_001"}]}),
            encoding="utf-8",
        )
        Path(files["model_informed_discovery_requests_csv"]).write_text("request_id\nmodel_gap_001\n", encoding="utf-8")
        Path(files["model_informed_discovery_requests_md"]).write_text("# Discovery Requests\n", encoding="utf-8")
        Path(files["model_informed_discovery_payloads_json"]).write_text(
            json.dumps({"payload_count": 1, "payloads": [{"request_id": "model_gap_001", "payload": {"repository": "auto"}}]}),
            encoding="utf-8",
        )
        Path(files["model_informed_discovery_payloads_csv"]).write_text("request_id,repository\nmodel_gap_001,auto\n", encoding="utf-8")
        Path(files["model_informed_discovery_payloads_md"]).write_text("# Discovery Payloads\n", encoding="utf-8")
        Path(files["model_informed_discovery_payload_queue_json"]).write_text(
            json.dumps(
                {
                    "item_count": 1,
                    "ready_count": 1,
                    "review_count": 0,
                    "blocked_count": 0,
                    "items": [{"request_id": "model_gap_001", "queue_status": "ready_for_user_confirmation"}],
                }
            ),
            encoding="utf-8",
        )
        Path(files["model_informed_discovery_payload_queue_csv"]).write_text("request_id,queue_status\nmodel_gap_001,ready_for_user_confirmation\n", encoding="utf-8")
        Path(files["model_informed_discovery_payload_queue_md"]).write_text("# Discovery Payload Queue\n", encoding="utf-8")
        Path(files["model_informed_curation_queue_json"]).write_text(
            json.dumps(
                {
                    "row_count": 1,
                    "rows": [
                        {
                            "curation_id": "model_curation:model_gap_001",
                            "curation_type": "review_model_informed_discovery_request",
                            "request_id": "model_gap_001",
                            "action": "confirm_and_run_discovery",
                            "planned_repositories": ["pride", "massive", "iprox"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        Path(files["model_informed_curation_queue_csv"]).write_text("request_id,action\nmodel_gap_001,confirm_and_run_discovery\n", encoding="utf-8")
        Path(files["model_informed_curation_queue_md"]).write_text("# Model-informed Curation Queue\n", encoding="utf-8")
        return DatasetModelLoopResult(
            status="completed",
            recipe_dir=str(kwargs["recipe_dir"]),
            output_dir=str(output_dir),
            task_type="denovo",
            metric_status="available",
            failure_mode_count=1,
            expansion_action_count=1,
            warnings=["low_training_rows"],
            files=files,
        )

    monkeypatch.setattr(web_app, "run_dataset_model_loop", fake_model_loop)
    result = asyncio.run(
        web_app.run_dataset_model_loop_api(
            {
                "recipe_dir": str(tmp_path / "recipe"),
                "task_type": "denovo",
                "build_id": "model_loop_test",
            }
        )
    )

    assert result["status"] == "completed"
    assert result["task_cards"][0]["task_type"] == "model_loop:denovo"
    assert "failure_modes:1" in result["task_cards"][0]["warnings"]
    assert "discovery_requests:1" in result["task_cards"][0]["warnings"]
    assert "planned_repositories:pride,massive,iprox" in result["task_cards"][0]["warnings"]
    assert result["task_cards"][0]["target_schema"] == "multi_repository"
    assert result["model_informed_discovery_requests"]["request_count"] == 1
    assert "adapter_contract:external_adapter_metrics_schema_incomplete" in result["task_cards"][0]["warnings"]
    assert "model_eval_summary_json" in result["downloads"]
    assert "model_adapter_contract_json" in result["downloads"]
    assert "model_adapter_input_manifest_json" in result["downloads"]
    assert "external_model_metrics_json" in result["downloads"]
    assert "model_adapter_log" in result["downloads"]
    assert "model_loop_report_md" in result["downloads"]
    assert "model_informed_gap_report_json" in result["downloads"]
    assert "model_informed_discovery_requests_json" in result["downloads"]
    assert "model_informed_discovery_requests_csv" in result["downloads"]
    assert "model_informed_discovery_payloads_json" in result["downloads"]
    assert "model_informed_discovery_payload_queue_json" in result["downloads"]
    assert "model_informed_curation_queue_json" in result["downloads"]
    assert result["model_informed_discovery_payloads"]["payload_count"] == 1
    assert result["model_informed_discovery_payload_queue"]["ready_count"] == 1
    assert result["model_informed_curation_queue"]["row_count"] == 1
    assert result["model_informed_curation_queue"]["rows"][0]["planned_repositories"] == ["pride", "massive", "iprox"]
    assert result["model_informed_repository_plan"]["repository_strategy"] == "multi_repository"
    assert result["model_informed_repository_plan"]["planned_repositories"] == ["pride", "massive", "iprox"]


def test_web_model_informed_discovery_payload_from_direct_request():
    result = asyncio.run(
        web_app.model_informed_discovery_payload_api(
            {
                "request": {
                    "request_id": "model_gap_001",
                    "task_type": "denovo",
                    "dimension": "ptm",
                    "target": "modified_peptides",
                    "query": "modified peptide DDA small files",
                    "repositories": ["pride", "massive"],
                    "constraints": {
                        "modification_scope": "any_ptm",
                        "species_policy": "open",
                        "acquisition": "DDA",
                        "labeling_strategy": "label_free",
                    },
                    "requires_user_confirmation": True,
                }
            }
        )
    )

    payload = result["payload"]
    assert result["status"] == "ready"
    assert result["request_id"] == "model_gap_001"
    assert result["requires_user_confirmation"] is True
    assert payload["repository"] == "auto"
    assert payload["repository_strategy"] == "multi_repository"
    assert payload["planned_repositories"] == ["pride", "massive"]
    assert payload["task_type"] == "denovo"
    assert payload["ptm_type"] == "unknown_ptm"
    assert payload["species_policy"] == "open"
    assert payload["acquisition_mode"] == "dda"
    assert payload["prompt"] == "modified peptide DDA small files"
    assert payload["agentic"] is True


def test_web_model_informed_discovery_payload_from_build_request(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    build_dir = tmp_path / "ai_ready_builds" / "model_loop_build"
    build_dir.mkdir(parents=True)
    (build_dir / "model_informed_discovery_requests.json").write_text(
        json.dumps(
            {
                "request_count": 2,
                "requests": [
                    {
                        "request_id": "skip_me",
                        "task_type": "rt_prediction",
                        "query": "human RT DDA",
                    },
                    {
                        "request_id": "model_gap_ptyr",
                        "task_type": "ptm_denovo",
                        "dimension": "ptm",
                        "target": "phosphotyrosine enrichment",
                        "reason": "model failed on phosphotyrosine examples",
                        "repository": "pride",
                        "constraints": {
                            "species": ["mouse"],
                            "species_policy": "open",
                            "acquisition": "DDA",
                            "labeling_strategy": "TMT",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = asyncio.run(
        web_app.model_informed_discovery_payload_api(
            {
                "build_id": "model_loop_build",
                "request_id": "model_gap_ptyr",
                "max_projects": 3,
                "max_files": 12,
            }
        )
    )

    payload = result["payload"]
    assert result["status"] == "ready"
    assert payload["repository"] == "pride"
    assert payload["repository_strategy"] == "single_repository"
    assert payload["planned_repositories"] == ["pride"]
    assert payload["task_type"] == "ptm_denovo"
    assert payload["ptm_type"] == "phospho"
    assert payload["species"] == ["mouse"]
    assert payload["species_policy"] == "open"
    assert payload["labeling_strategy"] == "TMT"
    assert payload["max_projects"] == 3
    assert payload["max_files"] == 12


def test_web_make_data_scientist_agent_report_returns_downloads(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)

    def fake_report(**kwargs) -> DataScientistAgentReportResult:
        output_dir = Path(kwargs["output_dir"])
        assert kwargs["guidance_alignment_dir"] == tmp_path / "guidance_alignment"
        output_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "real_data_scientist_agent_report_md": str(output_dir / "real_data_scientist_agent_report.md"),
            "real_data_scientist_agent_summary_json": str(output_dir / "real_data_scientist_agent_summary.json"),
        }
        Path(files["real_data_scientist_agent_summary_json"]).write_text(
            json.dumps(
                {
                    "status": "ready",
                    "selected_count": 2,
                    "excluded_count": 1,
                    "leakage": {"status": "pass", "issue_counts": {}},
                    "model_loop": {"status": "completed"},
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        Path(files["real_data_scientist_agent_report_md"]).write_text("# Report\n", encoding="utf-8")
        return DataScientistAgentReportResult(
            status="ready",
            recipe_dir=str(kwargs["recipe_dir"]),
            output_dir=str(output_dir),
            selected_count=2,
            excluded_count=1,
            model_loop_status="completed",
            guidance_alignment_status="mostly_aligned",
            files=files,
        )

    monkeypatch.setattr(web_app, "make_data_scientist_agent_report", fake_report)
    result = asyncio.run(
        web_app.make_data_scientist_agent_report_api(
            {
                "recipe_dir": str(tmp_path / "recipe"),
                "model_loop_dir": str(tmp_path / "model_loop"),
                "guidance_alignment_dir": str(tmp_path / "guidance_alignment"),
                "build_id": "data_scientist_report_test",
            }
        )
    )

    assert result["status"] == "ready"
    assert result["task_cards"][0]["task_type"] == "data_scientist_agent_report"
    assert "real_data_scientist_agent_report_md" in result["downloads"]
    assert "real_data_scientist_agent_summary_json" in result["downloads"]


def test_web_make_guidance_alignment_report_returns_downloads(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)

    def fake_alignment(**kwargs) -> GuidanceAlignmentResult:
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "guidance_alignment_report_json": str(output_dir / "guidance_alignment_report.json"),
            "guidance_alignment_report_md": str(output_dir / "guidance_alignment_report.md"),
        }
        Path(files["guidance_alignment_report_json"]).write_text(
            json.dumps({"status": "mostly_aligned", "summary": {"achieved": 8, "partial": 1, "missing": 0}}),
            encoding="utf-8",
        )
        Path(files["guidance_alignment_report_md"]).write_text("# Guidance Alignment Report\n", encoding="utf-8")
        return GuidanceAlignmentResult(
            status="mostly_aligned",
            output_dir=str(output_dir),
            achieved_count=8,
            partial_count=1,
            missing_count=0,
            files=files,
        )

    monkeypatch.setattr(web_app, "make_guidance_alignment_report", fake_alignment)
    result = asyncio.run(
        web_app.make_guidance_alignment_report_api(
            {
                "recipe_dir": str(tmp_path / "recipe"),
                "build_id": "guidance_alignment_test",
            }
        )
    )

    assert result["status"] == "mostly_aligned"
    assert result["task_cards"][0]["task_type"] == "guidance_alignment"
    assert "guidance_alignment_report_json" in result["downloads"]
    assert "guidance_alignment_report_md" in result["downloads"]


def test_web_run_data_scientist_agent_loop_returns_downloads(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    seen_kwargs = {}

    def fake_loop(**kwargs) -> DataScientistAgentLoopResult:
        seen_kwargs.update(kwargs)
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        model_loop_dir = output_dir / "model_loop"
        model_loop_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "data_scientist_agent_loop_summary_json": str(output_dir / "data_scientist_agent_loop_summary.json"),
            "data_scientist_agent_loop_report_md": str(output_dir / "data_scientist_agent_loop_report.md"),
            "repository_audit:repository_audit_json": str(output_dir / "repository_audit.json"),
            "model_loop_dir": str(model_loop_dir),
        }
        Path(files["data_scientist_agent_loop_summary_json"]).write_text(
            json.dumps(
                {
                    "status": "completed_with_alignment_gaps",
                    "selected_count": 2,
                    "model_loop_dir": str(model_loop_dir),
                    "guidance_alignment_status": "mostly_aligned",
                    "warnings": ["model_strategy_comparison_not_run"],
                    "blockers": [],
                    "files": {"model_loop_dir": str(model_loop_dir)},
                }
            ),
            encoding="utf-8",
        )
        Path(files["data_scientist_agent_loop_report_md"]).write_text("# Loop\n", encoding="utf-8")
        (output_dir / "repository_audit.json").write_text(
            json.dumps({"repositories_attempted": ["pride", "massive"], "rows": []}),
            encoding="utf-8",
        )
        (model_loop_dir / "model_eval_summary.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "task_type": "denovo",
                    "adapter": "dry_run",
                    "metric_status": "available",
                    "validation": {"warnings": [], "blockers": []},
                    "metrics": {"total_rows": 9},
                }
            ),
            encoding="utf-8",
        )
        (model_loop_dir / "model_informed_discovery_payloads.json").write_text(
            json.dumps(
                {
                    "payload_count": 1,
                    "payloads": [
                        {
                            "request_id": "model_gap_multi_repo",
                            "payload": {
                                "repository": "auto",
                                "repository_strategy": "multi_repository",
                                "planned_repositories": ["pride", "massive", "iprox"],
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (model_loop_dir / "model_informed_discovery_payload_queue.json").write_text(
            json.dumps(
                {
                    "item_count": 1,
                    "ready_count": 1,
                    "review_count": 0,
                    "blocked_count": 0,
                    "items": [
                        {
                            "request_id": "model_gap_multi_repo",
                            "queue_status": "ready_for_user_confirmation",
                            "repository_strategy": "multi_repository",
                            "planned_repositories": ["pride", "massive", "iprox"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return DataScientistAgentLoopResult(
            status="completed_with_alignment_gaps",
            output_dir=str(output_dir),
            batch_dir=str(kwargs["batch_dir"]),
            task_type="denovo",
            recipe_status="ready",
            model_loop_status="completed",
            final_report_status="ready",
            guidance_alignment_status="mostly_aligned",
            warnings=["model_strategy_comparison_not_run"],
            files=files,
        )

    monkeypatch.setattr(web_app, "run_data_scientist_agent_loop", fake_loop)
    result = asyncio.run(
        web_app.run_data_scientist_agent_loop_api(
            {
                "batch_dir": str(tmp_path / "batch"),
                "task_type": "denovo",
                "build_id": "data_scientist_loop_test",
                "curation_default_decision": "needs_review",
                "curation_memory_dir": str(tmp_path / "memory"),
                "repository_smoke_dirs": f"{tmp_path / 'massive_smoke'}\n{tmp_path / 'iprox_smoke'}",
            }
        )
    )

    assert seen_kwargs["curation_default_decision"] == "needs_review"
    assert seen_kwargs["curation_memory_dir"] == tmp_path / "memory"
    assert seen_kwargs["repository_smoke_dirs"] == [tmp_path / "massive_smoke", tmp_path / "iprox_smoke"]
    assert result["status"] == "completed_with_alignment_gaps"
    assert any(card["task_type"] == "data_scientist_agent_loop" for card in result["task_cards"])
    audit_cards = [card for card in result["task_cards"] if card["task_type"] == "repository_audit"]
    assert audit_cards
    assert result["repository_audit"]["repositories_attempted"] == ["pride", "massive"]
    model_cards = [card for card in result["task_cards"] if card["task_type"] == "model_loop:denovo"]
    assert model_cards
    assert "planned_repositories:pride,massive,iprox" in model_cards[0]["warnings"]
    assert result["model_informed_repository_plan"]["planned_repositories"] == ["pride", "massive", "iprox"]
    assert "data_scientist_agent_loop_summary_json" in result["downloads"]
    assert "data_scientist_agent_loop_report_md" in result["downloads"]
    assert "repository_audit_json" in result["downloads"]


def test_web_ai_ready_locates_agent_run_outputs(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    agent_run_dir = tmp_path / "run"
    _write_tsv(
        agent_run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5}],
    )
    (agent_run_dir / "msdt").mkdir(parents=True)
    (agent_run_dir / "msdt" / "sample_fp_msdt.parquet").write_bytes(b"placeholder")

    result = asyncio.run(
        web_app.locate_agent_run_ai_ready_inputs_api(
            {
                "agent_run_dir": str(agent_run_dir),
                "build_id": "agent_run_locator_test",
            }
        )
    )

    assert result["status"] == "completed"
    assert result["agent_run_input_locations"]["summary"]["generic_ai_ready_available"] is True
    assert "agent_run_input_locations_json" in result["downloads"]


def test_web_ai_ready_builds_from_agent_run(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    agent_run_dir = tmp_path / "run"
    _write_tsv(
        agent_run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5, "PSM Q-Value": 0.001}],
    )

    result = asyncio.run(
        web_app.build_ai_ready_from_agent_run_api(
            {
                "agent_run_dir": str(agent_run_dir),
                "task_type": ["rt_prediction"],
                "build_id": "agent_run_build_test",
            }
        )
    )

    assert result["status"] == "completed"
    assert result["task_cards"][0]["task_type"] == "rt_prediction"
    assert result["task_cards"][0]["rows_out"] == 1
    assert "agent_run_build_summary_json" in result["downloads"]


def test_web_ai_ready_validates_mini_e2e_from_agent_run(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    agent_run_dir = tmp_path / "run"
    agent_run_dir.mkdir(parents=True, exist_ok=True)
    (agent_run_dir / "runtime.log").write_text(
        "\n".join(
            [
                "Process 'PhilosopherFilter' finished, exit code: 2",
                "Process returned non-zero exit code",
            ]
        ),
        encoding="utf-8",
    )
    _write_tsv(
        agent_run_dir / "fragpipe" / "exp" / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5, "PSM Q-Value": 0.001}],
    )

    result = asyncio.run(
        web_app.validate_agent_run_mini_e2e_api(
            {
                "agent_run_dir": str(agent_run_dir),
                "task_type": ["rt_prediction"],
                "build_id": "mini_e2e_test",
            }
        )
    )

    assert result["status"] == "completed"
    assert result["task_cards"][0]["task_type"] == "rt_prediction"
    assert result["task_cards"][0]["rows_out"] == 1
    assert result["recovery_cards"][0]["scope"] == "upstream_full"
    assert result["recovery_cards"][0]["primary_issue"] == "partial_outputs_available"
    assert result["recovery_cards"][0]["workflow_outcome"] == "failed_with_usable_partial_outputs"
    assert result["recovery_cards"][0]["usable_partial_outputs"] is True
    assert result["mini_e2e_summary"]["ai_ready_outcome"] == "completed_from_usable_partial_outputs"
    assert "mini_e2e_summary_json" in result["downloads"]
    assert "mini_e2e_upstream_recovery_json" in result["downloads"]
    assert "mini_e2e_upstream_recovery_md" in result["downloads"]
