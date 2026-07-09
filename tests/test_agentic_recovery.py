from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.agentic_recovery import run_agentic_recovery
from agent.ai_ready.agentic_recovery_batch import run_agentic_recovery_batch
from agent.ai_ready.mini_e2e import validate_agent_run_ai_ready_mini
from agent.cli import app


class _FakeLLM:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        return self.payload


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)
    return path


def _write_rawspectrum(path: Path, *, scan: int = 101) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "scan": scan,
                "charge": 2,
                "precursor_mz": 500.2,
                "mz_array": [98.06004, 147.1128],
                "intensity_array": [1000.0, 500.0],
            }
        ]
    ).to_parquet(path, index=False)
    return path


def _blocked_mini_without_peaklist(tmp_path: Path, *, with_rawspectrum: bool = False) -> tuple[Path, Path]:
    run_dir = tmp_path / "run"
    _write_tsv(
        run_dir / "fragpipe" / "exp" / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Modified Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )
    if with_rawspectrum:
        _write_rawspectrum(run_dir / "rawspectrum" / "sample_rawspectrum.parquet")
    mini_dir = tmp_path / "mini"
    result = validate_agent_run_ai_ready_mini(
        agent_run_dir=run_dir,
        task_types=["denovo"],
        output_dir=mini_dir,
        auto_recover=False,
    )
    assert result.status == "blocked"
    assert result.primary_issue == "missing_peaklist"
    return run_dir, mini_dir


