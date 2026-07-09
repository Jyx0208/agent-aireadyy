from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent.ai_ready.guidance_alignment import make_guidance_alignment_report
from agent.cli import app


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_alignment_artifacts(root: Path) -> tuple[Path, Path, Path, Path]:
    recipe_dir = root / "recipe"
    discovery_dir = root / "discovery"
    model_dir = root / "model_loop"
    benchmark_dir = root / "benchmark"

    _write_json(
        recipe_dir / "dataset_recipe.json",
        {
            "selected_files": [
                {"task_type": "denovo", "repository": "pride", "project_accession": "PXD001", "source_file": "sample.mzML"},
                {
                    "task_type": "denovo",
                    "repository": "local",
                    "project_accession": "LOCAL_EXISTING",
                    "source_file": "local_search/sample.psm.tsv",
                    "search_result_path": "local_search/psm.tsv",
                    "peaklist_path": "local_search/spectra.mgf",
                    "ai_ready_outcome": "completed_from_existing_search_results",
                    "rows_out": 42,
                    "status": "completed",
                },
            ],
            "excluded_files": [],
        },
    )
    _write_json(recipe_dir / "dataset_split_plan.json", {"split_policy": "project_disjoint", "rows": [{"split": "train"}]})
    _write_json(recipe_dir / "leakage_risk_report.json", {"status": "pass", "issue_counts": {}})
    _write_json(recipe_dir / "split_baseline_evaluation.json", {"status": "ready", "interpretation": "agent_split_reduces_leakage_vs_random_baseline"})
    _write_json(recipe_dir / "hard_benchmark_manifest.json", {"rows": [{"tags": ["modified_peptide"]}]})
    _write_json(
        recipe_dir / "counterfactual_benchmark_manifest.json",
        {"rows": [{"case_type": "positive_training_case", "tags": ["label_available"]}], "case_type_counts": {"positive_training_case": 1}},
    )
    _write_json(recipe_dir / "coverage_gap_report.json", {"gaps": [{"dimension": "instrument"}]})
    _write_json(recipe_dir / "agent_expansion_plan.json", {"actions": [{"action": "plan_discovery_query"}]})
    _write_json(
        recipe_dir / "evidence_graph.json",
        {
            "nodes": [
                {"type": "project"},
                {"type": "file"},
                {"type": "sample"},
                {"type": "task"},
                {"type": "parquet"},
                {"type": "split"},
                {"type": "hard_case"},
                {"type": "counterfactual_case"},
                {"type": "curation_item"},
                {"type": "local_input", "repository": "local", "search_result_path": "local_search/psm.tsv"},
                {"type": "repository_attempt", "repository": "pride", "status": "completed"},
                {"type": "repository_attempt", "repository": "massive", "status": "completed"},
                {"type": "repository_attempt", "repository": "iprox", "status": "blocked"},
            ],
            "edges": [{"source": "a", "target": "b"}],
        },
    )
    _write_json(recipe_dir / "curation_queue.json", {"rows": [{"curation_type": "check_leakage_risk"}]})
    _write_json(recipe_dir / "curation_efficiency_report.json", {"review_reduction_rate": 0.5, "critical_issue_coverage": {"leakage": 1}})

    _write_json(
        discovery_dir / "task_ai_readiness_matrix.json",
        {
            "rows": [
                {
                    "task_type": "denovo",
                    "semantic_metadata_confidence": 0.82,
                    "ptm_evidence_terms": "Ti4+-IMAC;phosphotyrosine enrichment",
                    "ptm_enrichment_methods": "Ti4+-IMAC;anti-phosphotyrosine antibody enrichment",
                    "reasons": "semantic_ptm_evidence;ptm_enrichment_method_evidence;species_open_diversity_gain",
                },
                {
                    "task_type": "rt_prediction",
                    "semantic_metadata_confidence": 0.42,
                    "warnings": "isobaric_labeling_not_first_choice_for_task",
                    "reasons": "labeling_weak_for_task",
                },
            ]
        },
    )
    _write_json(
        discovery_dir / "quality_report.json",
        {
            "memory_feedback_summary": {
                "files_with_memory_feedback": 1,
                "action_counts": {"skip": 1},
                "repository_strategy_counts": {"multi_repository": 1},
                "planned_repository_counts": {"pride": 1, "massive": 1, "iprox": 1},
            },
            "semantic_metadata_confidence_mean": 0.62,
            "species_policy_distribution": {"open": 2},
            "labeling_strategy_distribution": {"label_free": 1, "TMT": 1},
            "ptm_enrichment_method_distribution": {"Ti4+-IMAC": 1, "anti-phosphotyrosine antibody enrichment": 1},
        },
    )
    _write_json(
        discovery_dir / "data_value_ranking.json",
        {
            "rows": [
                {
                    "source_file": "a.mzML",
                    "data_value_score": 0.8,
                    "memory_recommended_action": "skip",
                    "memory_planned_repositories": "pride;massive;iprox",
                }
            ]
        },
    )
    _write_json(
        discovery_dir / "data_value_strategy_eval.json",
        {"strategy_rows": [{"strategy": "agent_data_value"}], "interpretation": "agent_data_value_selection_outperforms_proxy_baselines"},
    )
    _write_json(
        discovery_dir / "repository_audit.json",
        {
            "requested_repository": "auto",
            "repositories_attempted": ["pride", "massive", "iprox"],
            "repository_counts": {"pride": 1, "massive": 1},
            "rows": [
                {
                    "repository": "pride",
                    "status": "completed",
                    "support_status": "remote_discovery_v1",
                    "selected_files": 1,
                    "next_step": "send_selected_to_batch_or_ai_ready_build",
                },
                {
                    "repository": "massive",
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
        },
    )

    _write_json(model_dir / "model_eval_summary.json", {"status": "completed"})
    _write_json(model_dir / "model_failure_modes.json", {"failure_mode_count": 1})
    _write_json(model_dir / "model_informed_gap_report.json", {"gaps": [{"target": "charge4"}]})
    _write_json(model_dir / "model_informed_expansion_plan.json", {"actions": [{"action": "plan_discovery_query"}]})
    _write_json(
        model_dir / "model_informed_discovery_payloads.json",
        {
            "request_count": 1,
            "payload_count": 1,
            "payloads": [
                {
                    "request_id": "model_gap_001",
                    "payload": {
                        "repository": "auto",
                        "repository_strategy": "multi_repository",
                        "planned_repositories": ["pride", "massive", "iprox"],
                    },
                }
            ],
        },
    )
    _write_json(
        model_dir / "model_informed_discovery_payload_queue.json",
        {
            "item_count": 1,
            "ready_count": 1,
            "review_count": 0,
            "blocked_count": 0,
            "items": [
                {
                    "request_id": "model_gap_001",
                    "queue_status": "ready_for_user_confirmation",
                    "repository_strategy": "multi_repository",
                    "planned_repositories": ["pride", "massive", "iprox"],
                }
            ],
        },
    )
    _write_json(model_dir / "model_strategy_comparison.json", {"interpretation": "agent_selected_dataset_outperforms_best_baseline_on_heldout_metrics"})

    _write_json(
        benchmark_dir / "benchmark_summary.json",
        {
            "status": "benchmark_complete",
            "run_count": 3,
            "acceptance": {
                "benchmark_complete": True,
                "has_clean_full_completed": True,
                "has_partial_output_recovery": True,
                "has_blocked_or_review_case": True,
            },
            "run_results": [
                {
                    "agent_run_dir": "runs/mini/local_partial",
                    "ai_ready_outcome": "completed_from_usable_partial_outputs",
                    "status": "completed",
                    "rt_rows_out": 37,
                }
            ],
        },
    )
    return recipe_dir, discovery_dir, model_dir, benchmark_dir


def test_make_guidance_alignment_report_marks_complete_artifact_set(tmp_path: Path) -> None:
    recipe_dir, discovery_dir, model_dir, benchmark_dir = _write_alignment_artifacts(tmp_path)

    result = make_guidance_alignment_report(
        output_dir=tmp_path / "alignment",
        recipe_dir=recipe_dir,
        discovery_dir=discovery_dir,
        model_loop_dir=model_dir,
        benchmark_dir=benchmark_dir,
    )

    assert result.status == "aligned"
    assert result.missing_count == 0
    payload = json.loads(Path(result.files["guidance_alignment_report_json"]).read_text(encoding="utf-8"))
    closed_loop = next(item for item in payload["requirements"] if item["name"] == "closed_loop_dataset_model_co_optimization")
    assert closed_loop["status"] == "achieved"
    assert any("model_informed_repositories=['iprox', 'massive', 'pride']" in item for item in closed_loop["evidence"])
    assert {item["name"] for item in payload["requirements"]} >= {
        "task_specific_ai_readiness_score",
        "semantic_metadata_interpretation_and_policy",
        "data_value_prediction",
        "multi_repository_discovery_and_audit",
        "local_and_existing_result_reuse",
        "closed_loop_dataset_model_co_optimization",
        "curation_memory_feedback_loop",
        "real_3_5_sample_benchmark_evidence",
    }
    report = Path(result.files["guidance_alignment_report_md"]).read_text(encoding="utf-8")
    assert "Guidance Alignment Report" in report


def test_make_guidance_alignment_report_flags_missing_real_benchmark(tmp_path: Path) -> None:
    recipe_dir, discovery_dir, model_dir, _ = _write_alignment_artifacts(tmp_path)

    result = make_guidance_alignment_report(
        output_dir=tmp_path / "alignment",
        recipe_dir=recipe_dir,
        discovery_dir=discovery_dir,
        model_loop_dir=model_dir,
    )

    assert result.status in {"partial", "mostly_aligned"}
    payload = json.loads(Path(result.files["guidance_alignment_report_json"]).read_text(encoding="utf-8"))
    benchmark_item = next(item for item in payload["requirements"] if item["name"] == "real_3_5_sample_benchmark_evidence")
    assert benchmark_item["status"] == "missing"


def test_make_guidance_alignment_report_requires_model_informed_repository_plan(tmp_path: Path) -> None:
    recipe_dir, discovery_dir, model_dir, benchmark_dir = _write_alignment_artifacts(tmp_path)
    (model_dir / "model_informed_discovery_payloads.json").unlink()
    (model_dir / "model_informed_discovery_payload_queue.json").unlink()

    result = make_guidance_alignment_report(
        output_dir=tmp_path / "alignment_missing_model_repo_plan",
        recipe_dir=recipe_dir,
        discovery_dir=discovery_dir,
        model_loop_dir=model_dir,
        benchmark_dir=benchmark_dir,
    )

    assert result.status == "mostly_aligned"
    payload = json.loads(Path(result.files["guidance_alignment_report_json"]).read_text(encoding="utf-8"))
    closed_loop = next(item for item in payload["requirements"] if item["name"] == "closed_loop_dataset_model_co_optimization")
    assert closed_loop["status"] == "partial"
    assert any("model_informed_repositories=[]" in item for item in closed_loop["evidence"])


def test_make_guidance_alignment_report_requires_curation_memory_feedback_loop(tmp_path: Path) -> None:
    recipe_dir, discovery_dir, model_dir, benchmark_dir = _write_alignment_artifacts(tmp_path)
    (discovery_dir / "quality_report.json").unlink()

    result = make_guidance_alignment_report(
        output_dir=tmp_path / "alignment_missing_memory_feedback",
        recipe_dir=recipe_dir,
        discovery_dir=discovery_dir,
        model_loop_dir=model_dir,
        benchmark_dir=benchmark_dir,
    )

    assert result.status == "mostly_aligned"
    payload = json.loads(Path(result.files["guidance_alignment_report_json"]).read_text(encoding="utf-8"))
    memory_loop = next(item for item in payload["requirements"] if item["name"] == "curation_memory_feedback_loop")
    assert memory_loop["status"] == "partial"
    assert any("files_with_memory_feedback=0" in item for item in memory_loop["evidence"])


def test_make_guidance_alignment_report_counts_curation_memory_writeback_as_partial(tmp_path: Path) -> None:
    recipe_dir, discovery_dir, model_dir, benchmark_dir = _write_alignment_artifacts(tmp_path)
    (discovery_dir / "quality_report.json").unlink()
    _write_json(discovery_dir / "data_value_ranking.json", {"rows": [{"source_file": "a.mzML", "data_value_score": 0.8}]})
    _write_json(
        recipe_dir / "curation_memory_update.json",
        {
            "status": "updated",
            "imported_decision_count": 2,
            "skipped_count": 0,
            "memory_summary": {"review_decision_count": 2},
        },
    )

    result = make_guidance_alignment_report(
        output_dir=tmp_path / "alignment_memory_writeback_only",
        recipe_dir=recipe_dir,
        discovery_dir=discovery_dir,
        model_loop_dir=model_dir,
        benchmark_dir=benchmark_dir,
    )

    assert result.status == "mostly_aligned"
    payload = json.loads(Path(result.files["guidance_alignment_report_json"]).read_text(encoding="utf-8"))
    memory_loop = next(item for item in payload["requirements"] if item["name"] == "curation_memory_feedback_loop")
    assert memory_loop["status"] == "partial"
    assert any("curation_memory_imported_decisions=2" in item for item in memory_loop["evidence"])
    assert any("Rerun discovery/value scoring" in item for item in memory_loop["remaining_work"])


def test_make_guidance_alignment_report_requires_semantic_metadata_policy_evidence(tmp_path: Path) -> None:
    recipe_dir, discovery_dir, model_dir, benchmark_dir = _write_alignment_artifacts(tmp_path)
    _write_json(discovery_dir / "task_ai_readiness_matrix.json", {"rows": [{"task_type": "denovo"}, {"task_type": "rt_prediction"}]})
    quality = json.loads((discovery_dir / "quality_report.json").read_text(encoding="utf-8"))
    quality.pop("semantic_metadata_confidence_mean", None)
    quality.pop("species_policy_distribution", None)
    quality.pop("labeling_strategy_distribution", None)
    quality.pop("ptm_enrichment_method_distribution", None)
    _write_json(discovery_dir / "quality_report.json", quality)

    result = make_guidance_alignment_report(
        output_dir=tmp_path / "alignment_missing_semantic_policy",
        recipe_dir=recipe_dir,
        discovery_dir=discovery_dir,
        model_loop_dir=model_dir,
        benchmark_dir=benchmark_dir,
    )

    assert result.status == "partial"
    payload = json.loads(Path(result.files["guidance_alignment_report_json"]).read_text(encoding="utf-8"))
    semantic = next(item for item in payload["requirements"] if item["name"] == "semantic_metadata_interpretation_and_policy")
    assert semantic["status"] == "missing"
    assert any("semantic_ptm_rows=0" in item for item in semantic["evidence"])


def test_make_guidance_alignment_report_requires_local_existing_result_reuse(tmp_path: Path) -> None:
    recipe_dir, discovery_dir, model_dir, benchmark_dir = _write_alignment_artifacts(tmp_path)
    _write_json(
        recipe_dir / "dataset_recipe.json",
        {"selected_files": [{"task_type": "denovo", "repository": "pride", "source_file": "sample.mzML"}], "excluded_files": []},
    )
    graph = json.loads((recipe_dir / "evidence_graph.json").read_text(encoding="utf-8"))
    graph["nodes"] = [
        node
        for node in graph["nodes"]
        if not (
            isinstance(node, dict)
            and (node.get("type") == "local_input" or node.get("repository") == "local")
        )
    ]
    _write_json(recipe_dir / "evidence_graph.json", graph)
    benchmark = json.loads((benchmark_dir / "benchmark_summary.json").read_text(encoding="utf-8"))
    benchmark.pop("run_results", None)
    _write_json(benchmark_dir / "benchmark_summary.json", benchmark)

    result = make_guidance_alignment_report(
        output_dir=tmp_path / "alignment_missing_local_reuse",
        recipe_dir=recipe_dir,
        discovery_dir=discovery_dir,
        model_loop_dir=model_dir,
        benchmark_dir=benchmark_dir,
    )

    assert result.status == "partial"
    payload = json.loads(Path(result.files["guidance_alignment_report_json"]).read_text(encoding="utf-8"))
    local_reuse = next(item for item in payload["requirements"] if item["name"] == "local_and_existing_result_reuse")
    assert local_reuse["status"] == "missing"
    assert any("local_or_existing_recipe_rows=0" in item for item in local_reuse["evidence"])


def test_make_guidance_alignment_report_cli(tmp_path: Path) -> None:
    recipe_dir, discovery_dir, model_dir, benchmark_dir = _write_alignment_artifacts(tmp_path)
    output_dir = tmp_path / "cli_alignment"
    result = CliRunner().invoke(
        app,
        [
            "make-guidance-alignment-report",
            "--output-dir",
            str(output_dir),
            "--recipe-dir",
            str(recipe_dir),
            "--discovery-dir",
            str(discovery_dir),
            "--model-loop-dir",
            str(model_dir),
            "--benchmark-dir",
            str(benchmark_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "aligned"
    assert (output_dir / "guidance_alignment_report.json").exists()