def _manual_mini_summary(
    path: Path,
    *,
    status: str = "blocked",
    primary_issue: str,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    task_results: list[dict[str, object]] | None = None,
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "agent_run_dir": str(path / "run"),
        "blockers": blockers or [],
        "warnings": warnings or [],
        "primary_issue": primary_issue,
        "task_results": task_results or [],
    }
    (path / "mini_e2e_summary.json").write_text(json.dumps(payload), encoding="utf-8")
    (path / "agent_recovery_report.json").write_text(
        json.dumps(
            {
                "status": "needs_action",
                "primary_issue": primary_issue,
                "recommended_next_step": "test recommendation",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_agentic_recovery_plans_missing_peaklist_action_without_execution(tmp_path: Path):
    _, mini_dir = _blocked_mini_without_peaklist(tmp_path)

    result = run_agentic_recovery(
        mini_e2e_dir=mini_dir,
        output_dir=tmp_path / "agentic",
        allow_safe_actions=False,
        llm_client=None,
    )

    assert result.status == "planned"
    assert result.mode == "deterministic_react"
    assert result.planned_actions[0].action_type == "generate_peaklist_and_retry"
    assert result.executed_actions == []
    assert Path(result.files["agentic_recovery_plan_json"]).exists()
    assert Path(result.files["agentic_recovery_report_md"]).exists()


def test_agentic_recovery_executes_safe_peaklist_retry(tmp_path: Path):
    _, mini_dir = _blocked_mini_without_peaklist(tmp_path, with_rawspectrum=True)

    result = run_agentic_recovery(
        mini_e2e_dir=mini_dir,
        output_dir=tmp_path / "agentic",
        allow_safe_actions=True,
        llm_client=None,
    )

    assert result.status == "executed"
    assert result.executed_actions[0]["status"] == "completed"
    assert result.retry_summary_path is not None
    retry_summary = json.loads(Path(result.retry_summary_path).read_text(encoding="utf-8"))
    assert retry_summary["status"] == "completed"
    assert retry_summary["task_results"][0]["rows_out"] == 1


def test_agentic_recovery_rejects_unsupported_llm_action(tmp_path: Path):
    _, mini_dir = _blocked_mini_without_peaklist(tmp_path)
    llm = _FakeLLM(
        {
            "thought": "Try changing the FASTA.",
            "actions": [{"action_type": "change_fasta", "reason": "bad idea"}],
            "final_recommendation": "change fasta",
        }
    )

    result = run_agentic_recovery(
        mini_e2e_dir=mini_dir,
        output_dir=tmp_path / "agentic",
        allow_safe_actions=True,
        llm_client=llm,
    )

    assert result.mode == "llm_react"
    assert result.executed_actions == []
    assert result.planned_actions[0].action_type == "no_action"
    assert "unsupported_llm_action" in result.planned_actions[0].blockers


def test_agentic_recover_build_cli_writes_outputs(tmp_path: Path):
    _, mini_dir = _blocked_mini_without_peaklist(tmp_path)
    output_dir = tmp_path / "agentic"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "agentic-recover-build",
            "--mini-e2e-dir",
            str(mini_dir),
            "--output-dir",
            str(output_dir),
            "--no-use-llm",
            "--plan-only",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "planned"
    assert payload["planned_actions"][0]["action_type"] == "generate_peaklist_and_retry"
    assert (output_dir / "agentic_recovery_plan.json").exists()
    assert (output_dir / "agentic_recovery_trace.json").exists()
    assert (output_dir / "agentic_recovery_report.md").exists()


def test_agentic_recovery_plans_bounded_oom_memory_retry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_FRAGPIPE_RAM_GB", "8")
    monkeypatch.setenv("AGENT_RECOVERY_MAX_FRAGPIPE_RAM_GB", "12")
    mini_dir = _manual_mini_summary(
        tmp_path / "mini_oom",
        primary_issue="resource_oom",
        blockers=["fragpipe_oom"],
    )

    result = run_agentic_recovery(
        mini_e2e_dir=mini_dir,
        output_dir=tmp_path / "agentic_oom",
        allow_safe_actions=True,
        llm_client=None,
    )

    action = result.planned_actions[0]
    assert result.status == "planned"
    assert action.action_type == "recommend_memory_retry"
    assert action.safe_to_execute is False
    assert action.parameters["suggested_fragpipe_ram_gb"] == 12
    assert action.parameters["suggested_threads"] == 4
    assert "AGENT_FRAGPIPE_RAM_GB=12" in result.final_recommendation


def test_agentic_recovery_plans_partial_export_for_low_psm_msbooster(tmp_path: Path):
    mini_dir = _manual_mini_summary(
        tmp_path / "mini_low_psm",
        primary_issue="low_psm_msbooster",
        warnings=["RT regression using 0 PSMs"],
        task_results=[
            {"task_type": "rt_prediction", "status": "completed", "rows_out": 37, "blockers": []},
            {"task_type": "psm_scoring", "status": "completed", "rows_out": 37, "blockers": []},
        ],
    )

    result = run_agentic_recovery(
        mini_e2e_dir=mini_dir,
        output_dir=tmp_path / "agentic_low_psm",
        allow_safe_actions=True,
        llm_client=None,
    )

    action = result.planned_actions[0]
    assert result.status == "planned"
    assert action.action_type == "partial_export_from_existing_results"
    assert action.parameters["training_quality"] == "weak"
    assert result.executed_actions == []
    assert "partial AI-ready export" in action.expected_effect


def test_agentic_recovery_plans_partial_export_for_partial_outputs(tmp_path: Path):
    mini_dir = _manual_mini_summary(
        tmp_path / "mini_partial_outputs",
        primary_issue="partial_outputs_available",
        warnings=["PhilosopherFilter failed after PIN and MSDT outputs were written"],
        task_results=[
            {"task_type": "rt_prediction", "status": "completed", "rows_out": 6586, "blockers": []},
            {"task_type": "denovo", "status": "completed", "rows_out": 6586, "blockers": []},
        ],
    )

    result = run_agentic_recovery(
        mini_e2e_dir=mini_dir,
        output_dir=tmp_path / "agentic_partial_outputs",
        allow_safe_actions=True,
        llm_client=None,
    )

    action = result.planned_actions[0]
    assert result.status == "planned"
    assert action.action_type == "partial_export_from_existing_results"
    assert action.parameters["requires_full_rerun"] is False
    assert "conservative partial AI-ready export" in action.expected_effect


def test_agentic_recovery_plans_partial_export_for_msdt_feature_missing(tmp_path: Path):
    mini_dir = _manual_mini_summary(
        tmp_path / "mini_msdt_feature_missing",
        primary_issue="msdt_feature_missing",
        warnings=["Usecols do not match columns: delta_RT_loess, unweighted_spectral_entropy"],
        task_results=[
            {"task_type": "rt_prediction", "status": "completed", "rows_out": 1561, "blockers": []},
            {"task_type": "psm_scoring", "status": "completed", "rows_out": 2968, "blockers": []},
        ],
    )

    result = run_agentic_recovery(
        mini_e2e_dir=mini_dir,
        output_dir=tmp_path / "agentic_msdt_feature_missing",
        allow_safe_actions=True,
        llm_client=None,
    )

    action = result.planned_actions[0]
    assert result.status == "planned"
    assert action.action_type == "partial_export_from_existing_results"
    assert action.parameters["clean_full_retry_requires"] == "msbooster_compatible_workflow_or_converter_fix"
    assert "MSBooster-compatible" in action.expected_effect


def test_agentic_recovery_plans_spectrum_matching_retry(tmp_path: Path):
    mini_dir = _manual_mini_summary(
        tmp_path / "mini_spectrum_mismatch",
        primary_issue="spectrum_mismatch",
        blockers=["spectrum_not_matched"],
        task_results=[
            {"task_type": "denovo", "status": "blocked", "rows_out": 0, "blockers": ["spectrum_not_matched"]},
        ],
    )

    result = run_agentic_recovery(
        mini_e2e_dir=mini_dir,
        output_dir=tmp_path / "agentic_spectrum_mismatch",
        allow_safe_actions=True,
        llm_client=None,
    )

    action = result.planned_actions[0]
    assert result.status == "planned"
    assert action.action_type == "recommend_spectrum_matching_retry"
    assert action.safe_to_execute is False
    assert "native_id" in action.parameters["matching_strategies"]
    assert "spectrum id" in result.final_recommendation


def test_agentic_recovery_plans_smaller_candidate_for_slow_or_oversized_input(tmp_path: Path):
    mini_dir = _manual_mini_summary(
        tmp_path / "mini_download_slow",
        primary_issue="download_slow_or_failed",
        blockers=["input_too_large"],
    )

    result = run_agentic_recovery(
        mini_e2e_dir=mini_dir,
        output_dir=tmp_path / "agentic_download_slow",
        allow_safe_actions=True,
        llm_client=None,
    )

    action = result.planned_actions[0]
    assert result.status == "planned"
    assert action.action_type == "recommend_smaller_candidate"
    assert action.parameters["hard_size_limit_mb"] == 500
    assert result.executed_actions == []


def test_agentic_recovery_batch_summarizes_plans(tmp_path: Path):
    _, mini_peaklist = _blocked_mini_without_peaklist(tmp_path / "case_peaklist")
    mini_oom = _manual_mini_summary(
        tmp_path / "case_oom" / "mini_oom",
        primary_issue="resource_oom",
        blockers=["fragpipe_oom"],
    )
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "mini_e2e_batch_summary.json").write_text(
        json.dumps(
            {
                "run_results": [
                    {"output_dir": str(mini_peaklist), "agent_run_dir": str(tmp_path / "case_peaklist" / "run")},
                    {"output_dir": str(mini_oom), "agent_run_dir": str(tmp_path / "case_oom" / "run")},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = run_agentic_recovery_batch(
        batch_dir=batch_dir,
        output_dir=tmp_path / "agentic_batch",
        allow_safe_actions=False,
        llm_client=None,
    )

    assert result.status == "planned"
    assert len(result.run_results) == 2
    assert result.primary_issue_counts["missing_peaklist"] == 1
    assert result.primary_issue_counts["resource_oom"] == 1
    assert result.planned_action_counts["generate_peaklist_and_retry"] == 1
    assert result.planned_action_counts["recommend_memory_retry"] == 1
    assert Path(result.files["agentic_recovery_batch_summary_json"]).exists()
    assert Path(result.files["agentic_recovery_batch_summary_csv"]).exists()
    assert Path(result.files["agentic_recovery_batch_report_md"]).exists()


def test_agentic_recover_batch_cli_writes_outputs(tmp_path: Path):
    _, mini_dir = _blocked_mini_without_peaklist(tmp_path)
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    (batch_dir / "mini_e2e_batch_summary.json").write_text(
        json.dumps({"run_results": [{"output_dir": str(mini_dir)}]}),
        encoding="utf-8",
    )
    output_dir = tmp_path / "agentic_batch"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "agentic-recover-batch",
            "--batch-dir",
            str(batch_dir),
            "--output-dir",
            str(output_dir),
            "--no-use-llm",
            "--plan-only",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "planned"
    assert payload["run_count"] == 1
    assert payload["planned_action_counts"]["generate_peaklist_and_retry"] == 1
    assert (output_dir / "agentic_recovery_batch_summary.json").exists()
    assert (output_dir / "agentic_recovery_batch_summary.csv").exists()
    assert (output_dir / "agentic_recovery_batch_report.md").exists()
