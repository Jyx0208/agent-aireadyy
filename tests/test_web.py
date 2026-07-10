from __future__ import annotations

import asyncio
import json
import os
import time
import zipfile
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.web.app as web_app
from agent.web.app import StderrCapture, WebReporter, _create_task_inner, _tasks
from agent.web.app import _build_review_summary, _cleanup_expired_results, _list_public_results, _primary_project_error, _zip_output_dir
from agent.web.app import _start_ready_queued_tasks, _strip_ansi, submit_task_review
from agent.web.app import _try_start_queued_task, download_results, get_task, health, list_project_history


async def _llm_ok(_config):
    return True, "ok"


@pytest.fixture(autouse=True)
def _isolate_pride_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_PRIDE_CACHE_DIR", str(tmp_path / "pride-cache"))


def _value(value, confidence=0.9, source="test", conflict_flag=False):
    return SimpleNamespace(
        value=value,
        confidence=confidence,
        source=source,
        evidence_excerpt="test evidence",
        conflict_flag=conflict_flag,
    )


def test_primary_project_error_allows_known_project_local_source():
    result = SimpleNamespace(
        resolution=SimpleNamespace(
            primary_project=SimpleNamespace(
                project_accession="PXD079072",
                match_type="known_project_local_source",
                match_score=100,
                matched_file="Xinyi3_-80.mzML",
            ),
            needs_review=False,
            resolution_reason="local cached source",
        )
    )

    assert _primary_project_error(result) == ""


def test_known_local_source_reuses_manifest_context_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "data" / "local_mzml_samples" / "PXD079072" / "Xinyi3_-80.mzML"
    sample.parent.mkdir(parents=True)
    sample.write_text("mzml", encoding="utf-8")
    context_dir = tmp_path / "runs" / "Xinyi3_previous"
    context_dir.mkdir(parents=True)
    (context_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (context_dir / "attributes.json").write_text("{}", encoding="utf-8")
    manifest = [
        {
            "project_accession": "PXD079072",
            "file_name": "Xinyi3_-80.mzML",
            "local_path": "data/local_mzml_samples/PXD079072/Xinyi3_-80.mzML",
            "web_full_run_dir": "runs/Xinyi3_previous",
        }
    ]
    manifest_path = tmp_path / "data" / "local_mzml_samples" / "local_mzml_samples_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    source = web_app._known_local_source_from_input(str(sample))

    assert source is not None
    assert source["project_accession"] == "PXD079072"
    assert source["context_dir"] == str(context_dir)


def test_known_local_source_falls_back_to_runs_context(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "data" / "local_mzml_samples" / "PXD079072" / "Xinyi3_-80.mzML"
    sample.parent.mkdir(parents=True)
    sample.write_text("mzml", encoding="utf-8")
    stale_context_dir = tmp_path / "runs" / "Xinyi3_missing"
    fallback_context_dir = tmp_path / "runs" / "Xinyi3_-80__previous_success"
    fallback_context_dir.mkdir(parents=True)
    (fallback_context_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (fallback_context_dir / "attributes.json").write_text("{}", encoding="utf-8")
    (fallback_context_dir / "task_state.json").write_text(
        json.dumps({"project_accession": "PXD079072"}),
        encoding="utf-8",
    )
    manifest = [
        {
            "project_accession": "PXD079072",
            "file_name": "Xinyi3_-80.mzXML",
            "prepared_mzml_path": "data/local_mzml_samples/PXD079072/Xinyi3_-80.mzML",
            "web_full_run_dir": str(stale_context_dir),
        }
    ]
    manifest_path = tmp_path / "data" / "local_mzml_samples" / "local_mzml_samples_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    source = web_app._known_local_source_from_input(str(sample))

    assert source is not None
    assert source["context_dir"] == str(fallback_context_dir)


def _write_minimal_dda_mzml(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mzML xmlns="http://psi.hupo.org/ms/mzml">
  <run id="run1">
    <spectrumList count="2">
      <spectrum id="scan=1">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="1"/>
      </spectrum>
      <spectrum id="scan=2">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="2"/>
      </spectrum>
    </spectrumList>
  </run>
</mzML>
""",
        encoding="utf-8",
    )


def datetime_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def test_strip_ansi_removes_llm_color_codes():
    raw = "\x1b[0m\x1b[90mrecommended\x1b[0m_fasta_url"

    assert _strip_ansi(raw) == "recommended_fasta_url"


def test_strip_ansi_preserves_bracketed_metadata_sources():
    assert _strip_ansi("[massive.organisms, 0.90]") == "[massive.organisms, 0.90]"


def test_english_log_localizes_missing_fasta_review_issue():
    task_id = "english-fasta-review"
    _tasks[task_id] = {"logs": deque(maxlen=5), "ui_language": "en"}
    try:
        web_app._log(
            task_id,
            "error",
            "[阻断] 未找到可以从 UniProt 下载的真实 FASTA（物种：environmental samples <Bacillariophyta>）。默认占位 FASTA 不能用于真实搜库；请让 LLM 给出 UniProt proteome ID，或指定项目 FASTA。",
        )

        message = _tasks[task_id]["logs"][0]["message"]
        assert "No real UniProt FASTA" in message
        assert "environmental samples <Bacillariophyta>" in message
        assert not web_app._contains_cjk(message)
    finally:
        _tasks.pop(task_id, None)


def test_web_pipeline_does_not_emit_mojibake_blocking_marker():
    source = Path("src/agent/web/app.py").read_text(encoding="utf-8")

    assert "[闃" not in source
    assert 'f"[阻断] {issue}"' in source


def test_get_agent_audit_returns_active_task_audit_files(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    task_id = "audit-active"
    output_dir = tmp_path / "active-run"
    output_dir.mkdir()
    (output_dir / "agent_observation.json").write_text(
        json.dumps({"selected_project": {"project_accession": "PXD000001"}}),
        encoding="utf-8",
    )
    (output_dir / "agent_plan.json").write_text(json.dumps({"execution_gate": "allowed"}), encoding="utf-8")
    _tasks[task_id] = {"task_id": task_id, "output_dir": str(output_dir), "logs": deque(maxlen=10)}
    try:
        payload = asyncio.run(web_app.get_agent_audit(task_id))
    finally:
        _tasks.pop(task_id, None)

    assert payload["available"] is True
    assert payload["observation"]["selected_project"]["project_accession"] == "PXD000001"
    assert payload["plan"]["execution_gate"] == "allowed"
    assert payload["available_files"] == ["agent_observation.json", "agent_plan.json"]
    assert "agent_decision_trace.json" in payload["missing_files"]


def test_get_agent_audit_reads_history_record_and_ignores_bad_json(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    run_dir = tmp_path / "history-run"
    run_dir.mkdir()
    (run_dir / "task_history.json").write_text(
        json.dumps({"task_id": "audit-history", "output_dir": "history-run", "status": "completed"}),
        encoding="utf-8",
    )
    (run_dir / "agent_observation.json").write_text("{bad json", encoding="utf-8")
    (run_dir / "agent_decision_trace.json").write_text(json.dumps({"decisions": []}), encoding="utf-8")

    payload = asyncio.run(web_app.get_agent_audit("audit-history"))

    assert payload["available"] is True
    assert payload["observation"] is None
    assert payload["decision_trace"] == {"decisions": []}
    assert payload["available_files"] == ["agent_decision_trace.json"]
    assert "agent_observation.json" in payload["invalid_files"]


def test_get_agent_audit_reports_missing_audit_files(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    output_dir = tmp_path / "empty-run"
    output_dir.mkdir()
    _tasks["audit-empty"] = {"task_id": "audit-empty", "output_dir": str(output_dir), "logs": deque(maxlen=10)}
    try:
        payload = asyncio.run(web_app.get_agent_audit("audit-empty"))
    finally:
        _tasks.pop("audit-empty", None)

    assert payload["available"] is False
    assert payload["available_files"] == []
    assert set(payload["missing_files"]) == {
        "agent_observation.json",
        "agent_plan.json",
        "agent_decision_trace.json",
        "recovery_audit.json",
    }


def test_download_progress_is_throttled_but_completion_is_logged(monkeypatch):
    task_id = "progress-upsert-test"
    _tasks[task_id] = {"logs": deque(maxlen=10)}
    times = iter([0.0, 0.1, 0.2])
    monkeypatch.setattr(web_app, "monotonic", lambda: next(times))
    try:
        reporter = WebReporter(task_id)

        reporter(
            {
                "kind": "download_progress",
                "label": "uniprot_human_UP000005640.fasta",
                "downloaded": 1 * 1024 * 1024,
                "total": 20 * 1024 * 1024,
                "speed_bps": 1 * 1024 * 1024,
                "complete": False,
            }
        )
        reporter(
            {
                "kind": "download_progress",
                "label": "uniprot_human_UP000005640.fasta",
                "downloaded": 2 * 1024 * 1024,
                "total": 20 * 1024 * 1024,
                "speed_bps": 1 * 1024 * 1024,
                "complete": False,
            }
        )
        reporter(
            {
                "kind": "download_progress",
                "label": "uniprot_human_UP000005640.fasta",
                "downloaded": 20 * 1024 * 1024,
                "total": 20 * 1024 * 1024,
                "speed_bps": 1 * 1024 * 1024,
                "complete": True,
            }
        )

        first, second = list(_tasks[task_id]["logs"])
        assert first["key"] == second["key"]
        assert first["replace"] is True
        assert second["replace"] is True
        assert len(_tasks[task_id]["logs"]) == 2
        assert "Download complete" in second["message"]
        assert not web_app._contains_cjk(second["message"])
    finally:
        _tasks.pop(task_id, None)


def test_english_task_logs_are_localized_before_storage():
    task_id = "english-log-localization"
    _tasks[task_id] = {"logs": deque(maxlen=20), "ui_language": "en"}
    try:
        reporter = WebReporter(task_id)

        web_app._log(task_id, "info", "任务开始：sample.raw")
        web_app._step(task_id, 1, "[1/5] 解析 PRIDE 项目")
        reporter({"kind": "activity_start", "label": "正在查询 PRIDE Archive API 并匹配项目/文件…"})
        reporter("未找到匹配的 SDRF 行，且项目包含多个仪器；无法确定文件级仪器信息。")
        reporter("我们根据提供的元数据判断采集模式。项目标题和描述提到。")

        messages = [entry["message"] for entry in _tasks[task_id]["logs"]]
        assert any("Task started: sample.raw" in message for message in messages)
        assert any("Resolve PRIDE project" in message for message in messages)
        assert any("Querying PRIDE Archive API" in message for message in messages)
        assert any("file-level instrument cannot be determined" in message for message in messages)
        assert all(not web_app._contains_cjk(message) for message in messages)
    finally:
        _tasks.pop(task_id, None)


def test_chinese_task_logs_remain_chinese():
    task_id = "chinese-log-localization"
    _tasks[task_id] = {"logs": deque(maxlen=20), "ui_language": "zh"}
    try:
        web_app._log(task_id, "info", "任务开始：sample.raw")
        assert "任务开始" in _tasks[task_id]["logs"][0]["message"]
    finally:
        _tasks.pop(task_id, None)


def test_build_review_summary_extracts_fixed_sidebar_parameters(tmp_path):
    result = SimpleNamespace(
        attributes=SimpleNamespace(
            acquisition_mode=_value("DDA"),
            species=_value("Homo sapiens"),
            instrument_name=_value("Orbitrap Fusion"),
            enzyme=_value("Trypsin"),
            fixed_mods=_value(["Carbamidomethyl C"]),
            variable_mods=_value(["Oxidation M"]),
            search_parameter_hints=_value(
                {
                    "missed_cleavages": 2,
                    "precursor_tol": "4.5 ppm",
                    "fragment_tol": "0.5 Da",
                    "recommended_workflow_name": "Default.workflow",
                    "recommended_fasta_name": "human.fasta",
                }
            ),
        ),
        plan=SimpleNamespace(
            fragpipe_workflow_path=tmp_path / "Default.workflow",
            fasta_path=tmp_path / "human.fasta",
            fasta_selection_mode="reviewed",
            raw_data_type="mzml",
            thread_num=1,
            needs_review=True,
            blocking_issues=["搜库参数需要人工复核"],
        ),
    )

    summary = _build_review_summary(result)

    labels = [item["label"] for item in summary["items"]]
    assert "workflow" in labels
    assert "FASTA" in labels
    assert "precursor_tol" in labels
    assert summary["needs_review"] is True
    assert summary["issues"] == ["搜库参数需要人工复核"]


def test_build_review_summary_shows_normalized_plan_fasta_over_raw_llm_name(tmp_path):
    result = SimpleNamespace(
        attributes=SimpleNamespace(
            acquisition_mode=_value("DDA"),
            species=_value("Rattus norvegicus"),
            instrument_name=_value("Orbitrap Exploris 480"),
            enzyme=_value("Trypsin"),
            fixed_mods=_value(["Carbamidomethyl C"]),
            variable_mods=_value(["Oxidation M"]),
            search_parameter_hints=_value(
                {
                    "recommended_workflow_name": "Default.workflow",
                    "recommended_fasta_name": "uniprot-rat-reviewed.fasta",
                    "recommended_fasta_url": "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/reference_proteomes/Rodentia/UP000002494/UP000002494_10116.fasta.gz",
                    "recommended_fasta_source": "UniProt",
                },
                confidence=0.8,
                source="llm_confirmed",
            ),
        ),
        plan=SimpleNamespace(
            fragpipe_workflow_path=tmp_path / "Default.workflow",
            fasta_path=tmp_path / "uniprot_rat_UP000002494.fasta",
            fasta_selection_mode="inferred",
            fasta_download_url="https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/reference_proteomes/Rodentia/UP000002494/UP000002494_10116.fasta.gz",
            raw_data_type="mzml",
            thread_num=1,
            needs_review=False,
            blocking_issues=[],
        ),
    )

    summary = _build_review_summary(result)
    items = {item["label"]: item for item in summary["items"]}

    assert items["recommended_fasta_name"]["value"] == "uniprot_rat_UP000002494.fasta"
    assert items["recommended_fasta_name"]["source"] == "plan"
    assert "confidence" not in items["recommended_fasta_name"]
    assert items["recommended_fasta_url"]["value"] == result.plan.fasta_download_url
    assert items["recommended_fasta_url"]["source"] == "plan"
    assert items["recommended_fasta_source"]["value"] == "UniProt"
    assert items["recommended_fasta_source"]["source"] == "plan"


def test_parameter_audit_records_repository_and_transfer_metadata(tmp_path):
    result = SimpleNamespace(
        resolution=SimpleNamespace(
            primary_project=SimpleNamespace(
                repository="massive",
                project_accession="MSV000000001",
                native_accession="MSV000000001",
                px_accession="PXD000001",
                matched_file="raw/sample.raw",
                match_type="exact",
                match_score=100,
            ),
            needs_review=False,
        ),
        context=SimpleNamespace(repository="massive", native_accession="MSV000000001", px_accession="PXD000001"),
        attributes=SimpleNamespace(
            acquisition_mode=_value("DDA"),
            species=_value("Homo sapiens"),
            instrument_name=_value("Orbitrap"),
            enzyme=_value("Trypsin"),
            labeling_strategy=_value("label-free"),
            fixed_mods=_value(["Carbamidomethyl C"]),
            variable_mods=_value(["Oxidation M"]),
            search_parameter_hints=_value({"recommended_workflow_name": "Default.workflow"}),
        ),
        asset=SimpleNamespace(
            repository="massive",
            original_file_name="sample.raw",
            matched_project_file="raw/sample.raw",
            logical_path="raw/sample.raw",
            resolved_asset_type="raw",
            download_url="ftp://massive.ucsd.edu/MSV000000001/raw/sample.raw",
            download_urls=["ftp://massive.ucsd.edu/MSV000000001/raw/sample.raw"],
            transfer_method="ftp",
            expected_size_bytes=123,
            requires_conversion=True,
        ),
        plan=SimpleNamespace(
            source_file_name="sample.raw",
            source_data_path=tmp_path / "assets" / "prepared" / "sample.mzML",
            fragpipe_workflow_path=None,
            fasta_path=tmp_path / "fasta" / "human.fasta",
            fasta_selection_mode="inferred",
            fasta_download_url="https://example.test/human.fasta",
            raw_data_type="mzml",
            thread_num=2,
            manifest_path=tmp_path / "fragpipe" / "fragpipe-files.fp-manifest",
            expected_pin_path=tmp_path / "fragpipe" / "exp" / "sample_edited.pin",
            output_paths={"fp_msdt": tmp_path / "msdt" / "sample_fp_msdt.parquet"},
            rawspectrum_output_path=tmp_path / "rawspectrum" / "sample_rawspectrum.parquet",
            needs_review=False,
            blocking_issues=[],
        ),
    )

    audit = web_app._write_parameter_audit_files(tmp_path, "batch1", 1, "sample.raw", result)

    assert audit["repository"] == "massive"
    assert audit["project"]["repository"] == "massive"
    assert audit["project"]["native_accession"] == "MSV000000001"
    assert audit["project"]["px_accession"] == "PXD000001"
    assert audit["input"]["logical_path"] == "raw/sample.raw"
    assert audit["input"]["transfer_method"] == "ftp"
    assert audit["input"]["download_urls"] == ["ftp://massive.ucsd.edu/MSV000000001/raw/sample.raw"]
    manifest = json.loads((tmp_path / "msdt_input_manifest.json").read_text(encoding="utf-8"))
    assert "agent_observation.json" not in manifest["audit_files"]
    assert "agent_plan.json" not in manifest["audit_files"]
    assert "agent_decision_trace.json" not in manifest["audit_files"]


def test_agent_audit_package_does_not_fail_task_when_reporter_fails(monkeypatch, tmp_path):
    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit writer failed")

    def fail_report(_message):
        raise RuntimeError("reporter failed")

    monkeypatch.setattr("agent.agent_core.audit.write_agent_audit_for_result", fail_audit)

    web_app._write_agent_audit_package(tmp_path, SimpleNamespace(), report=fail_report)


def test_build_review_summary_includes_user_choices_for_multi_species_and_instruments(tmp_path):
    result = SimpleNamespace(
        attributes=SimpleNamespace(
            acquisition_mode=_value("DDA"),
            species=_value("Homo sapiens; Mus musculus", source="pride.organisms", conflict_flag=True),
            instrument_name=_value("Orbitrap Fusion Lumos; Q Exactive HF", source="pride.instruments", conflict_flag=True),
            enzyme=_value("Trypsin"),
            fixed_mods=_value(["Carbamidomethyl C"]),
            variable_mods=_value(["Oxidation M"]),
            search_parameter_hints=_value({"recommended_workflow_name": "Default.workflow"}),
        ),
        context=SimpleNamespace(
            metadata={
                "organisms": SimpleNamespace(value=["Homo sapiens", "Mus musculus"]),
                "instruments": SimpleNamespace(value=["Orbitrap Fusion Lumos", "Q Exactive HF"]),
            },
            project_files=[],
        ),
        plan=SimpleNamespace(
            fragpipe_workflow_path=tmp_path / "Default.workflow",
            fasta_path=tmp_path / "uniprot_human_UP000005640.fasta",
            fasta_selection_mode="inferred",
            fasta_download_url="https://rest.uniprot.org/uniprotkb/stream?compressed=false&format=fasta&query=%28proteome%3AUP000005640%29",
            raw_data_type="mzml",
            thread_num=1,
            needs_review=True,
            blocking_issues=[
                "未找到匹配的 SDRF 行，且项目包含多个物种；无法确定文件级物种信息。",
                "未找到匹配的 SDRF 行，且项目包含多个仪器；无法确定文件级仪器信息。",
            ],
        ),
    )

    summary = _build_review_summary(result)

    assert summary["review_options"] == [
        {"field": "species", "label": "选择物种", "values": ["Homo sapiens", "Mus musculus"]},
        {"field": "instrument_name", "label": "选择仪器", "values": ["Orbitrap Fusion Lumos", "Q Exactive HF"]},
    ]


def test_submit_task_review_requeues_blocked_task_with_user_overrides(monkeypatch, tmp_path):
    task_id = "review-task"
    monkeypatch.setattr(web_app, "_start_ready_queued_tasks", lambda: [])
    _tasks[task_id] = {
        "task_id": task_id,
        "input_value": "sample.raw",
        "submitter": "Alice",
        "output_dir": str(tmp_path / "sample"),
        "status": "blocked",
        "created_at": datetime.now(UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "logs": deque(maxlen=20),
        "step": 4,
        "total_steps": 5,
        "blocking_issues": ["未找到匹配的 SDRF 行，且项目包含多个物种；无法确定文件级物种信息。"],
        "llm_config": {"api_key": "sk-test", "base_url": "https://api.example.test", "model": "model", "timeout": "1"},
    }
    try:
        result = asyncio.run(
            submit_task_review(
                task_id,
                {"overrides": {"species": "Homo sapiens", "instrument_name": "Q Exactive HF"}},
            )
        )

        assert result["status"] == "queued"
        assert _tasks[task_id]["review_overrides"] == {"species": "Homo sapiens", "instrument_name": "Q Exactive HF"}
        assert _tasks[task_id]["blocking_issues"] == []
        assert "finished_at" not in _tasks[task_id]
    finally:
        _tasks.pop(task_id, None)


def test_list_public_results_discovers_existing_run_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    project = tmp_path / "public-project"
    project.mkdir()
    (project / "result.txt").write_text("ok", encoding="utf-8")
    _zip_output_dir(project)

    results = _list_public_results()

    assert len(results) == 1
    assert results[0]["result_id"] == "public-project"
    assert results[0]["can_download"] is True
    assert results[0]["file_count"] == 1
    assert results[0]["expires_in_seconds"] <= 1800


def test_list_public_results_skips_protected_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setenv("AGENT_PROTECTED_RESULT_DIRS", "configured-protected")

    public = tmp_path / "public-project"
    public.mkdir()
    (public / "result.txt").write_text("ok", encoding="utf-8")

    marked = tmp_path / "marked-protected"
    marked.mkdir()
    (marked / ".agent_keep").write_text("keep", encoding="utf-8")
    (marked / "large.txt").write_text("skip", encoding="utf-8")

    configured = tmp_path / "configured-protected"
    configured.mkdir()
    (configured / "large.txt").write_text("skip", encoding="utf-8")

    results = _list_public_results()

    assert [item["result_id"] for item in results] == ["public-project"]


def test_cleanup_results_keeps_only_four_latest_downloadable_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setenv("AGENT_MAX_RESULT_PROJECTS", "4")
    monkeypatch.setenv("AGENT_RESULT_RETENTION_SECONDS", "1800")
    active_project = tmp_path / "active-project"
    active_project.mkdir()
    (active_project / "result.txt").write_text("active", encoding="utf-8")
    base_time = time.time() - 1000
    for idx in range(6):
        project = tmp_path / f"done-{idx}"
        project.mkdir()
        (project / "result.txt").write_text(str(idx), encoding="utf-8")
        (project / "task_history.json").write_text(
            json.dumps({"task_id": f"done-{idx}", "status": "completed", "input_value": f"done-{idx}.raw"}),
            encoding="utf-8",
        )
        stamp = base_time + idx
        os.utime(project / "result.txt", (stamp, stamp))
        os.utime(project, (stamp, stamp))
    _tasks["active"] = {"status": "running", "output_dir": str(active_project), "logs": deque(maxlen=10)}

    try:
        removed = _cleanup_expired_results()
    finally:
        _tasks.pop("active", None)

    assert removed == ["done-0", "done-1"]
    assert not (tmp_path / "done-0").exists()
    assert not (tmp_path / "done-1").exists()
    assert (tmp_path / "done-2").exists()
    assert (tmp_path / "done-5").exists()
    assert active_project.exists()


def test_cleanup_results_removes_expired_process_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setenv("AGENT_RESULT_RETENTION_SECONDS", "1800")
    old_stamp = time.time() - 1900
    for name, status in [("failed-process", "failed"), ("completed-process", "completed")]:
        project = tmp_path / name
        (project / "assets" / "downloads").mkdir(parents=True)
        (project / "assets" / "downloads" / "sample.raw").write_text("raw", encoding="utf-8")
        (project / "task_history.json").write_text(
            json.dumps({"task_id": name, "status": status, "input_value": f"{name}.raw"}),
            encoding="utf-8",
        )
        for path in project.rglob("*"):
            os.utime(path, (old_stamp, old_stamp))
        os.utime(project, (old_stamp, old_stamp))

    removed = _cleanup_expired_results()

    assert set(removed) == {"failed-process", "completed-process"}
    assert not (tmp_path / "failed-process").exists()
    assert not (tmp_path / "completed-process").exists()


def test_cleanup_results_preserves_protected_validation_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setenv("AGENT_RESULT_RETENTION_SECONDS", "1800")
    monkeypatch.delenv("AGENT_PROTECTED_RESULT_DIRS", raising=False)
    old_stamp = time.time() - 1900
    for name in ["baseline_validation", "ai_ready_builds", "marked-old-run", "ordinary-old-run"]:
        project = tmp_path / name
        project.mkdir()
        (project / "result.txt").write_text(name, encoding="utf-8")
        if name == "marked-old-run":
            (project / ".agent_keep").write_text("keep", encoding="utf-8")
        (project / "task_history.json").write_text(
            json.dumps({"task_id": name, "status": "completed", "input_value": f"{name}.mzML"}),
            encoding="utf-8",
        )
        for path in project.rglob("*"):
            os.utime(path, (old_stamp, old_stamp))
        os.utime(project, (old_stamp, old_stamp))

    removed = _cleanup_expired_results()

    assert "ordinary-old-run" in removed
    assert "baseline_validation" not in removed
    assert "ai_ready_builds" not in removed
    assert "marked-old-run" not in removed
    assert (tmp_path / "baseline_validation").exists()
    assert (tmp_path / "ai_ready_builds").exists()
    assert (tmp_path / "marked-old-run").exists()
    assert not (tmp_path / "ordinary-old-run").exists()


def test_known_local_source_from_input_detects_pxd_acquisition_file(tmp_path):
    source = tmp_path / "data" / "PXD123456" / "sample.mzML"
    source.parent.mkdir(parents=True)
    source.write_text("mzml", encoding="utf-8")

    result = web_app._known_local_source_from_input(str(source))

    assert result == {
        "source_path": str(source),
        "project_accession": "PXD123456",
        "matched_file": "sample.mzML",
    }


def test_known_local_source_from_input_ignores_non_acquisition_or_unknown_project(tmp_path):
    search_result = tmp_path / "PXD123456" / "psm.tsv"
    search_result.parent.mkdir(parents=True)
    search_result.write_text("psm", encoding="utf-8")
    no_project = tmp_path / "sample.mzML"
    no_project.write_text("mzml", encoding="utf-8")

    assert web_app._known_local_source_from_input(str(search_result)) is None
    assert web_app._known_local_source_from_input(str(no_project)) is None


def test_cleanup_pride_cache_removes_old_files_only_when_idle(monkeypatch, tmp_path):
    runs_dir = tmp_path / "runs"
    cache_dir = tmp_path / "cache"
    runs_dir.mkdir()
    (cache_dir / "PXD123456").mkdir(parents=True)
    cache_file = cache_dir / "PXD123456" / "sample.raw"
    cache_file.write_text("raw", encoding="utf-8")
    old_stamp = time.time() - 1900
    os.utime(cache_file, (old_stamp, old_stamp))
    os.utime(cache_file.parent, (old_stamp, old_stamp))
    monkeypatch.setattr(web_app, "_runs_dir", runs_dir)
    monkeypatch.setenv("AGENT_PRIDE_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("AGENT_RESULT_RETENTION_SECONDS", "1800")

    _tasks["active-cache"] = {"task_id": "active-cache", "status": "running", "logs": deque(maxlen=10)}
    try:
        assert _cleanup_expired_results() == []
        assert cache_file.exists()
    finally:
        _tasks.pop("active-cache", None)

    removed = _cleanup_expired_results()

    assert any(item.startswith("pride-cache/") for item in removed)
    assert not cache_file.exists()


def test_create_task_accepts_numeric_timeout_from_browser_payload(monkeypatch):
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_pipeline_thread", lambda _task_id: None)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("AGENT_LLM_TIMEOUT", raising=False)

    result = asyncio.run(
        _create_task_inner(
            {
                "input_value": "P17_severe_NoPOTS.raw",
                "llm_config": {
                    "api_key": "sk-test",
                    "base_url": " https://api.example.com ",
                    "model": " deepseek-test ",
                    "timeout": 1200,
                },
            }
        )
    )

    task_id = result.get("task_id")
    try:
        assert "error" not in result
        assert "AGENT_LLM_API_KEY" not in os.environ
        assert "AGENT_LLM_BASE_URL" not in os.environ
        assert "AGENT_LLM_MODEL" not in os.environ
        assert "AGENT_LLM_TIMEOUT" not in os.environ
        assert _tasks[task_id]["llm_config"] == {
            "api_key": "sk-test",
            "base_url": "https://api.example.com",
            "model": "deepseek-test",
            "timeout": "1200",
        }
    finally:
        if task_id:
            _tasks.pop(task_id, None)


def test_create_task_persists_reviewed_fasta_input(monkeypatch):
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_pipeline_thread", lambda _task_id: None)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)

    result = asyncio.run(
        _create_task_inner(
            {
                "input_value": "sample.raw",
                "reviewed_fasta": "https://example.test/reference.fasta",
                "llm_config": {"api_key": "sk-test"},
            }
        )
    )

    task_id = result.get("task_id")
    try:
        assert "error" not in result
        assert _tasks[task_id]["reviewed_fasta_url"] == "https://example.test/reference.fasta"
        assert _tasks[task_id]["reviewed_fasta_path"] is None
    finally:
        if task_id:
            _tasks.pop(task_id, None)


def test_create_task_persists_submitter_history_without_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_pipeline_thread", lambda _task_id: None)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)

    result = asyncio.run(
        _create_task_inner(
            {
                "input_value": "sample.raw",
                "submitter": "Alice",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    task_id = result.get("task_id")
    try:
        assert result["submitter"] == "Alice"
        detail = asyncio.run(get_task(task_id))
        assert detail["submitter"] == "Alice"
        history_path = tmp_path / "sample" / "task_history.json"
        data = history_path.read_text(encoding="utf-8")
        assert "Alice" in data
        assert "sk-secret" not in data
        assert "api_key" not in data
        assert "llm_config" not in data
    finally:
        if task_id:
            _tasks.pop(task_id, None)


def test_create_task_persists_run_mode_and_language(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_pipeline_thread", lambda _task_id: None)

    result = asyncio.run(
        _create_task_inner(
            {
                "input_value": "sample.raw",
                "submitter": "Alice",
                "run_mode": "parameters",
                "resource_policy": "fast",
                "ui_language": "zh",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    task_id = result.get("task_id")
    try:
        assert result["run_mode"] == "parameters"
        assert result["resource_policy"] == "fast"
        assert result["ui_language"] == "zh"
        assert _tasks[task_id]["run_mode"] == "parameters"
        assert _tasks[task_id]["resource_policy"] == "fast"
        assert _tasks[task_id]["ui_language"] == "zh"
        detail = asyncio.run(get_task(task_id))
        assert detail["run_mode"] == "parameters"
        assert detail["resource_policy"] == "fast"
        assert detail["ui_language"] == "zh"
        history = json.loads((tmp_path / "sample" / "task_history.json").read_text(encoding="utf-8"))
        assert history["run_mode"] == "parameters"
        assert history["resource_policy"] == "fast"
        assert history["ui_language"] == "zh"
        assert "sk-secret" not in json.dumps(history)
    finally:
        if task_id:
            _tasks.pop(task_id, None)


def test_create_task_persists_repository_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_pipeline_thread", lambda _task_id: None)

    result = asyncio.run(
        _create_task_inner(
            {
                "input_value": "MSV000000001.raw",
                "submitter": "Alice",
                "repository": "massive",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    task_id = result.get("task_id")
    try:
        assert result["repository"] == "massive"
        assert _tasks[task_id]["repository"] == "massive"
        detail = asyncio.run(get_task(task_id))
        assert detail["repository"] == "massive"
        history = json.loads((tmp_path / "MSV000000001" / "task_history.json").read_text(encoding="utf-8"))
        assert history["repository"] == "massive"
    finally:
        if task_id:
            _tasks.pop(task_id, None)


def test_create_task_defaults_to_full_workflow_and_english(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_pipeline_thread", lambda _task_id: None)
    monkeypatch.setenv("AGENT_WEB_FULL_WORKFLOW_ENABLED", "1")

    result = asyncio.run(
        _create_task_inner(
            {
                "input_value": "sample.raw",
                "submitter": "Alice",
                "run_mode": "unexpected",
                "ui_language": "fr",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    task_id = result.get("task_id")
    try:
        assert result["run_mode"] == "full"
        assert result["ui_language"] == "en"
        assert _tasks[task_id]["run_mode"] == "full"
        assert _tasks[task_id]["ui_language"] == "en"
    finally:
        if task_id:
            _tasks.pop(task_id, None)


def test_create_task_downgrades_full_workflow_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_pipeline_thread", lambda _task_id: None)
    monkeypatch.setenv("AGENT_WEB_FULL_WORKFLOW_ENABLED", "0")

    result = asyncio.run(
        _create_task_inner(
            {
                "input_value": "sample.raw",
                "submitter": "Alice",
                "run_mode": "full",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    task_id = result.get("task_id")
    try:
        assert result["run_mode"] == "prepare"
        assert _tasks[task_id]["run_mode"] == "prepare"
    finally:
        if task_id:
            _tasks.pop(task_id, None)


def test_create_parameter_batch_persists_manifest_without_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._run_llm_check", _llm_ok, raising=False)
    started: list[str] = []
    monkeypatch.setattr("agent.web.app._start_parameter_batch_thread", lambda batch_id: started.append(batch_id), raising=False)

    result = asyncio.run(
        web_app.create_parameter_batch(
            {
                "input_text": "sample_a.raw\n\nsample_b.raw\n",
                "submitter": "Alice",
                "jobs": 8,
                "ui_language": "zh",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    batch_id = result.get("batch_id")
    try:
        assert result["status"] == "queued"
        assert result["item_count"] == 2
        assert result["jobs"] == 2
        assert started == [batch_id]
        manifest_path = tmp_path / "_batches" / batch_id / "batch_manifest.json"
        manifest_text = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        assert manifest["submitter"] == "Alice"
        assert manifest["ui_language"] == "zh"
        assert manifest["items"][0]["input"] == "sample_a.raw"
        assert "sk-secret" not in manifest_text
        assert "api_key" not in manifest_text

        detail = asyncio.run(web_app.get_parameter_batch(batch_id))
        assert detail["batch_id"] == batch_id
        assert detail["status"] == "queued"
        assert detail["can_download"] is False
    finally:
        if batch_id:
            web_app._batches.pop(batch_id, None)


def test_create_parameter_batch_persists_discovery_context(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._run_llm_check", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_parameter_batch_thread", lambda _batch_id: None, raising=False)

    result = asyncio.run(
        web_app.create_parameter_batch(
            {
                "inputs": ["sample_a.raw"],
                "input_records": [
                    {
                        "file_name": "sample_a.raw",
                        "project_accession": "PXD000001",
                        "download_url": "https://example.test/sample_a.raw",
                        "file_role": "raw_acquisition",
                        "task_readiness_status": "weak_ready",
                    }
                ],
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    batch_id = result.get("batch_id")
    try:
        manifest = json.loads((tmp_path / "_batches" / batch_id / "batch_manifest.json").read_text(encoding="utf-8"))
        context = manifest["items"][0]["discovery_context"]
        assert context["project_accession"] == "PXD000001"
        assert context["file_name"] == "sample_a.raw"
        assert context["download_url"] == "https://example.test/sample_a.raw"
        public = asyncio.run(web_app.get_parameter_batch(batch_id))
        assert public["items"][0]["discovery_context"]["project_accession"] == "PXD000001"
    finally:
        if batch_id:
            web_app._batches.pop(batch_id, None)


def test_run_parameter_batch_writes_excel_and_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._run_llm_check", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_parameter_batch_thread", lambda _batch_id: None, raising=False)

    def attr(value):
        return {"value": value, "confidence": 1.0, "source": "test", "evidence_excerpt": "", "conflict_flag": False}

    class FakeService:
        def __init__(self, **_kwargs):
            self.reporter = _kwargs.get("reporter")

        def plan_dda_run_from_repository(self, task, output_dir, **_kwargs):
            output_dir = Path(output_dir)
            if callable(self.reporter):
                self.reporter(f"planned {task.file_name}")
            return SimpleNamespace(
                resolution=SimpleNamespace(
                    primary_project=SimpleNamespace(
                        project_accession="PXDTEST",
                        matched_file=task.file_name,
                        match_type="exact",
                        match_score=100,
                    ),
                    needs_review=False,
                    resolution_confidence=1.0,
                ),
                context=SimpleNamespace(metadata={"organisms": {"value": ["Homo sapiens"]}}, project_files=[]),
                attributes=SimpleNamespace(
                    acquisition_mode=SimpleNamespace(**attr("DDA")),
                    species=SimpleNamespace(**attr("Homo sapiens")),
                    instrument_name=SimpleNamespace(**attr("Orbitrap Fusion")),
                    enzyme=SimpleNamespace(**attr("Trypsin")),
                    labeling_strategy=SimpleNamespace(**attr("label-free")),
                    fixed_mods=SimpleNamespace(**attr(["C[57.02]"])),
                    variable_mods=SimpleNamespace(**attr(["M[15.99]"])),
                    search_parameter_hints=SimpleNamespace(
                        **attr(
                            {
                                "recommended_workflow_name": "Default.workflow",
                                "recommended_fasta_name": "human.fasta",
                            }
                        )
                    ),
                ),
                plan=SimpleNamespace(
                    task_id=task.task_id,
                    source_file_name=task.file_name,
                    fragpipe_workflow_path=output_dir / "workflows" / "Default.workflow",
                    fasta_path=output_dir / "fasta" / "human.fasta",
                    fasta_selection_mode="inferred",
                    fasta_download_url="https://example.test/human.fasta",
                    raw_data_type="mzml",
                    thread_num=2,
                    needs_review=False,
                    blocking_issues=[],
                ),
                asset=SimpleNamespace(),
            )

        def write_task_bundle(self, output_dir, resolution, context, attributes, plan, **_kwargs):
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            project = {
                "primary_project": {
                    "project_accession": resolution.primary_project.project_accession,
                    "matched_file": resolution.primary_project.matched_file,
                    "match_type": resolution.primary_project.match_type,
                    "match_score": resolution.primary_project.match_score,
                },
                "needs_review": False,
            }
            attributes_json = {
                key: value.__dict__
                for key, value in attributes.__dict__.items()
                if not key.startswith("_")
            }
            (output_dir / "project_resolution.json").write_text(json.dumps(project), encoding="utf-8")
            (output_dir / "metadata.json").write_text(
                json.dumps({"project_accession": "PXDTEST", "metadata": context.metadata}),
                encoding="utf-8",
            )
            (output_dir / "attributes.json").write_text(json.dumps(attributes_json), encoding="utf-8")
            (output_dir / "decision_trace.json").write_text(
                json.dumps(
                    {
                        "source_file_name": plan.source_file_name,
                        "source_data_path": str(output_dir / "assets" / "prepared" / f"{Path(plan.source_file_name).stem}.mzML"),
                        "raw_data_type": plan.raw_data_type,
                        "fragpipe_workflow_path": str(plan.fragpipe_workflow_path),
                        "fasta_path": str(plan.fasta_path),
                        "fasta_download_url": plan.fasta_download_url,
                        "fasta_selection_mode": plan.fasta_selection_mode,
                        "converter_config_path": str(output_dir / "converter_config.json"),
                        "manifest_path": str(output_dir / "fragpipe" / "fragpipe-files.fp-manifest"),
                        "expected_pin_path": str(output_dir / "fragpipe" / "exp" / f"{Path(plan.source_file_name).stem}_edited.pin"),
                        "output_paths": {"fp_msdt": str(output_dir / "msdt" / f"{Path(plan.source_file_name).stem}_fp_msdt.parquet")},
                        "thread_num": plan.thread_num,
                        "needs_review": plan.needs_review,
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "asset_resolution.json").write_text(
                json.dumps(
                    {
                        "original_file_name": plan.source_file_name,
                        "matched_project_file": plan.source_file_name,
                        "download_url": f"https://example.test/{plan.source_file_name}",
                        "resolved_asset_type": "raw",
                        "requires_conversion": True,
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "converter_config.json").write_text(
                json.dumps({"generate_fragpipe_search_result": {"workflow_path": str(plan.fragpipe_workflow_path)}}),
                encoding="utf-8",
            )
            plan.fragpipe_workflow_path.parent.mkdir(parents=True, exist_ok=True)
            plan.fragpipe_workflow_path.write_text(
                "msfragger.search_enzyme_name_1=stricttrypsin\nmsfragger.search_enzyme_cut_1=KR\n",
                encoding="utf-8",
            )
            (output_dir / "task_state.json").write_text(
                json.dumps({"status": "completed", "stage": "planning", "source_file": plan.source_file_name}),
                encoding="utf-8",
            )

    monkeypatch.setattr("agent.orchestrator.pipeline.AgentService", FakeService)

    created = asyncio.run(
        web_app.create_parameter_batch(
            {
                "input_text": "sample_a.raw\nsample_b.raw",
                "submitter": "Alice",
                "jobs": 2,
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )
    batch_id = created["batch_id"]
    try:
        web_app._run_parameter_batch(batch_id)

        detail = asyncio.run(web_app.get_parameter_batch(batch_id))
        assert detail["status"] == "completed"
        assert detail["completed_items"] == 2
        assert detail["failed_items"] == 0
        assert detail["can_download"] is True
        assert detail["submitter"] == "Alice"
        assert any("Batch started" in event["message"] for event in detail["events"])
        assert any("Excel report written" in event["message"] for event in detail["events"])
        assert any("planned sample_a.raw" in line for line in detail["items"][0]["log_tail"])
        excel_path = tmp_path / "_batches" / batch_id / "benchmark_results.xlsx"
        assert excel_path.exists()
        audit_path = tmp_path / "_batches" / batch_id / "items" / "001_sample_a" / "parameter_audit.json"
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        assert audit["workflow"]["name"] == "Default.workflow"
        assert audit["input"]["download_url"] == "https://example.test/sample_a.raw"
        item_dir = tmp_path / "_batches" / batch_id / "items" / "001_sample_a"
        assert (item_dir / "agent_observation.json").exists()
        assert (item_dir / "agent_plan.json").exists()
        assert (item_dir / "agent_decision_trace.json").exists()
        audit_response = asyncio.run(web_app.download_parameter_batch_audit(batch_id))
        assert audit_response.path.endswith("_audit.zip")
        with zipfile.ZipFile(audit_response.path) as archive:
            names = set(archive.namelist())
        assert "items/001_sample_a/parameter_audit.json" in names
        assert "items/001_sample_a/agent_observation.json" in names
        assert "items/001_sample_a/agent_plan.json" in names
        assert "items/001_sample_a/agent_decision_trace.json" in names
        assert "items/001_sample_a/logs/runtime.log" in names
        with zipfile.ZipFile(excel_path) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "sample_a.raw" in sheet
        assert "PXDTEST" in sheet
        assert "Actual input file" in sheet
        assert "Workflow path" in sheet
    finally:
        web_app._batches.pop(batch_id, None)


def test_public_batch_record_localizes_english_log_tail_and_events(tmp_path):
    batch_dir = tmp_path / "batch"
    item_dir = batch_dir / "items" / "001_sample"
    (item_dir / "logs").mkdir(parents=True)
    (item_dir / "logs" / "runtime.log").write_text(
        "任务开始：sample.raw\n未找到匹配的 SDRF 行，且项目包含多个仪器；无法确定文件级仪器信息。\n",
        encoding="utf-8",
    )
    batch = {
        "batch_id": "batch_en",
        "status": "failed",
        "submitter": "Alice",
        "ui_language": "en",
        "output_dir": str(batch_dir),
        "items": [
            {
                "index": 1,
                "input": "sample.raw",
                "status": "failed",
                "output_dir": str(item_dir),
                "error": "任务运行失败。",
            }
        ],
        "events": [{"ts": "2026-05-13T00:00:00+08:00", "level": "error", "message": "任务运行失败。"}],
        "errors": ["任务运行失败。"],
    }

    public = web_app._public_batch_record(batch)
    visible_text = json.dumps(
        {
            "log_tail": public["items"][0]["log_tail"],
            "item_error": public["items"][0]["error"],
            "events": [event["message"] for event in public["events"]],
            "errors": public["errors"],
        },
        ensure_ascii=False,
    )

    assert "Task started" in visible_text
    assert "file-level instrument cannot be determined" in visible_text
    assert "Task execution failed." in visible_text
    assert not web_app._contains_cjk(visible_text)


def test_create_parameter_batch_normalizes_unsupported_repository_to_default(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setattr(web_app, "_start_parameter_batch_thread", lambda _batch_id: None)

    result = asyncio.run(
        web_app.create_parameter_batch(
            {
                "inputs": ["sample.raw"],
                "submitter": "Alice",
                "repository": "unknownrepo",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    batch_id = result.get("batch_id")
    try:
        assert result["repository"] == "pride"
        with web_app._batches_lock:
            batch = dict(web_app._batches[batch_id])
        assert batch["repository"] == "pride"
        manifest = json.loads((Path(batch["output_dir"]) / "batch_manifest.json").read_text(encoding="utf-8"))
        assert manifest["repository"] == "pride"
        assert "llm_config" not in manifest
    finally:
        if batch_id:
            with web_app._batches_lock:
                web_app._batches.pop(batch_id, None)


def test_clean_repository_accepts_iprox_aliases():
    assert web_app._clean_repository("iprox") == "iprox"
    assert web_app._clean_repository("iProX") == "iprox"
    assert web_app._clean_repository("IPX") == "iprox"


def test_create_batch_persists_run_mode_and_resource_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._run_llm_check", _llm_ok, raising=False)
    monkeypatch.setattr(web_app, "_start_parameter_batch_thread", lambda _batch_id: None)

    result = asyncio.run(
        web_app.create_parameter_batch(
            {
                "inputs": ["sample.raw"],
                "submitter": "Alice",
                "repository": "massive",
                "run_mode": "prepare",
                "resource_policy": "conservative",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    batch_id = result.get("batch_id")
    try:
        assert result["run_mode"] == "prepare"
        assert result["resource_policy"] == "conservative"
        with web_app._batches_lock:
            batch = dict(web_app._batches[batch_id])
        assert batch["run_mode"] == "prepare"
        assert batch["resource_policy"] == "conservative"
        manifest = json.loads((Path(batch["output_dir"]) / "batch_manifest.json").read_text(encoding="utf-8"))
        assert manifest["run_mode"] == "prepare"
        assert manifest["resource_policy"] == "conservative"
    finally:
        if batch_id:
            with web_app._batches_lock:
                web_app._batches.pop(batch_id, None)


def test_create_batch_downgrades_full_workflow_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._run_llm_check", _llm_ok, raising=False)
    monkeypatch.setattr(web_app, "_start_parameter_batch_thread", lambda _batch_id: None)
    monkeypatch.setenv("AGENT_WEB_FULL_WORKFLOW_ENABLED", "0")

    result = asyncio.run(
        web_app.create_parameter_batch(
            {
                "inputs": ["sample.raw"],
                "submitter": "Alice",
                "run_mode": "full",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    batch_id = result.get("batch_id")
    try:
        assert result["run_mode"] == "prepare"
        with web_app._batches_lock:
            batch = dict(web_app._batches[batch_id])
        assert batch["run_mode"] == "prepare"
    finally:
        if batch_id:
            with web_app._batches_lock:
                web_app._batches.pop(batch_id, None)


def test_preflight_endpoint_accepts_single_and_batch_inputs(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    captured: dict[str, object] = {}

    def fake_preflight(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "blocking_issues": [], "checks": []}

    monkeypatch.setattr(web_app, "run_preflight", fake_preflight)

    result = asyncio.run(
        web_app.preflight(
            {
                "input_value": "single.raw",
                "inputs": ["batch.raw"],
                "repository": "massive",
                "run_mode": "prepare",
                "resource_policy": "fast",
            }
        )
    )

    assert result["status"] == "ok"
    assert captured["inputs"] == ["batch.raw"]
    assert captured["run_mode"] == "prepare"
    assert captured["repository"] == "massive"
    assert captured["resource_policy"] == "fast"
    assert captured["output_root"] == tmp_path


def test_prepare_batch_item_generates_input_package_without_docker_run(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    docker_calls: list[str] = []

    def attr(value):
        return {"value": value, "confidence": 1.0, "source": "test", "evidence_excerpt": "", "conflict_flag": False}

    class FakeService:
        def __init__(self, **_kwargs):
            self.reporter = _kwargs.get("reporter")

        def prepare_repository_msdt_docker_input(self, task, output_dir, **_kwargs):
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            workflow = output_dir / "workflows" / "Default.workflow"
            workflow.parent.mkdir(parents=True, exist_ok=True)
            workflow.write_text("msfragger.search_enzyme_name_1=stricttrypsin\n", encoding="utf-8")
            fasta = output_dir / "fasta" / "human.fasta"
            fasta.parent.mkdir(parents=True, exist_ok=True)
            fasta.write_text(">P1\nPEPTIDE\n", encoding="utf-8")
            prepared = output_dir / "assets" / "prepared" / f"{Path(task.file_name).stem}.mzML"
            _write_minimal_dda_mzml(prepared)
            plan = SimpleNamespace(
                task_id=task.task_id,
                source_file_name=task.file_name,
                source_data_path=prepared,
                fragpipe_workflow_path=workflow,
                fasta_path=fasta,
                fasta_selection_mode="inferred",
                fasta_download_url="https://example.test/human.fasta",
                raw_data_type="mzml",
                thread_num=2,
                manifest_path=output_dir / "fragpipe" / "fragpipe-files.fp-manifest",
                expected_pin_path=output_dir / "fragpipe" / "exp" / f"{Path(task.file_name).stem}_edited.pin",
                output_paths={"fp_msdt": output_dir / "msdt" / f"{Path(task.file_name).stem}_fp_msdt.parquet"},
                rawspectrum_output_path=output_dir / "rawspectrum" / f"{Path(task.file_name).stem}_rawspectrum.parquet",
                needs_review=False,
                blocking_issues=[],
            )
            result = SimpleNamespace(
                resolution=SimpleNamespace(
                    primary_project=SimpleNamespace(
                        repository="massive",
                        project_accession="MSVTEST",
                        matched_file=task.file_name,
                        match_type="exact",
                        match_score=100,
                    ),
                    needs_review=False,
                    resolution_confidence=1.0,
                ),
                context=SimpleNamespace(repository="massive", metadata={}, project_files=[]),
                attributes=SimpleNamespace(
                    acquisition_mode=SimpleNamespace(**attr("DDA")),
                    species=SimpleNamespace(**attr("Homo sapiens")),
                    instrument_name=SimpleNamespace(**attr("Orbitrap")),
                    enzyme=SimpleNamespace(**attr("Trypsin")),
                    labeling_strategy=SimpleNamespace(**attr("label-free")),
                    fixed_mods=SimpleNamespace(**attr(["Carbamidomethyl C"])),
                    variable_mods=SimpleNamespace(**attr(["Oxidation M"])),
                    search_parameter_hints=SimpleNamespace(**attr({"recommended_workflow_name": "Default.workflow"})),
                ),
                asset=SimpleNamespace(
                    repository="massive",
                    original_file_name=task.file_name,
                    matched_project_file=task.file_name,
                    resolved_asset_type="raw",
                    download_url="ftp://massive/sample.raw",
                    download_urls=["ftp://massive/sample.raw"],
                    transfer_method="ftp",
                    expected_size_bytes=123,
                    requires_conversion=True,
                ),
                plan=plan,
            )
            (output_dir / "project_resolution.json").write_text(
                json.dumps({"primary_project": {"project_accession": "MSVTEST", "matched_file": task.file_name, "match_type": "exact", "match_score": 100}}),
                encoding="utf-8",
            )
            (output_dir / "metadata.json").write_text(json.dumps({"metadata": {}}), encoding="utf-8")
            (output_dir / "attributes.json").write_text(json.dumps({"species": attr("Homo sapiens")}), encoding="utf-8")
            (output_dir / "decision_trace.json").write_text(json.dumps({"source_file_name": task.file_name}), encoding="utf-8")
            (output_dir / "asset_resolution.json").write_text(json.dumps({"original_file_name": task.file_name}), encoding="utf-8")
            (output_dir / "converter_config.json").write_text(json.dumps({"input": str(prepared)}), encoding="utf-8")
            bundle = SimpleNamespace(plan=plan, converter_config_path=output_dir / "converter_config.json", materialized_workflow_path=workflow, materialized_fasta_path=fasta, task_root=output_dir)
            return bundle, result, prepared

    class FakeDockerRunner:
        def __init__(self, *_args, **_kwargs):
            docker_calls.append("init")

        def run(self, _bundle):
            docker_calls.append("run")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent.orchestrator.pipeline.AgentService", FakeService)
    monkeypatch.setattr("agent.msdt_converter.docker_runner.DockerMSDTConverterRunner", FakeDockerRunner)

    batch_id = "prepare_batch"
    item_dir = tmp_path / "_batches" / batch_id / "items" / "001_sample"
    batch = {
        "batch_id": batch_id,
        "status": "running",
        "created_at": web_app._now_iso(),
        "updated_at": web_app._now_iso(),
        "repository": "massive",
        "run_mode": "prepare",
        "resource_policy": "balanced",
        "output_dir": str(tmp_path / "_batches" / batch_id),
        "excel_path": str(tmp_path / "_batches" / batch_id / "benchmark_results.xlsx"),
        "items": [{"index": 1, "input": "sample.raw", "status": "queued", "output_dir": str(item_dir)}],
        "llm_config": {"api_key": "sk-test", "base_url": "https://api.example.com", "model": "m1", "timeout": "120"},
    }
    with web_app._batches_lock:
        web_app._batches[batch_id] = batch
    try:
        result = web_app._run_parameter_batch_item(batch_id, 0)
        detail = asyncio.run(web_app.get_parameter_batch(batch_id))

        assert result["status"] == "completed"
        assert docker_calls == []
        assert detail["run_mode"] == "prepare"
        assert detail["items"][0]["status"] == "completed"
        assert (item_dir / "converter_config.json").exists()
        assert (item_dir / "parameter_audit.json").exists()
        assert (item_dir / "agent_observation.json").exists()
        assert (item_dir / "agent_plan.json").exists()
        assert (item_dir / "agent_decision_trace.json").exists()
    finally:
        with web_app._batches_lock:
            web_app._batches.pop(batch_id, None)


def test_full_batch_item_packages_agent_audit_files(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.delenv("AGENT_DISABLE_FULL_WORKFLOW", raising=False)
    monkeypatch.setenv("AGENT_WEB_FULL_WORKFLOW_ENABLED", "true")
    docker_calls: list[str] = []

    def attr(value):
        return {"value": value, "confidence": 1.0, "source": "test", "evidence_excerpt": "", "conflict_flag": False}

    class FakeService:
        def __init__(self, **_kwargs):
            self.reporter = _kwargs.get("reporter")

        def prepare_repository_msdt_docker_input(self, task, output_dir, **_kwargs):
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            workflow = output_dir / "workflows" / "Default.workflow"
            workflow.parent.mkdir(parents=True, exist_ok=True)
            workflow.write_text("msfragger.search_enzyme_name_1=stricttrypsin\n", encoding="utf-8")
            fasta = output_dir / "fasta" / "human.fasta"
            fasta.parent.mkdir(parents=True, exist_ok=True)
            fasta.write_text(">P1\nPEPTIDE\n", encoding="utf-8")
            prepared = output_dir / "assets" / "prepared" / f"{Path(task.file_name).stem}.mzML"
            _write_minimal_dda_mzml(prepared)
            plan = SimpleNamespace(
                task_id=task.task_id,
                source_file_name=task.file_name,
                source_data_path=prepared,
                fragpipe_workflow_path=workflow,
                fasta_path=fasta,
                fasta_selection_mode="inferred",
                fasta_download_url="https://example.test/human.fasta",
                raw_data_type="mzml",
                thread_num=2,
                manifest_path=output_dir / "fragpipe" / "fragpipe-files.fp-manifest",
                expected_pin_path=output_dir / "fragpipe" / "exp" / f"{Path(task.file_name).stem}_edited.pin",
                output_paths={"fp_msdt": output_dir / "msdt" / f"{Path(task.file_name).stem}_fp_msdt.parquet"},
                rawspectrum_output_path=output_dir / "rawspectrum" / f"{Path(task.file_name).stem}_rawspectrum.parquet",
                needs_review=False,
                blocking_issues=[],
            )
            result = SimpleNamespace(
                resolution=SimpleNamespace(
                    primary_project=SimpleNamespace(
                        repository="massive",
                        project_accession="MSVTEST",
                        matched_file=task.file_name,
                        match_type="exact",
                        match_score=100,
                    ),
                    needs_review=False,
                    resolution_confidence=1.0,
                ),
                context=SimpleNamespace(repository="massive", metadata={}, project_files=[]),
                attributes=SimpleNamespace(
                    acquisition_mode=SimpleNamespace(**attr("DDA")),
                    species=SimpleNamespace(**attr("Homo sapiens")),
                    instrument_name=SimpleNamespace(**attr("Orbitrap")),
                    enzyme=SimpleNamespace(**attr("Trypsin")),
                    labeling_strategy=SimpleNamespace(**attr("label-free")),
                    fixed_mods=SimpleNamespace(**attr(["Carbamidomethyl C"])),
                    variable_mods=SimpleNamespace(**attr(["Oxidation M"])),
                    search_parameter_hints=SimpleNamespace(**attr({"recommended_workflow_name": "Default.workflow"})),
                ),
                asset=SimpleNamespace(
                    repository="massive",
                    original_file_name=task.file_name,
                    matched_project_file=task.file_name,
                    resolved_asset_type="raw",
                    download_url="ftp://massive/sample.raw",
                    download_urls=["ftp://massive/sample.raw"],
                    transfer_method="ftp",
                    expected_size_bytes=123,
                    requires_conversion=True,
                ),
                plan=plan,
            )
            (output_dir / "project_resolution.json").write_text(
                json.dumps({"primary_project": {"project_accession": "MSVTEST", "matched_file": task.file_name, "match_type": "exact", "match_score": 100}}),
                encoding="utf-8",
            )
            (output_dir / "metadata.json").write_text(json.dumps({"metadata": {}}), encoding="utf-8")
            (output_dir / "attributes.json").write_text(json.dumps({"species": attr("Homo sapiens")}), encoding="utf-8")
            (output_dir / "decision_trace.json").write_text(json.dumps({"source_file_name": task.file_name}), encoding="utf-8")
            (output_dir / "asset_resolution.json").write_text(json.dumps({"original_file_name": task.file_name}), encoding="utf-8")
            (output_dir / "converter_config.json").write_text(json.dumps({"input": str(prepared)}), encoding="utf-8")
            bundle = SimpleNamespace(plan=plan, converter_config_path=output_dir / "converter_config.json", materialized_workflow_path=workflow, materialized_fasta_path=fasta, task_root=output_dir)
            return bundle, result, prepared

    class FakeDockerRunner:
        def __init__(self, *_args, **_kwargs):
            docker_calls.append("init")

        def run(self, bundle):
            docker_calls.append("run")
            plan = bundle.plan
            plan.rawspectrum_output_path.parent.mkdir(parents=True, exist_ok=True)
            plan.rawspectrum_output_path.write_text("rawspectrum", encoding="utf-8")
            plan.expected_pin_path.parent.mkdir(parents=True, exist_ok=True)
            plan.expected_pin_path.write_text("pin", encoding="utf-8")
            plan.output_paths["fp_msdt"].parent.mkdir(parents=True, exist_ok=True)
            plan.output_paths["fp_msdt"].write_text("msdt", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent.orchestrator.pipeline.AgentService", FakeService)
    monkeypatch.setattr("agent.msdt_converter.docker_runner.DockerMSDTConverterRunner", FakeDockerRunner)

    batch_id = "full_batch"
    item_dir = tmp_path / "_batches" / batch_id / "items" / "001_sample"
    batch = {
        "batch_id": batch_id,
        "status": "running",
        "created_at": web_app._now_iso(),
        "updated_at": web_app._now_iso(),
        "repository": "massive",
        "run_mode": "full",
        "resource_policy": "balanced",
        "output_dir": str(tmp_path / "_batches" / batch_id),
        "excel_path": str(tmp_path / "_batches" / batch_id / "benchmark_results.xlsx"),
        "items": [{"index": 1, "input": "sample.raw", "status": "queued", "output_dir": str(item_dir)}],
        "llm_config": {"api_key": "sk-test", "base_url": "https://api.example.com", "model": "m1", "timeout": "120"},
    }
    with web_app._batches_lock:
        web_app._batches[batch_id] = batch
    try:
        result = web_app._run_parameter_batch_item(batch_id, 0)

        assert result["status"] == "completed"
        assert docker_calls == ["init", "run"]
        assert (item_dir / "agent_observation.json").exists()
        assert (item_dir / "agent_plan.json").exists()
        assert (item_dir / "agent_decision_trace.json").exists()
        manifest = json.loads((item_dir / "msdt_input_manifest.json").read_text(encoding="utf-8"))
        assert "agent_observation.json" in manifest["audit_files"]
        assert "agent_plan.json" in manifest["audit_files"]
        assert "agent_decision_trace.json" in manifest["audit_files"]
        zip_path = item_dir / ".download_cache" / "results-compressed.zip"
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
        assert "agent_observation.json" in names
        assert "agent_plan.json" in names
        assert "agent_decision_trace.json" in names
    finally:
        with web_app._batches_lock:
            web_app._batches.pop(batch_id, None)


def test_full_batch_item_failure_writes_recovery_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.delenv("AGENT_DISABLE_FULL_WORKFLOW", raising=False)
    monkeypatch.setenv("AGENT_WEB_FULL_WORKFLOW_ENABLED", "true")

    def attr(value):
        return {"value": value, "confidence": 1.0, "source": "test", "evidence_excerpt": "", "conflict_flag": False}

    class FakeService:
        def __init__(self, **_kwargs):
            self.reporter = _kwargs.get("reporter")

        def prepare_repository_msdt_docker_input(self, task, output_dir, **_kwargs):
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            workflow = output_dir / "workflows" / "Default.workflow"
            workflow.parent.mkdir(parents=True, exist_ok=True)
            workflow.write_text("msfragger.search_enzyme_name_1=stricttrypsin\n", encoding="utf-8")
            fasta = output_dir / "fasta" / "human.fasta"
            fasta.parent.mkdir(parents=True, exist_ok=True)
            fasta.write_text(">P1\nPEPTIDE\n", encoding="utf-8")
            prepared = output_dir / "assets" / "prepared" / f"{Path(task.file_name).stem}.mzML"
            _write_minimal_dda_mzml(prepared)
            plan = SimpleNamespace(
                task_id=task.task_id,
                source_file_name=task.file_name,
                source_data_path=prepared,
                fragpipe_workflow_path=workflow,
                fasta_path=fasta,
                fasta_selection_mode="inferred",
                fasta_download_url="https://example.test/human.fasta",
                raw_data_type="mzml",
                thread_num=2,
                manifest_path=output_dir / "fragpipe" / "fragpipe-files.fp-manifest",
                expected_pin_path=output_dir / "fragpipe" / "exp" / f"{Path(task.file_name).stem}_edited.pin",
                output_paths={"fp_msdt": output_dir / "msdt" / f"{Path(task.file_name).stem}_fp_msdt.parquet"},
                rawspectrum_output_path=output_dir / "rawspectrum" / f"{Path(task.file_name).stem}_rawspectrum.parquet",
                needs_review=False,
                blocking_issues=[],
            )
            result = SimpleNamespace(
                resolution=SimpleNamespace(
                    primary_project=SimpleNamespace(
                        repository="massive",
                        project_accession="MSVTEST",
                        matched_file=task.file_name,
                        match_type="exact",
                        match_score=100,
                    ),
                    needs_review=False,
                    resolution_confidence=1.0,
                ),
                context=SimpleNamespace(repository="massive", metadata={}, project_files=[]),
                attributes=SimpleNamespace(
                    acquisition_mode=SimpleNamespace(**attr("DDA")),
                    species=SimpleNamespace(**attr("Homo sapiens")),
                    instrument_name=SimpleNamespace(**attr("Orbitrap")),
                    enzyme=SimpleNamespace(**attr("Trypsin")),
                    labeling_strategy=SimpleNamespace(**attr("label-free")),
                    fixed_mods=SimpleNamespace(**attr(["Carbamidomethyl C"])),
                    variable_mods=SimpleNamespace(**attr(["Oxidation M"])),
                    search_parameter_hints=SimpleNamespace(**attr({"recommended_workflow_name": "Default.workflow"})),
                ),
                asset=SimpleNamespace(
                    repository="massive",
                    original_file_name=task.file_name,
                    matched_project_file=task.file_name,
                    resolved_asset_type="raw",
                    download_url="ftp://massive/sample.raw",
                    download_urls=["ftp://massive/sample.raw"],
                    transfer_method="ftp",
                    expected_size_bytes=123,
                    requires_conversion=True,
                ),
                plan=plan,
            )
            (output_dir / "project_resolution.json").write_text(
                json.dumps({"primary_project": {"project_accession": "MSVTEST", "matched_file": task.file_name, "match_type": "exact", "match_score": 100}}),
                encoding="utf-8",
            )
            (output_dir / "metadata.json").write_text(json.dumps({"metadata": {}}), encoding="utf-8")
            (output_dir / "attributes.json").write_text(json.dumps({"species": attr("Homo sapiens")}), encoding="utf-8")
            (output_dir / "decision_trace.json").write_text(json.dumps({"source_file_name": task.file_name}), encoding="utf-8")
            (output_dir / "asset_resolution.json").write_text(json.dumps({"original_file_name": task.file_name}), encoding="utf-8")
            (output_dir / "converter_config.json").write_text(json.dumps({"input": str(prepared)}), encoding="utf-8")
            bundle = SimpleNamespace(plan=plan, converter_config_path=output_dir / "converter_config.json", materialized_workflow_path=workflow, materialized_fasta_path=fasta, task_root=output_dir)
            return bundle, result, prepared

    class FakeDockerRunner:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, bundle):
            plan = bundle.plan
            plan.rawspectrum_output_path.parent.mkdir(parents=True, exist_ok=True)
            plan.rawspectrum_output_path.write_text("rawspectrum", encoding="utf-8")
            plan.expected_pin_path.parent.mkdir(parents=True, exist_ok=True)
            plan.expected_pin_path.write_text("pin", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="generate msdt fail", stderr="")

    monkeypatch.setattr("agent.orchestrator.pipeline.AgentService", FakeService)
    monkeypatch.setattr("agent.msdt_converter.docker_runner.DockerMSDTConverterRunner", FakeDockerRunner)

    batch_id = "full_batch_failure"
    item_dir = tmp_path / "_batches" / batch_id / "items" / "001_sample"
    batch = {
        "batch_id": batch_id,
        "status": "running",
        "created_at": web_app._now_iso(),
        "updated_at": web_app._now_iso(),
        "repository": "massive",
        "run_mode": "full",
        "resource_policy": "balanced",
        "output_dir": str(tmp_path / "_batches" / batch_id),
        "excel_path": str(tmp_path / "_batches" / batch_id / "benchmark_results.xlsx"),
        "items": [{"index": 1, "input": "sample.raw", "status": "queued", "output_dir": str(item_dir)}],
        "llm_config": {"api_key": "sk-test", "base_url": "https://api.example.com", "model": "m1", "timeout": "120"},
    }
    with web_app._batches_lock:
        web_app._batches[batch_id] = batch
    try:
        result = web_app._run_parameter_batch_item(batch_id, 0)

        assert result["status"] == "failed"
        recovery = json.loads((item_dir / "recovery_audit.json").read_text(encoding="utf-8"))
        assert recovery["failure"]["category"] == "missing_msdt_output"
        assert recovery["task"]["run_mode"] == "full"
        assert recovery["recovery"]["decision"] == "manual_required"
        assert result["workflow_outcome"] == "failed_with_usable_partial_outputs"
        assert result["usable_partial_outputs"] is True
        assert result["recovery_primary_issue"] == "partial_outputs_available"
        report = json.loads((item_dir / "agent_recovery_report.json").read_text(encoding="utf-8"))
        assert report["workflow_outcome"] == "failed_with_usable_partial_outputs"
        detail = asyncio.run(web_app.get_parameter_batch(batch_id))
        assert detail["items"][0]["workflow_outcome"] == "failed_with_usable_partial_outputs"
        assert detail["items"][0]["usable_partial_outputs"] is True
    finally:
        with web_app._batches_lock:
            web_app._batches.pop(batch_id, None)


def test_parameter_batch_uses_mzml_probe_for_unresolved_instrument_and_cleans_large_files(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    planner_calls = []

    def attr(value, source="test", confidence=1.0, conflict_flag=False):
        return {"value": value, "confidence": confidence, "source": source, "evidence_excerpt": "", "conflict_flag": conflict_flag}

    class FakeService:
        def __init__(self, **_kwargs):
            self.reporter = _kwargs.get("reporter")

        def plan_dda_run_from_repository(self, task, output_dir, **_kwargs):
            planner_calls.append(_kwargs.get("repository"))
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            workflow = output_dir / "template.workflow"
            workflow.write_text(
                "msfragger.search_enzyme_name_1=stricttrypsin\nmsfragger.search_enzyme_cut_1=KR\n",
                encoding="utf-8",
            )
            asset = SimpleNamespace(
                original_file_name=task.file_name,
                matched_project_file=task.file_name,
                resolved_asset_type="raw",
                download_url="https://example.test/raw",
                expected_size_bytes=123,
                requires_conversion=True,
                local_path=output_dir / "assets" / "downloads" / task.file_name,
                prepared_path=output_dir / "assets" / "prepared" / f"{Path(task.file_name).stem}.mzML",
            )
            return SimpleNamespace(
                resolution=SimpleNamespace(
                    primary_project=SimpleNamespace(
                        project_accession="PXDTEST",
                        matched_file=task.file_name,
                        match_type="exact",
                        match_score=100,
                    ),
                    needs_review=False,
                    resolution_confidence=1.0,
                ),
                context=SimpleNamespace(metadata={"instruments": {"value": ["Q Exactive", "Orbitrap Fusion"]}}, project_files=[]),
                attributes=SimpleNamespace(
                    acquisition_mode=SimpleNamespace(**attr("DDA")),
                    species=SimpleNamespace(**attr("Homo sapiens")),
                    instrument_name=SimpleNamespace(**attr("Q Exactive; Orbitrap Fusion", "pride.instruments", 0.5, True)),
                    instrument_family=SimpleNamespace(**attr("unknown", "pride.instruments", 0.4, True)),
                    enzyme=SimpleNamespace(**attr("Trypsin")),
                    labeling_strategy=SimpleNamespace(**attr("label-free")),
                    fixed_mods=SimpleNamespace(**attr(["C[57.02]"])),
                    variable_mods=SimpleNamespace(**attr(["M[15.99]"])),
                    search_parameter_hints=SimpleNamespace(
                        **attr({"recommended_workflow_name": "Default.workflow", "precursor_tol": "20ppm", "fragment_tol": "20ppm"})
                    ),
                ),
                plan=SimpleNamespace(
                    task_id=task.task_id,
                    source_file_name=task.file_name,
                    source_data_path=asset.prepared_path,
                    fragpipe_workflow_path=workflow,
                    fasta_path=output_dir / "fasta" / "human.fasta",
                    fasta_selection_mode="inferred",
                    fasta_download_url="https://example.test/human.fasta",
                    raw_data_type="mzml",
                    thread_num=2,
                    manifest_path=output_dir / "fragpipe" / "fragpipe-files.fp-manifest",
                    expected_pin_path=output_dir / "fragpipe" / "exp" / f"{Path(task.file_name).stem}_edited.pin",
                    output_paths={"fp_msdt": output_dir / "msdt" / f"{Path(task.file_name).stem}_fp_msdt.parquet"},
                    rawspectrum_output_path=output_dir / "rawspectrum" / f"{Path(task.file_name).stem}_rawspectrum.parquet",
                    needs_review=True,
                    blocking_issues=["未找到匹配的 SDRF 行，且项目包含多个仪器；无法确定文件级仪器信息。"],
                ),
                asset=asset,
            )

        def _can_retry_with_mzml_instrument(self, plan):
            return bool(plan.needs_review)

        def prepare_asset(self, asset):
            asset.local_path.parent.mkdir(parents=True, exist_ok=True)
            asset.prepared_path.parent.mkdir(parents=True, exist_ok=True)
            asset.local_path.write_bytes(b"raw")
            _write_minimal_dda_mzml(asset.prepared_path)
            return asset.prepared_path

        def replan_with_mzml_instrument(self, result, prepared_path, task, output_dir, **_kwargs):
            attrs = result.attributes
            attrs.instrument_name = SimpleNamespace(**attr("Q Exactive", "mzml"))
            attrs.instrument_family = SimpleNamespace(**attr("orbitrap", "mzml"))
            result.plan.needs_review = False
            result.plan.blocking_issues = []
            result.plan.source_data_path = Path(prepared_path)
            return result

        def write_task_bundle(self, output_dir, resolution, context, attributes, plan, **_kwargs):
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "project_resolution.json").write_text(
                json.dumps({"primary_project": {"project_accession": "PXDTEST", "matched_file": plan.source_file_name}, "needs_review": False}),
                encoding="utf-8",
            )
            (output_dir / "metadata.json").write_text(json.dumps({"metadata": context.metadata}), encoding="utf-8")
            (output_dir / "attributes.json").write_text(json.dumps({"instrument_name": attributes.instrument_name.__dict__}), encoding="utf-8")
            (output_dir / "decision_trace.json").write_text(json.dumps({"needs_review": plan.needs_review}), encoding="utf-8")
            (output_dir / "asset_resolution.json").write_text(json.dumps({"original_file_name": plan.source_file_name}), encoding="utf-8")
            (output_dir / "converter_config.json").write_text(
                json.dumps({"generate_fragpipe_search_result": {"workflow_path": str(plan.fragpipe_workflow_path)}}),
                encoding="utf-8",
            )

    monkeypatch.setattr("agent.orchestrator.pipeline.AgentService", FakeService)
    batch_id = "probe_batch"
    output_dir = tmp_path / "_batches" / batch_id / "items" / "001_probe"
    batch = {
        "batch_id": batch_id,
        "status": "running",
        "created_at": web_app._now_iso(),
        "updated_at": web_app._now_iso(),
        "repository": "massive",
        "output_dir": str(tmp_path / "_batches" / batch_id),
        "excel_path": str(tmp_path / "_batches" / batch_id / "benchmark_results.xlsx"),
        "items": [{"index": 1, "input": "probe.raw", "status": "queued", "output_dir": str(output_dir)}],
        "llm_config": {"api_key": "sk-test", "base_url": "https://api.example.com", "model": "m1", "timeout": "120"},
    }
    with web_app._batches_lock:
        web_app._batches[batch_id] = batch
    try:
        result = web_app._run_parameter_batch_item(batch_id, 0)
        detail = asyncio.run(web_app.get_parameter_batch(batch_id))
        assert result["status"] == "completed"
        assert planner_calls == ["massive"]
        assert detail["repository"] == "massive"
        assert detail["items"][0]["status"] == "completed"
        assert (output_dir / "parameter_audit.json").exists()
        assert not (output_dir / "assets" / "downloads" / "probe.raw").exists()
        assert not (output_dir / "assets" / "prepared" / "probe.mzML").exists()
    finally:
        with web_app._batches_lock:
            web_app._batches.pop(batch_id, None)


def test_project_history_lists_parameter_batches_from_memory_and_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    with web_app._batches_lock:
        previous_batches = dict(web_app._batches)
        web_app._batches.clear()
    try:
        active_batch = {
            "batch_id": "activebatch",
            "status": "running",
            "submitter": "Alice",
            "created_at": "2026-05-09T12:00:00+08:00",
            "started_at": "2026-05-09T12:01:00+08:00",
            "updated_at": "2026-05-09T12:02:00+08:00",
            "jobs": 2,
            "ui_language": "en",
            "output_dir": str(tmp_path / "_batches" / "activebatch"),
            "excel_path": str(tmp_path / "_batches" / "activebatch" / "benchmark_results.xlsx"),
            "items": [
                {"index": 1, "input": "a.raw", "status": "running", "output_dir": str(tmp_path / "_batches" / "activebatch" / "items" / "001_a")},
                {"index": 2, "input": "b.raw", "status": "queued", "output_dir": str(tmp_path / "_batches" / "activebatch" / "items" / "002_b")},
            ],
            "errors": [],
        }
        completed_batch = {
            "batch_id": "donebatch",
            "status": "completed",
            "submitter": "Bob",
            "created_at": "2026-05-09T11:00:00+08:00",
            "started_at": "2026-05-09T11:01:00+08:00",
            "finished_at": "2026-05-09T11:10:00+08:00",
            "updated_at": "2026-05-09T11:10:00+08:00",
            "jobs": 1,
            "ui_language": "en",
            "output_dir": str(tmp_path / "_batches" / "donebatch"),
            "excel_path": str(tmp_path / "_batches" / "donebatch" / "benchmark_results.xlsx"),
            "items": [{"index": 1, "input": "done.raw", "status": "completed", "output_dir": str(tmp_path / "_batches" / "donebatch" / "items" / "001_done")}],
            "errors": [],
        }
        with web_app._batches_lock:
            web_app._batches["activebatch"] = active_batch
            web_app._write_batch_manifest(active_batch)
        done_dir = tmp_path / "_batches" / "donebatch"
        done_dir.mkdir(parents=True)
        (done_dir / "benchmark_results.xlsx").write_bytes(b"xlsx")
        web_app._write_batch_manifest(completed_batch)

        history = asyncio.run(list_project_history())
    finally:
        with web_app._batches_lock:
            web_app._batches.clear()
            web_app._batches.update(previous_batches)

    active = {item["batch_id"]: item for item in history["active_tasks"] if item.get("kind") == "batch"}
    results = {item["batch_id"]: item for item in history["results"] if item.get("kind") == "batch"}

    assert active["activebatch"]["display_name"] == "Batch Excel report"
    assert active["activebatch"]["task_id"] == "batch-activebatch"
    assert active["activebatch"]["primary_action"] == "watch"
    assert active["activebatch"]["item_count"] == 2
    assert active["activebatch"]["run_mode"] == "parameters"
    assert results["donebatch"]["display_name"] == "Batch Excel report"
    assert results["donebatch"]["primary_action"] == "download"
    assert results["donebatch"]["can_download"] is True
    assert results["donebatch"]["file_count"] >= 1
    assert history["summary"]["total"] == 2
    assert history["summary"]["downloadable"] == 1


def test_cleanup_results_does_not_remove_batch_history_root(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setenv("AGENT_RESULT_RETENTION_SECONDS", "1800")
    batch_root = tmp_path / "_batches"
    batch_dir = batch_root / "oldbatch"
    batch_dir.mkdir(parents=True)
    (batch_dir / "benchmark_results.xlsx").write_bytes(b"xlsx")
    (batch_dir / "batch_manifest.json").write_text(
        json.dumps(
            {
                "batch_id": "oldbatch",
                "status": "completed",
                "submitter": "Alice",
                "created_at": "2026-05-09T11:00:00+08:00",
                "finished_at": "2026-05-09T11:10:00+08:00",
                "output_dir": str(batch_dir),
                "excel_path": str(batch_dir / "benchmark_results.xlsx"),
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    old_stamp = time.time() - 7200
    for path in batch_root.rglob("*"):
        os.utime(path, (old_stamp, old_stamp))
    os.utime(batch_root, (old_stamp, old_stamp))

    removed = _cleanup_expired_results()

    assert "_batches" not in removed
    assert batch_root.exists()
    assert batch_dir.exists()


def test_project_history_recovers_from_corrupt_index_by_scanning_run_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    (tmp_path / "project_history.json").write_text("{not valid json", encoding="utf-8")
    run_dir = tmp_path / "recoverable"
    run_dir.mkdir()
    (run_dir / "result.txt").write_text("ok", encoding="utf-8")
    (run_dir / "task_history.json").write_text(
        json.dumps(
            {
                "task_id": "recoverable-task",
                "input_value": "recoverable.raw",
                "submitter": "Alice",
                "status": "completed",
                "output_dir": "recoverable",
                "started_at": "2026-05-09T12:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )

    history = asyncio.run(list_project_history())

    assert history["results"][0]["task_id"] == "recoverable-task"
    repaired_index = json.loads((tmp_path / "project_history.json").read_text(encoding="utf-8"))
    assert repaired_index[0]["task_id"] == "recoverable-task"


def test_project_history_ignores_legacy_batches_pseudo_record(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    (tmp_path / "project_history.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "_batches",
                    "input_value": "_batches",
                    "status": "completed",
                    "output_dir": "_batches",
                    "project_key": "batches",
                    "history_id": "batches",
                },
                {
                    "task_id": "real",
                    "input_value": "real.raw",
                    "status": "completed",
                    "output_dir": "real",
                    "started_at": "2026-05-09T12:00:00+08:00",
                },
            ]
        ),
        encoding="utf-8",
    )

    history = asyncio.run(list_project_history())

    assert [item["task_id"] for item in history["results"]] == ["real"]


def test_project_history_marks_disk_only_running_batch_as_interrupted(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    with web_app._batches_lock:
        previous_batches = dict(web_app._batches)
        web_app._batches.clear()
    batch_dir = tmp_path / "_batches" / "orphanbatch"
    batch_dir.mkdir(parents=True)
    (batch_dir / "batch_manifest.json").write_text(
        json.dumps(
            {
                "batch_id": "orphanbatch",
                "status": "running",
                "submitter": "Alice",
                "created_at": "2026-05-09T12:00:00+08:00",
                "started_at": "2026-05-09T12:01:00+08:00",
                "updated_at": "2026-05-09T12:02:00+08:00",
                "output_dir": str(batch_dir),
                "excel_path": str(batch_dir / "benchmark_results.xlsx"),
                "items": [{"index": 1, "input": "a.raw", "status": "running", "output_dir": str(batch_dir / "items" / "001_a")}],
            }
        ),
        encoding="utf-8",
    )

    try:
        history = asyncio.run(list_project_history())
    finally:
        with web_app._batches_lock:
            web_app._batches.clear()
            web_app._batches.update(previous_batches)

    assert history["active_tasks"] == []
    assert history["results"][0]["batch_id"] == "orphanbatch"
    assert history["results"][0]["status"] == "failed"
    assert history["results"][0]["interrupted"] is True


def test_parameter_only_mode_stops_after_planning_without_full_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "_start_ready_queued_tasks", lambda: [])

    calls = []
    output_dir = tmp_path / "sample"
    workflow_path = tmp_path / "Default.workflow"
    workflow_path.write_text(
        "msfragger.search_enzyme_name_1=stricttrypsin\n"
        "msfragger.search_enzyme_cut_1=KR\n"
        "msfragger.allowed_missed_cleavage_1=2\n",
        encoding="utf-8",
    )
    result = SimpleNamespace(
        resolution=SimpleNamespace(
            primary_project=SimpleNamespace(project_accession="PXDTEST", matched_file="sample.raw"),
            resolution_confidence=1.0,
        ),
        context=SimpleNamespace(metadata={}, project_files=[]),
        attributes=SimpleNamespace(
            acquisition_mode=_value("DDA"),
            species=_value("Homo sapiens"),
            instrument_name=_value("Orbitrap Fusion"),
            enzyme=_value("Trypsin"),
            fixed_mods=_value(["C[57.02]"]),
            variable_mods=_value(["M[15.99]"]),
            search_parameter_hints=_value(
                {
                    "recommended_workflow_name": "Default.workflow",
                    "recommended_fasta_name": "uniprot_human_UP000005640.fasta",
                    "precursor_tol": "20ppm",
                    "fragment_tol": "0.02Da",
                }
            ),
        ),
        plan=SimpleNamespace(
            task_id="sample",
            source_file_name="sample.raw",
            source_data_path=output_dir / "assets" / "prepared" / "sample.mzML",
            fragpipe_workflow_path=workflow_path,
            manifest_path=output_dir / "fragpipe" / "fragpipe-files.fp-manifest",
            fasta_path=tmp_path / "uniprot_human_UP000005640.fasta",
            fasta_selection_mode="inferred",
            fasta_download_url="https://example.test/human.fasta",
            raw_data_type="mzml",
            converter_config_path=output_dir / "converter_config.json",
            rawspectrum_output_path=output_dir / "rawspectrum" / "sample_rawspectrum.parquet",
            expected_pin_path=output_dir / "fragpipe" / "exp" / "sample_edited.pin",
            output_paths={"fp_msdt": output_dir / "msdt" / "sample_fp_msdt.parquet"},
            thread_num=2,
            needs_review=False,
            blocking_issues=[],
        ),
        asset=SimpleNamespace(
            original_file_name="sample.raw",
            matched_project_file="sample.raw",
            download_url="https://example.test/sample.raw",
            resolved_asset_type="raw",
            requires_conversion=True,
            expected_size_bytes=123456,
        ),
    )

    class FakeService:
        def __init__(self, **_kwargs):
            pass

        def plan_dda_run_from_repository(self, **_kwargs):
            calls.append("plan")
            return result

        def _can_retry_with_mzml_instrument(self, _plan):
            return False

        def prepare_asset(self, _asset):
            raise AssertionError("parameter-only mode must not prepare raw data")

        def write_task_bundle(self, output_dir_arg, *_args, **_kwargs):
            calls.append("write_task_bundle")
            output = Path(output_dir_arg)
            output.mkdir(parents=True, exist_ok=True)
            (output / "metadata.json").write_text("{}", encoding="utf-8")
            (output / "project_resolution.json").write_text(
                json.dumps(
                    {
                        "primary_project": {
                            "project_accession": "PXDTEST",
                            "matched_file": "sample.raw",
                            "match_type": "exact",
                            "match_score": 100,
                        },
                        "needs_review": False,
                    }
                ),
                encoding="utf-8",
            )
            (output / "attributes.json").write_text(
                json.dumps(
                    {
                        "acquisition_mode": _value("DDA").__dict__,
                        "species": _value("Homo sapiens").__dict__,
                        "instrument_name": _value("Orbitrap Fusion").__dict__,
                        "enzyme": _value("Trypsin").__dict__,
                        "fixed_mods": _value(["C[57.02]"]).__dict__,
                        "variable_mods": _value(["M[15.99]"]).__dict__,
                        "search_parameter_hints": _value(
                            {
                                "recommended_workflow_name": "Default.workflow",
                                "recommended_fasta_name": "uniprot_human_UP000005640.fasta",
                                "precursor_tol": "20ppm",
                                "fragment_tol": "0.02Da",
                            }
                        ).__dict__,
                    }
                ),
                encoding="utf-8",
            )
            (output / "asset_resolution.json").write_text(
                json.dumps(
                    {
                        "original_file_name": "sample.raw",
                        "matched_project_file": "sample.raw",
                        "download_url": "https://example.test/sample.raw",
                        "resolved_asset_type": "raw",
                        "requires_conversion": True,
                    }
                ),
                encoding="utf-8",
            )
            (output / "decision_trace.json").write_text(
                json.dumps(
                    {
                        "source_file_name": "sample.raw",
                        "source_data_path": str(output / "assets" / "prepared" / "sample.mzML"),
                        "raw_data_type": "mzml",
                        "fasta_path": str(tmp_path / "uniprot_human_UP000005640.fasta"),
                        "fasta_download_url": "https://example.test/human.fasta",
                        "fasta_selection_mode": "inferred",
                        "fragpipe_workflow_path": str(workflow_path),
                        "converter_config_path": str(output / "converter_config.json"),
                        "manifest_path": str(output / "fragpipe" / "fragpipe-files.fp-manifest"),
                        "expected_pin_path": str(output / "fragpipe" / "exp" / "sample_edited.pin"),
                        "output_paths": {"fp_msdt": str(output / "msdt" / "sample_fp_msdt.parquet")},
                        "thread_num": 2,
                        "needs_review": False,
                    }
                ),
                encoding="utf-8",
            )
            (output / "converter_config.json").write_text(
                json.dumps(
                    {
                        "generate_fragpipe_search_result": {
                            "workflow_path": str(workflow_path),
                            "data_path": str(output / "assets" / "prepared" / "sample.mzML"),
                            "fasta_path": str(tmp_path / "uniprot_human_UP000005640.fasta"),
                        }
                    }
                ),
                encoding="utf-8",
            )

    monkeypatch.setattr("agent.orchestrator.pipeline.AgentService", FakeService)

    task_id = "parameter-only"
    _tasks[task_id] = {
        "task_id": task_id,
        "input_value": "sample.raw",
        "project_key": "sample",
        "submitter": "Alice",
        "output_dir": str(output_dir),
        "status": "running",
        "created_at": "2026-05-09T00:00:00+00:00",
        "started_at": "2026-05-09T00:00:00+00:00",
        "logs": deque(maxlen=100),
        "step": 0,
        "total_steps": 5,
        "blocking_issues": [],
        "prefer_project_fasta": False,
        "run_mode": "parameters",
        "ui_language": "en",
        "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1", "timeout": "1200"},
    }

    try:
        web_app._run_pipeline(task_id)

        assert calls == ["plan", "write_task_bundle"]
        assert _tasks[task_id]["status"] == "completed"
        assert _tasks[task_id]["step"] == 5
        detail = asyncio.run(get_task(task_id))
        assert detail["can_download"] is True
        state = json.loads((output_dir / "task_state.json").read_text(encoding="utf-8"))
        assert state["status"] == "completed"
        assert state["stage"] == "planning"
        zip_path = output_dir / ".download_cache" / "results-compressed.zip"
        assert zip_path.exists()
        assert (output_dir / "parameter_audit.json").exists()
        assert (output_dir / "msdt_input_manifest.json").exists()
        assert (output_dir / "agent_observation.json").exists()
        assert (output_dir / "agent_plan.json").exists()
        assert (output_dir / "agent_decision_trace.json").exists()
        manifest = json.loads((output_dir / "msdt_input_manifest.json").read_text(encoding="utf-8"))
        assert "agent_observation.json" in manifest["audit_files"]
        assert "agent_plan.json" in manifest["audit_files"]
        assert "agent_decision_trace.json" in manifest["audit_files"]
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            converter_config = json.loads(archive.read("converter_config.json").decode("utf-8"))
        assert "converter_config.json" in names
        assert "decision_trace.json" in names
        assert "parameter_audit.json" in names
        assert "msdt_input_manifest.json" in names
        assert "agent_observation.json" in names
        assert "agent_plan.json" in names
        assert "agent_decision_trace.json" in names
        assert "workflows/Default.workflow" in names
        assert "assets/prepared/sample.mzML" not in names
        assert "assets/downloads/sample.raw" not in names
        assert converter_config["generate_fragpipe_search_result"]["workflow_path"].endswith("workflows/Default.workflow")
        response = asyncio.run(download_results(task_id))
        assert "sample_parameters.zip" in response.headers["content-disposition"]
    finally:
        _tasks.pop(task_id, None)


def test_prepare_mode_generates_input_package_without_running_docker(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "_start_ready_queued_tasks", lambda: [])

    calls: list[str] = []
    output_dir = tmp_path / "prepare-sample"
    template_workflow = tmp_path / "Default.workflow"
    template_workflow.write_text("msfragger.search_enzyme_name_1=stricttrypsin\n", encoding="utf-8")
    prepared_path = output_dir / "assets" / "prepared" / "sample.mzML"
    fasta_path = output_dir / "fasta" / "uniprot_human.fasta"

    plan = SimpleNamespace(
        task_id="prepare-sample",
        source_file_name="sample.raw",
        source_data_path=prepared_path,
        fragpipe_workflow_path=template_workflow,
        manifest_path=output_dir / "fragpipe" / "fragpipe-files.fp-manifest",
        fasta_path=fasta_path,
        fasta_selection_mode="inferred",
        fasta_download_url="https://example.test/human.fasta",
        raw_data_type="mzml",
        converter_config_path=output_dir / "converter_config.json",
        rawspectrum_output_path=output_dir / "rawspectrum" / "sample_rawspectrum.parquet",
        expected_pin_path=output_dir / "fragpipe" / "exp" / "sample_edited.pin",
        output_paths={"fp_msdt": output_dir / "msdt" / "sample_fp_msdt.parquet"},
        thread_num=2,
        needs_review=False,
        blocking_issues=[],
    )
    result = SimpleNamespace(
        resolution=SimpleNamespace(
            primary_project=SimpleNamespace(project_accession="PXDTEST", matched_file="sample.raw"),
            resolution_confidence=1.0,
        ),
        context=SimpleNamespace(metadata={}, project_files=[]),
        attributes=SimpleNamespace(
            acquisition_mode=_value("DDA"),
            species=_value("Homo sapiens"),
            instrument_name=_value("Orbitrap Fusion"),
            enzyme=_value("Trypsin"),
            fixed_mods=_value(["C[57.02]"]),
            variable_mods=_value(["M[15.99]"]),
            search_parameter_hints=_value({"recommended_workflow_name": "Default.workflow"}),
        ),
        plan=plan,
        asset=SimpleNamespace(
            original_file_name="sample.raw",
            matched_project_file="sample.raw",
            download_url="https://example.test/sample.raw",
            resolved_asset_type="raw",
            requires_conversion=True,
            expected_size_bytes=123456,
        ),
    )

    class FakeService:
        def __init__(self, **_kwargs):
            pass

        def plan_dda_run_from_repository(self, **_kwargs):
            calls.append("plan")
            return result

        def _can_retry_with_mzml_instrument(self, _plan):
            return False

        def prepare_asset(self, _asset):
            calls.append("prepare_asset")
            _write_minimal_dda_mzml(prepared_path)
            return prepared_path

        def validate_prepared_data_for_plan(self, result_arg, _prepared_path):
            return result_arg

        def write_task_bundle(self, output_dir_arg, *_args, **_kwargs):
            calls.append("write_task_bundle")
            output = Path(output_dir_arg)
            output.mkdir(parents=True, exist_ok=True)
            (output / "metadata.json").write_text("{}", encoding="utf-8")
            (output / "project_resolution.json").write_text(
                json.dumps({"primary_project": {"project_accession": "PXDTEST", "matched_file": "sample.raw"}}),
                encoding="utf-8",
            )
            (output / "attributes.json").write_text(json.dumps({"species": _value("Homo sapiens").__dict__}), encoding="utf-8")
            (output / "asset_resolution.json").write_text(json.dumps({"original_file_name": "sample.raw"}), encoding="utf-8")
            (output / "decision_trace.json").write_text(json.dumps({"source_file_name": "sample.raw"}), encoding="utf-8")

    def fake_materialize_dda_task_bundle(**kwargs):
        calls.append("materialize")
        output = Path(kwargs["output_dir"])
        workflow = output / "workflows" / "Default.workflow"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("msfragger.search_enzyme_name_1=stricttrypsin\n", encoding="utf-8")
        fasta_path.parent.mkdir(parents=True, exist_ok=True)
        fasta_path.write_text(">P1\nPEPTIDE\n", encoding="utf-8")
        return SimpleNamespace(
            plan=plan,
            converter_config_path=output / "converter_config.json",
            materialized_workflow_path=workflow,
            materialized_fasta_path=fasta_path,
            task_root=output,
        )

    class FakeDockerRunner:
        def __init__(self, *_args, **_kwargs):
            calls.append("docker_init")

        def write_container_config(self, bundle):
            calls.append("write_container_config")
            bundle.converter_config_path.write_text(json.dumps({"config": "prepared"}), encoding="utf-8")

        def run(self, _bundle):
            raise AssertionError("prepare mode must not run Docker")

    monkeypatch.setattr("agent.orchestrator.pipeline.AgentService", FakeService)
    monkeypatch.setattr("agent.execution.bundle.materialize_dda_task_bundle", fake_materialize_dda_task_bundle)
    monkeypatch.setattr("agent.msdt_converter.docker_runner.DockerMSDTConverterRunner", FakeDockerRunner)

    task_id = "prepare-mode"
    _tasks[task_id] = {
        "task_id": task_id,
        "input_value": "sample.raw",
        "project_key": "sample",
        "submitter": "Alice",
        "output_dir": str(output_dir),
        "status": "running",
        "created_at": "2026-05-09T00:00:00+00:00",
        "started_at": "2026-05-09T00:00:00+00:00",
        "logs": deque(maxlen=100),
        "step": 0,
        "total_steps": 5,
        "blocking_issues": [],
        "prefer_project_fasta": False,
        "run_mode": "prepare",
        "ui_language": "en",
        "resource_policy": "balanced",
        "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1", "timeout": "1200"},
    }

    try:
        web_app._run_pipeline(task_id)

        assert calls == ["plan", "prepare_asset", "materialize", "write_task_bundle", "docker_init", "write_container_config"]
        assert _tasks[task_id]["status"] == "completed"
        assert _tasks[task_id]["step"] == 5
        assert (output_dir / "converter_config.json").exists()
        assert (output_dir / "parameter_audit.json").exists()
        assert (output_dir / "msdt_input_manifest.json").exists()
        assert (output_dir / "agent_observation.json").exists()
        assert (output_dir / "agent_plan.json").exists()
        assert (output_dir / "agent_decision_trace.json").exists()
        state = json.loads((output_dir / "task_state.json").read_text(encoding="utf-8"))
        assert state["stage"] == "packaging"
        detail = asyncio.run(get_task(task_id))
        assert detail["can_download"] is True
        zip_path = output_dir / ".download_cache" / "results-compressed.zip"
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
        assert "agent_observation.json" in names
        assert "agent_plan.json" in names
        assert "agent_decision_trace.json" in names
    finally:
        _tasks.pop(task_id, None)


def test_full_mode_failure_writes_recovery_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "_start_ready_queued_tasks", lambda: [])
    monkeypatch.delenv("AGENT_DISABLE_FULL_WORKFLOW", raising=False)
    monkeypatch.setenv("AGENT_WEB_FULL_WORKFLOW_ENABLED", "true")

    output_dir = tmp_path / "full-failed-sample"
    prepared_path = output_dir / "assets" / "prepared" / "sample.mzML"
    fasta_path = output_dir / "fasta" / "uniprot_human.fasta"
    workflow_path = output_dir / "workflows" / "Default.workflow"
    plan = SimpleNamespace(
        task_id="full-failed-sample",
        source_file_name="sample.raw",
        source_data_path=prepared_path,
        fragpipe_workflow_path=workflow_path,
        manifest_path=output_dir / "fragpipe" / "fragpipe-files.fp-manifest",
        fasta_path=fasta_path,
        fasta_selection_mode="inferred",
        fasta_download_url="https://example.test/human.fasta",
        raw_data_type="mzml",
        converter_config_path=output_dir / "converter_config.json",
        rawspectrum_output_path=output_dir / "rawspectrum" / "sample_rawspectrum.parquet",
        expected_pin_path=output_dir / "fragpipe" / "exp" / "sample_edited.pin",
        output_paths={"fp_msdt": output_dir / "msdt" / "sample_fp_msdt.parquet"},
        thread_num=2,
        needs_review=False,
        blocking_issues=[],
    )
    result = SimpleNamespace(
        resolution=SimpleNamespace(
            primary_project=SimpleNamespace(project_accession="PXDTEST", matched_file="sample.raw"),
            resolution_confidence=1.0,
            needs_review=False,
        ),
        context=SimpleNamespace(repository="pride", metadata={}, project_files=[]),
        attributes=SimpleNamespace(
            acquisition_mode=_value("DDA"),
            species=_value("Homo sapiens"),
            instrument_name=_value("Orbitrap Fusion"),
            enzyme=_value("Trypsin"),
            fixed_mods=_value(["C[57.02]"]),
            variable_mods=_value(["M[15.99]"]),
            labeling_strategy=_value("label-free"),
            search_parameter_hints=_value({"recommended_workflow_name": "Default.workflow"}),
        ),
        plan=plan,
        asset=SimpleNamespace(original_file_name="sample.raw", matched_project_file="sample.raw", resolved_asset_type="raw"),
    )

    class FakeService:
        def __init__(self, **_kwargs):
            pass

        def plan_dda_run_from_repository(self, **_kwargs):
            return result

        def _can_retry_with_mzml_instrument(self, _plan):
            return False

        def prepare_asset(self, _asset):
            _write_minimal_dda_mzml(prepared_path)
            return prepared_path

        def validate_prepared_data_for_plan(self, result_arg, _prepared_path):
            return result_arg

        def write_task_bundle(self, output_dir_arg, *_args, **_kwargs):
            Path(output_dir_arg).mkdir(parents=True, exist_ok=True)

    def fake_materialize_dda_task_bundle(**kwargs):
        output = Path(kwargs["output_dir"])
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        workflow_path.write_text("msfragger.search_enzyme_name_1=stricttrypsin\n", encoding="utf-8")
        fasta_path.parent.mkdir(parents=True, exist_ok=True)
        fasta_path.write_text(">P1\nPEPTIDE\n", encoding="utf-8")
        plan.converter_config_path.write_text(json.dumps({"config": "full"}), encoding="utf-8")
        return SimpleNamespace(
            plan=plan,
            converter_config_path=plan.converter_config_path,
            materialized_workflow_path=workflow_path,
            materialized_fasta_path=fasta_path,
            task_root=output,
        )

    class FakeDockerRunner:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, bundle):
            bundle.plan.rawspectrum_output_path.parent.mkdir(parents=True, exist_ok=True)
            bundle.plan.rawspectrum_output_path.write_text("rawspectrum", encoding="utf-8")
            bundle.plan.expected_pin_path.parent.mkdir(parents=True, exist_ok=True)
            bundle.plan.expected_pin_path.write_text("pin", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="generate msdt fail", stderr="")

    monkeypatch.setattr("agent.orchestrator.pipeline.AgentService", FakeService)
    monkeypatch.setattr("agent.execution.bundle.materialize_dda_task_bundle", fake_materialize_dda_task_bundle)
    monkeypatch.setattr("agent.msdt_converter.docker_runner.DockerMSDTConverterRunner", FakeDockerRunner)

    task_id = "full-failure"
    _tasks[task_id] = {
        "task_id": task_id,
        "input_value": "sample.raw",
        "project_key": "sample",
        "submitter": "Alice",
        "output_dir": str(output_dir),
        "status": "running",
        "created_at": "2026-05-09T00:00:00+00:00",
        "started_at": "2026-05-09T00:00:00+00:00",
        "logs": deque(maxlen=100),
        "step": 0,
        "total_steps": 5,
        "blocking_issues": [],
        "prefer_project_fasta": False,
        "run_mode": "full",
        "ui_language": "en",
        "resource_policy": "balanced",
        "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1", "timeout": "1200"},
    }

    try:
        web_app._run_pipeline(task_id)

        assert _tasks[task_id]["status"] == "failed"
        assert _tasks[task_id]["workflow_outcome"] == "failed_with_usable_partial_outputs"
        assert _tasks[task_id]["usable_partial_outputs"] is True
        assert _tasks[task_id]["recovery_primary_issue"] == "partial_outputs_available"
        recovery = json.loads((output_dir / "recovery_audit.json").read_text(encoding="utf-8"))
        assert recovery["schema_version"] == "recovery-audit/v1"
        assert recovery["failure"]["category"] == "missing_msdt_output"
        assert recovery["task"]["run_mode"] == "full"
        assert any(item["kind"] == "missing_output" for item in recovery["failure"]["evidence"])
        recovery_report = json.loads((output_dir / "agent_recovery_report.json").read_text(encoding="utf-8"))
        assert recovery_report["workflow_outcome"] == "failed_with_usable_partial_outputs"
        detail = asyncio.run(get_task(task_id))
        assert detail["status"] == "failed"
        assert detail["workflow_outcome"] == "failed_with_usable_partial_outputs"
        assert detail["usable_partial_outputs"] is True
    finally:
        _tasks.pop(task_id, None)


def test_pipeline_failure_persists_structured_error_without_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "_start_ready_queued_tasks", lambda: [])
    monkeypatch.delenv("AGENT_DEBUG_TRACEBACK", raising=False)

    output_dir = tmp_path / "failed-sample"

    class FakeService:
        def __init__(self, **_kwargs):
            pass

        def plan_dda_run_from_repository(self, **_kwargs):
            raise RuntimeError("permission denied while trying to connect to the docker API")

    monkeypatch.setattr("agent.orchestrator.pipeline.AgentService", FakeService)

    task_id = "structured-error"
    _tasks[task_id] = {
        "task_id": task_id,
        "input_value": "sample.raw",
        "project_key": "sample",
        "submitter": "Alice",
        "output_dir": str(output_dir),
        "status": "running",
        "created_at": "2026-05-09T00:00:00+00:00",
        "started_at": "2026-05-09T00:00:00+00:00",
        "logs": deque(maxlen=100),
        "step": 0,
        "total_steps": 5,
        "blocking_issues": [],
        "prefer_project_fasta": False,
        "run_mode": "parameters",
        "ui_language": "en",
        "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1", "timeout": "1200"},
    }

    try:
        web_app._run_pipeline(task_id)

        assert _tasks[task_id]["status"] == "failed"
        assert _tasks[task_id]["error_summary"]["category"] == "docker_permission"
        assert _tasks[task_id]["blocking_issues"] == [_tasks[task_id]["error_summary"]["public_message"]]
        error = json.loads((output_dir / "error.json").read_text(encoding="utf-8"))
        assert error["category"] == "docker_permission"
        assert "traceback" not in error
        recovery = json.loads((output_dir / "recovery_audit.json").read_text(encoding="utf-8"))
        assert recovery["failure"]["category"] == "docker_permission"
        assert recovery["failure"]["stage"] == "pipeline"
        assert recovery["task"]["run_mode"] == "parameters"
        history_text = (output_dir / "task_history.json").read_text(encoding="utf-8")
        assert "Traceback" not in history_text
        detail = asyncio.run(get_task(task_id))
        assert detail["error_summary"]["category"] == "docker_permission"
    finally:
        _tasks.pop(task_id, None)


def test_batch_item_error_writes_recovery_audit(tmp_path):
    output_dir = tmp_path / "batch-error"

    message = web_app._write_batch_item_error(output_dir, "sample.raw", TimeoutError("remote request timed out"))

    assert message
    recovery = json.loads((output_dir / "recovery_audit.json").read_text(encoding="utf-8"))
    assert recovery["schema_version"] == "recovery-audit/v1"
    assert recovery["failure"]["category"] == "timeout"
    assert recovery["failure"]["stage"] == "planning"
    assert recovery["task"]["run_mode"] == "batch"
    assert recovery["recovery"]["allowed_action"] == "retry_download"


def test_create_task_uses_unique_output_directory_for_repeated_input(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_pipeline_thread", lambda _task_id: None)
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_TASKS", "1")

    first = asyncio.run(
        _create_task_inner(
            {
                "input_value": "sample.raw",
                "submitter": "Alice",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )
    second = asyncio.run(
        _create_task_inner(
            {
                "input_value": "sample.raw",
                "submitter": "Bob",
                "llm_config": {"api_key": "sk-secret", "base_url": "https://api.example.com", "model": "m1"},
            }
        )
    )

    task_ids = [first.get("task_id"), second.get("task_id")]
    try:
        first_dir = Path(first["output_dir"])
        second_dir = Path(second["output_dir"])
        assert first_dir.name == "sample"
        assert second_dir.name.startswith("sample__")
        assert second_dir != first_dir
        assert (first_dir / "task_history.json").exists()
        assert (second_dir / "task_history.json").exists()

        history = asyncio.run(list_project_history())
        active_ids = {item["task_id"] for item in history["active_tasks"]}
        result_ids = {item["task_id"] for item in history["results"]}
        assert first["task_id"] in active_ids
        assert second["task_id"] in active_ids
        assert first["task_id"] not in result_ids
        assert second["task_id"] not in result_ids
    finally:
        for task_id in task_ids:
            if task_id:
                _tasks.pop(task_id, None)


def test_project_history_lists_active_submitters_and_results_without_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    active_dir = tmp_path / "active-project"
    result_dir = tmp_path / "finished-project"
    active_dir.mkdir()
    result_dir.mkdir()
    (result_dir / "result.txt").write_text("ok", encoding="utf-8")
    (result_dir / "task_history.json").write_text(
        '{"task_id":"finished","input_value":"done.raw","submitter":"Bob","status":"completed"}',
        encoding="utf-8",
    )
    _tasks["active"] = {
        "task_id": "active",
        "input_value": "active.raw",
        "submitter": "Alice",
        "status": "queued",
        "created_at": "2026-05-08T00:00:00+00:00",
        "output_dir": str(active_dir),
        "logs": deque(maxlen=10),
        "llm_config": {"api_key": "sk-active-secret"},
    }

    try:
        history = asyncio.run(list_project_history())
    finally:
        _tasks.pop("active", None)

    summary = history["summary"]
    serialized = str(history)
    assert history["active_tasks"][0]["submitter"] == "Alice"
    assert history["active_tasks"][0]["display_name"] == "active.raw"
    assert history["active_tasks"][0]["run_label"] == "active-project"
    assert history["active_tasks"][0]["status_group"] == "active"
    assert history["active_tasks"][0]["primary_action"] == "watch"
    assert history["results"][0]["submitter"] == "Bob"
    assert history["results"][0]["display_name"] == "done.raw"
    assert history["results"][0]["run_label"] == "finished-project"
    assert history["results"][0]["status_group"] == "success"
    assert history["results"][0]["primary_action"] == "view"
    assert summary["total"] == 2
    assert summary["active"] == 1
    assert summary["results"] == 1
    assert summary["status_counts"]["queued"] == 1
    assert summary["status_counts"]["completed"] == 1
    assert summary["downloadable"] == 0
    assert summary["storage_bytes"] >= len("ok")
    assert "sk-active-secret" not in serialized
    assert "api_key" not in serialized
    assert "llm_config" not in serialized


def test_project_history_adds_human_timing_and_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    output_dir = tmp_path / "timed-project"
    output_dir.mkdir()
    (output_dir / "result.txt").write_text("ok", encoding="utf-8")
    (output_dir / "task_history.json").write_text(
        json.dumps(
            {
                "task_id": "timed",
                "input_value": "timed.raw",
                "submitter": "Alice",
                "status": "failed",
                "output_dir": "timed-project",
                "created_at": "2026-05-09T11:59:00+08:00",
                "started_at": "2026-05-09T12:00:00+08:00",
                "finished_at": "2026-05-09T12:05:30+08:00",
                "updated_at": "2026-05-09T12:10:00+08:00",
                "blocking_issues": ["boom"],
            }
        ),
        encoding="utf-8",
    )

    history = asyncio.run(list_project_history())
    result = history["results"][0]

    assert result["display_name"] == "timed.raw"
    assert result["run_label"] == "timed-project"
    assert result["result_id"] == "timed-project"
    assert result["history_time"] == "2026-05-09T12:00:00+08:00"
    assert result["time_label"] == "started_at"
    assert result["duration_seconds"] == 330
    assert result["status_group"] == "failed"
    assert result["primary_action"] == "inspect"
    assert history["summary"]["status_counts"]["failed"] == 1
    assert history["summary"]["failed"] == 1


def test_project_history_keeps_record_after_download_directory_is_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    index = tmp_path / "project_history.json"
    index.write_text(
        json.dumps(
            [
                {
                    "task_id": "old",
                    "input_value": "old.raw",
                    "submitter": "Alice",
                    "status": "completed",
                    "output_dir": "old",
                    "finished_at": "2026-05-08T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    history = asyncio.run(list_project_history())

    assert history["results"][0]["task_id"] == "old"
    assert history["results"][0]["status"] == "completed"
    assert history["results"][0]["can_download"] is False


def test_task_history_persists_logs_and_review_without_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    task_id = "history-detail"
    output_dir = tmp_path / "history-detail"
    _tasks[task_id] = {
        "task_id": task_id,
        "input_value": "sample.raw",
        "submitter": "Alice",
        "status": "completed",
        "created_at": "2026-05-09T00:00:00+00:00",
        "finished_at": "2026-05-09T00:10:00+00:00",
        "output_dir": str(output_dir),
        "logs": deque(
            [
                {"type": "log", "ts": "00:00:01", "level": "info", "message": "started"},
                {"type": "review", "ts": "00:00:02", "summary": {"items": []}},
            ],
            maxlen=10,
        ),
        "step": 5,
        "total_steps": 5,
        "review_summary": {"items": [{"label": "workflow", "value": "LFQ-MBR.workflow"}]},
        "blocking_issues": ["manual review"],
        "llm_config": {"api_key": "sk-secret", "model": "m1"},
    }

    try:
        web_app._write_task_history(task_id)
    finally:
        _tasks.pop(task_id, None)

    history_text = (output_dir / "task_history.json").read_text(encoding="utf-8")
    history = json.loads(history_text)
    assert history["logs"][0]["message"] == "started"
    assert history["review_summary"]["items"][0]["value"] == "LFQ-MBR.workflow"
    assert history["blocking_issues"] == ["manual review"]
    assert "sk-secret" not in history_text
    assert "api_key" not in history_text
    assert "llm_config" not in history_text


def test_get_task_returns_archived_history_logs_after_result_directory_removed(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    (tmp_path / "project_history.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "archived",
                    "input_value": "archived.raw",
                    "submitter": "Bob",
                    "status": "completed",
                    "output_dir": "archived",
                    "finished_at": "2026-05-09T00:10:00+00:00",
                    "logs": [{"type": "log", "ts": "00:00:01", "level": "info", "message": "done"}],
                    "review_summary": {"items": [{"label": "workflow", "value": "Default.workflow"}]},
                }
            ]
        ),
        encoding="utf-8",
    )

    detail = asyncio.run(get_task("archived"))

    assert detail["task_id"] == "archived"
    assert detail["archived"] is True
    assert detail["logs"][0]["message"] == "done"
    assert detail["review_summary"]["items"][0]["value"] == "Default.workflow"
    assert detail["can_download"] is False


def test_cleanup_results_uses_finished_at_not_old_file_mtime(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setenv("AGENT_RESULT_RETENTION_SECONDS", "1800")
    project = tmp_path / "fresh-finished"
    project.mkdir()
    result_file = project / "result.txt"
    result_file.write_text("ok", encoding="utf-8")
    finished_at = time.time() - 60
    old_stamp = time.time() - 7200
    (project / "task_history.json").write_text(
        json.dumps(
            {
                "task_id": "fresh-finished",
                "status": "completed",
                "input_value": "fresh.raw",
                "output_dir": "fresh-finished",
                "finished_at": datetime_from_timestamp(finished_at),
            }
        ),
        encoding="utf-8",
    )
    for path in project.rglob("*"):
        os.utime(path, (old_stamp, old_stamp))
    os.utime(project, (old_stamp, old_stamp))

    removed = _cleanup_expired_results()

    assert "fresh-finished" not in removed
    assert project.exists()


def test_cleanup_preserves_history_index_after_result_files_expire(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setenv("AGENT_RESULT_RETENTION_SECONDS", "1800")
    project = tmp_path / "expired"
    project.mkdir()
    (project / "result.txt").write_text("ok", encoding="utf-8")
    old_stamp = time.time() - 7200
    (project / "task_history.json").write_text(
        json.dumps(
            {
                "task_id": "expired",
                "status": "completed",
                "input_value": "expired.raw",
                "submitter": "Alice",
                "output_dir": "expired",
                "finished_at": datetime_from_timestamp(old_stamp),
                "logs": [{"type": "log", "ts": "00:00:01", "level": "info", "message": "expired"}],
            }
        ),
        encoding="utf-8",
    )

    removed = _cleanup_expired_results()
    detail = asyncio.run(get_task("expired"))

    assert "expired" in removed
    assert not project.exists()
    assert detail["archived"] is True
    assert detail["logs"][0]["message"] == "expired"
    assert detail["can_download"] is False


def test_history_reflects_failed_status_and_disables_download(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    result_dir = tmp_path / "failed-project"
    result_dir.mkdir()
    (result_dir / "partial.txt").write_text("not a completed result", encoding="utf-8")
    (result_dir / "task_history.json").write_text(
        json.dumps({"task_id": "failed", "input_value": "failed.raw", "submitter": "Alice", "status": "failed"}),
        encoding="utf-8",
    )

    history = asyncio.run(list_project_history())
    result = history["results"][0]

    assert result["status"] == "failed"
    assert result["can_download"] is False


def test_project_history_uses_task_start_time_not_result_file_mtime(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setenv("AGENT_RESULT_RETENTION_SECONDS", "8640000")
    first_dir = tmp_path / "sample"
    second_dir = tmp_path / "sample__20260509-121000__bbbbbbbb"
    first_dir.mkdir()
    second_dir.mkdir()
    first_file = first_dir / "result.txt"
    second_file = second_dir / "result.txt"
    first_file.write_text("first", encoding="utf-8")
    second_file.write_text("second", encoding="utf-8")
    (first_dir / "task_history.json").write_text(
        json.dumps(
            {
                "task_id": "aaaa",
                "input_value": "sample.raw",
                "submitter": "Alice",
                "status": "completed",
                "output_dir": first_dir.name,
                "created_at": "2026-05-09T11:59:00+08:00",
                "started_at": "2026-05-09T12:00:00+08:00",
                "finished_at": "2026-05-09T12:20:00+08:00",
                "updated_at": "2026-05-09T12:30:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    (second_dir / "task_history.json").write_text(
        json.dumps(
            {
                "task_id": "bbbb",
                "input_value": "sample.raw",
                "submitter": "Bob",
                "status": "completed",
                "output_dir": second_dir.name,
                "created_at": "2026-05-09T12:09:00+08:00",
                "started_at": "2026-05-09T12:10:00+08:00",
                "finished_at": "2026-05-09T12:25:00+08:00",
                "updated_at": "2026-05-09T12:25:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    later_stamp = datetime.fromisoformat("2026-05-09T13:30:00+08:00").timestamp()
    earlier_stamp = datetime.fromisoformat("2026-05-09T12:40:00+08:00").timestamp()
    os.utime(first_file, (later_stamp, later_stamp))
    os.utime(first_dir, (later_stamp, later_stamp))
    os.utime(second_file, (earlier_stamp, earlier_stamp))
    os.utime(second_dir, (earlier_stamp, earlier_stamp))

    history = asyncio.run(list_project_history())

    assert [item["task_id"] for item in history["results"]] == ["bbbb", "aaaa"]
    assert [item["history_time"] for item in history["results"]] == [
        "2026-05-09T12:10:00+08:00",
        "2026-05-09T12:00:00+08:00",
    ]
    assert history["results"][1]["updated_at"] == "2026-05-09T12:30:00+08:00"
    assert history["results"][1]["file_updated_at"].startswith("2026-05-09T13:30:00")


def test_project_history_marks_orphan_running_record_as_interrupted(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setenv("AGENT_RESULT_RETENTION_SECONDS", "8640000")
    with web_app._tasks_lock:
        previous_tasks = dict(_tasks)
        _tasks.clear()
    (tmp_path / "project_history.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "orphan",
                    "input_value": "orphan.raw",
                    "submitter": "Alice",
                    "status": "running",
                    "output_dir": "orphan",
                    "created_at": "2026-05-09T12:00:00+08:00",
                    "started_at": "2026-05-09T12:01:00+08:00",
                    "updated_at": "2026-05-09T12:02:00+08:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    try:
        history = asyncio.run(list_project_history())
    finally:
        with web_app._tasks_lock:
            _tasks.clear()
            _tasks.update(previous_tasks)

    assert history["active_tasks"] == []
    assert history["results"][0]["task_id"] == "orphan"
    assert history["results"][0]["status"] == "failed"
    assert history["results"][0]["interrupted"] is True
    assert any("服务重启或任务被手动停止" in issue for issue in history["results"][0]["blocking_issues"])
    assert history["results"][0]["history_time"] == "2026-05-09T12:01:00+08:00"


def test_running_and_failed_tasks_are_not_downloadable(monkeypatch, tmp_path):
    running_dir = tmp_path / "running"
    failed_dir = tmp_path / "failed"
    running_dir.mkdir()
    failed_dir.mkdir()
    (running_dir / "partial.txt").write_text("running", encoding="utf-8")
    (failed_dir / "partial.txt").write_text("failed", encoding="utf-8")
    _tasks["running"] = {
        "task_id": "running",
        "input_value": "running.raw",
        "status": "running",
        "output_dir": str(running_dir),
        "logs": deque(maxlen=10),
    }
    _tasks["failed"] = {
        "task_id": "failed",
        "input_value": "failed.raw",
        "status": "failed",
        "output_dir": str(failed_dir),
        "logs": deque(maxlen=10),
    }
    try:
        running_detail = asyncio.run(get_task("running"))
        failed_detail = asyncio.run(get_task("failed"))
        running_download = asyncio.run(download_results("running"))
        failed_download = asyncio.run(download_results("failed"))
    finally:
        _tasks.pop("running", None)
        _tasks.pop("failed", None)

    assert running_detail["can_download"] is False
    assert failed_detail["can_download"] is False
    assert running_download == {"error": "任务未完成，不能下载结果"}
    assert failed_download == {"error": "任务未完成，不能下载结果"}


def test_web_task_uses_server_api_key_when_browser_key_is_absent(monkeypatch):
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setenv("AGENT_LLM_API_KEY", "sk-server-global")
    monkeypatch.delenv("AGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("AGENT_LLM_TIMEOUT", raising=False)

    result = asyncio.run(_create_task_inner({"input_value": "../outside", "llm_config": {}}))

    task_id = result.get("task_id")
    try:
        assert "error" not in result
        assert _tasks[task_id]["llm_config"]["api_key"] == "sk-server-global"
        assert _tasks[task_id]["llm_config"]["model"] == "deepseek-v4-pro"
    finally:
        if task_id:
            _tasks.pop(task_id, None)


def test_create_task_keeps_output_dir_inside_runs_for_pathlike_input(monkeypatch):
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setattr("agent.web.app._start_pipeline_thread", lambda _task_id: None)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("AGENT_LLM_TIMEOUT", raising=False)

    result = asyncio.run(_create_task_inner({"input_value": "../outside", "llm_config": {"api_key": "sk-user"}}))

    task_id = result.get("task_id")
    try:
        assert "error" not in result
        output_dir = Path(result["output_dir"])
        assert output_dir.parts[0] == "runs"
        assert output_dir.name == "outside" or output_dir.name.startswith("outside__")
        assert ".." not in output_dir.parts
    finally:
        if task_id:
            _tasks.pop(task_id, None)


def test_create_task_rejects_invalid_llm_api_before_creating_task(monkeypatch):
    async def llm_bad(_config):
        return False, "API Key 无效"

    monkeypatch.setattr("agent.web.app._check_llm_api", llm_bad, raising=False)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)

    before = set(_tasks)
    result = asyncio.run(
        _create_task_inner(
            {
                "input_value": "sample.raw",
                "llm_config": {"api_key": "sk-bad"},
            }
        )
    )

    assert result == {"error": "API Key 无效"}
    assert set(_tasks) == before


def test_create_task_queues_when_server_is_already_busy(monkeypatch):
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_TASKS", "1")
    busy_task = "busy-task"
    _tasks[busy_task] = {
        "task_id": busy_task,
        "status": "running",
        "created_at": "2026-05-08T00:00:00+00:00",
        "logs": deque(maxlen=10),
    }
    try:
        result = asyncio.run(_create_task_inner({"input_value": "sample.raw", "llm_config": {"api_key": "sk-user"}}))

        task_id = result["task_id"]
        assert result["status"] == "queued"
        assert result["queue_position"] == 1
        assert result["max_concurrent_tasks"] == 1
        assert _tasks[task_id]["status"] == "queued"
    finally:
        _tasks.pop(busy_task, None)
        if "task_id" in locals():
            _tasks.pop(task_id, None)


def test_queue_starts_tasks_in_creation_order(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_TASKS", "1")
    first = "queued-first"
    second = "queued-second"
    _tasks[first] = {
        "task_id": first,
        "status": "queued",
        "created_at": "2026-05-08T00:00:00+00:00",
        "logs": deque(maxlen=10),
        "llm_config": {"api_key": "sk-first", "base_url": "https://api.example.com", "model": "m1", "timeout": "30"},
    }
    _tasks[second] = {
        "task_id": second,
        "status": "queued",
        "created_at": "2026-05-08T00:00:01+00:00",
        "logs": deque(maxlen=10),
        "llm_config": {"api_key": "sk-second", "base_url": "https://api.example.com", "model": "m2", "timeout": "30"},
    }
    try:
        assert _try_start_queued_task(second) is False
        assert _try_start_queued_task(first) is True
        assert _tasks[first]["status"] == "running"
        assert _tasks[second]["status"] == "queued"
        assert os.environ.get("AGENT_LLM_MODEL") != "m1"
        assert _tasks[first]["llm_config"]["model"] == "m1"
    finally:
        _tasks.pop(first, None)
        _tasks.pop(second, None)


def test_queue_scheduler_starts_next_task_after_slot_frees(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_TASKS", "1")
    started: list[str] = []
    monkeypatch.setattr("agent.web.app._start_pipeline_thread", started.append)
    running = "running-before"
    queued = "queued-after"
    _tasks[running] = {
        "task_id": running,
        "status": "running",
        "created_at": "2026-05-08T00:00:00+00:00",
        "logs": deque(maxlen=10),
    }
    _tasks[queued] = {
        "task_id": queued,
        "status": "queued",
        "created_at": "2026-05-08T00:00:01+00:00",
        "logs": deque(maxlen=10),
        "llm_config": {"api_key": "sk-next", "base_url": "https://api.example.com", "model": "m2", "timeout": "30"},
    }
    try:
        assert _start_ready_queued_tasks() == []
        _tasks[running]["status"] = "completed"
        assert _start_ready_queued_tasks() == [queued]
        assert started == [queued]
        assert _tasks[queued]["status"] == "running"
    finally:
        _tasks.pop(running, None)
        _tasks.pop(queued, None)


def test_task_detail_and_health_include_queue_information(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_TASKS", "1")
    monkeypatch.setattr(
        "agent.web.app.collect_system_metrics",
        lambda root: {
            "cpu": {"logical_cores": 8, "load_percent": 12.5, "load_1m": 1.0},
            "memory": {
                "total_bytes": 16_000,
                "used_bytes": 4_000,
                "available_bytes": 12_000,
                "used_percent": 25.0,
            },
            "disk": {
                "total_bytes": 100_000,
                "used_bytes": 40_000,
                "free_bytes": 60_000,
                "used_percent": 40.0,
            },
        },
    )
    running = "running-task"
    queued = "queued-task"
    _tasks[running] = {
        "task_id": running,
        "input_value": "running.raw",
        "status": "running",
        "step": 1,
        "total_steps": 5,
        "logs": deque(maxlen=10),
        "blocking_issues": [],
    }
    _tasks[queued] = {
        "task_id": queued,
        "input_value": "queued.raw",
        "status": "queued",
        "step": 0,
        "total_steps": 5,
        "logs": deque(maxlen=10),
        "blocking_issues": [],
    }
    try:
        detail = asyncio.run(get_task(queued))
        status = asyncio.run(health())

        assert detail["queue_position"] == 1
        assert detail["queue_length"] == 1
        assert status["running_tasks"] == 1
        assert status["queued_tasks"] == 1
        assert status["system_metrics"]["cpu"]["logical_cores"] == 8
        assert status["system_metrics"]["cpu"]["load_percent"] == 12.5
        assert status["system_metrics"]["memory"]["used_percent"] == 25.0
        assert status["system_metrics"]["disk"]["free_bytes"] == 60_000
    finally:
        _tasks.pop(running, None)
        _tasks.pop(queued, None)


def test_web_reporter_renders_download_progress_events():
    task_id = "progress-test"
    _tasks[task_id] = {"logs": deque(maxlen=10)}
    try:
        reporter = WebReporter(task_id)

        reporter(
            {
                "kind": "download_progress",
                "label": "sample.raw",
                "downloaded": 5 * 1024 * 1024,
                "total": 20 * 1024 * 1024,
                "speed_bps": 2 * 1024 * 1024,
                "eta_seconds": 7.5,
                "complete": False,
            }
        )

        assert "25.0%" in _tasks[task_id]["logs"][0]["message"]
        assert "2.0 MB/s" in _tasks[task_id]["logs"][0]["message"]
    finally:
        _tasks.pop(task_id, None)


def test_stderr_capture_does_not_log_each_streaming_token_on_flush():
    task_id = "llm-stream-test"
    _tasks[task_id] = {"logs": deque(maxlen=10)}
    try:
        capture = StderrCapture(task_id)

        capture.write('"fixed')
        capture.flush()
        capture.write(' modification"')
        capture.flush()
        capture._flush()

        llm_logs = [log["message"] for log in _tasks[task_id]["logs"] if log["level"] == "llm"]
        assert llm_logs == ['"fixed modification"']
    finally:
        _tasks.pop(task_id, None)


def test_download_results_uses_cached_zip(tmp_path):
    task_id = "download-test"
    output_dir = tmp_path / "result"
    output_dir.mkdir()
    (output_dir / "result.txt").write_text("ok", encoding="utf-8")
    _tasks[task_id] = {
        "task_id": task_id,
        "input_value": "../sample.raw",
        "output_dir": str(output_dir),
        "status": "completed",
        "logs": deque(maxlen=10),
    }
    response = None
    try:
        before_pack = asyncio.run(get_task(task_id))
        not_ready = asyncio.run(download_results(task_id))
        assert before_pack["can_download"] is False
        assert not_ready == {"error": "结果 ZIP 尚未打包完成，请等待任务日志提示后再下载。"}
        zip_path = _zip_output_dir(output_dir)
        assert zip_path.name == "results-compressed.zip"
        response = asyncio.run(download_results(task_id))

        assert "sample_results.zip" in response.headers["content-disposition"]
        assert Path(response.path).parent.name == ".download_cache"
        with zipfile.ZipFile(response.path) as archive:
            assert archive.read("result.txt") == b"ok"
            assert archive.getinfo("result.txt").compress_type == zipfile.ZIP_DEFLATED
        cached_path = Path(response.path)
        second = asyncio.run(download_results(task_id))
        assert Path(second.path) == cached_path
    finally:
        _tasks.pop(task_id, None)


def test_download_results_excludes_large_intermediate_assets(tmp_path):
    task_id = "download-filter-test"
    output_dir = tmp_path / "result"
    (output_dir / "ai_ready").mkdir(parents=True)
    (output_dir / "msdt").mkdir()
    (output_dir / "assets" / "downloads").mkdir(parents=True)
    (output_dir / "fragpipe" / "exp").mkdir(parents=True)
    (output_dir / "workflows").mkdir()
    (output_dir / "ai_ready" / "sample_ai_ready.parquet").write_text("ai", encoding="utf-8")
    (output_dir / "msdt" / "sample_fp_msdt.parquet").write_text("msdt", encoding="utf-8")
    (output_dir / "assets" / "downloads" / "sample.raw").write_text("raw", encoding="utf-8")
    (output_dir / "fragpipe" / "fragger.params").write_text("search_enzyme_name_1 = stricttrypsin", encoding="utf-8")
    (output_dir / "fragpipe" / "Default.workflow").write_text("msfragger.search_enzyme_name_1=stricttrypsin", encoding="utf-8")
    (output_dir / "workflows" / "Default.workflow").write_text("msfragger.search_enzyme_name_1=stricttrypsin", encoding="utf-8")
    (output_dir / "fragpipe" / "exp" / "sample.pin").write_text("pin", encoding="utf-8")
    _tasks[task_id] = {
        "task_id": task_id,
        "input_value": "sample.raw",
        "output_dir": str(output_dir),
        "status": "completed",
    }
    response = None
    try:
        _zip_output_dir(output_dir)
        response = asyncio.run(download_results(task_id))

        with zipfile.ZipFile(response.path) as archive:
            names = set(archive.namelist())
        assert "ai_ready/sample_ai_ready.parquet" in names
        assert "msdt/sample_fp_msdt.parquet" in names
        assert "fragpipe/fragger.params" in names
        assert "fragpipe/Default.workflow" in names
        assert "workflows/Default.workflow" in names
        assert ".download_cache/results-compressed.zip" not in names
        assert "assets/downloads/sample.raw" not in names
        assert "fragpipe/exp/sample.pin" not in names
    finally:
        _tasks.pop(task_id, None)


def test_zip_output_rebuilds_cached_archive_missing_parameter_files(tmp_path):
    output_dir = tmp_path / "result"
    (output_dir / "msdt").mkdir(parents=True)
    (output_dir / "fragpipe").mkdir()
    msdt_path = output_dir / "msdt" / "sample_fp_msdt.parquet"
    params_path = output_dir / "fragpipe" / "fragger.params"
    msdt_path.write_text("msdt", encoding="utf-8")
    params_path.write_text("search_enzyme_name_1 = stricttrypsin", encoding="utf-8")

    zip_path = output_dir / ".download_cache" / "results-compressed.zip"
    zip_path.parent.mkdir()
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(msdt_path, "msdt/sample_fp_msdt.parquet")

    rebuilt = _zip_output_dir(output_dir)

    assert rebuilt == zip_path
    with zipfile.ZipFile(rebuilt) as archive:
        names = set(archive.namelist())
    assert "msdt/sample_fp_msdt.parquet" in names
    assert "fragpipe/fragger.params" in names


def test_saved_llm_config_is_masked_and_used_without_browser_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_LLM_CONFIG_PATH", str(tmp_path / "secrets" / "llm_config.json"))
    monkeypatch.setattr("agent.web.app._run_llm_check", _llm_ok)

    saved = asyncio.run(
        web_app.save_llm_config(
            {
                "llm_config": {
                    "api_key": "saved-secret",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-v4-pro",
                    "timeout": "1200",
                }
            }
        )
    )
    public = asyncio.run(web_app.get_llm_config())
    effective, error = web_app._build_llm_config({})

    assert saved["ok"] is True
    assert saved["api_key_set"] is True
    assert "api_key" not in saved
    assert public == {
        "api_key_set": True,
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "timeout": "1200",
        "source": "saved",
    }
    assert effective == {
        "api_key": "saved-secret",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "timeout": "1200",
    }
    assert error is None
    assert "saved-secret" not in json.dumps(saved)
    assert "saved-secret" not in json.dumps(public)


def test_saved_llm_config_can_be_deleted(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_LLM_CONFIG_PATH", str(tmp_path / "llm_config.json"))
    monkeypatch.setattr("agent.web.app._run_llm_check", _llm_ok)
    asyncio.run(
        web_app.save_llm_config(
            {"llm_config": {"api_key": "saved-secret", "model": "deepseek-v4-pro"}}
        )
    )

    deleted = asyncio.run(web_app.delete_llm_config())
    public = asyncio.run(web_app.get_llm_config())

    assert deleted == {"ok": True, "deleted": True}
    assert public["api_key_set"] is False


def test_list_llm_models_uses_saved_config_without_returning_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_LLM_CONFIG_PATH", str(tmp_path / "llm_config.json"))
    monkeypatch.setattr("agent.web.app._run_llm_check", _llm_ok)
    asyncio.run(
        web_app.save_llm_config(
            {"llm_config": {"api_key": "saved-secret", "model": "deepseek-v4-pro"}}
        )
    )
    captured = {}

    async def fake_fetch(config):
        captured.update(config)
        return ["deepseek-v4-flash", "deepseek-v4-pro"]

    monkeypatch.setattr("agent.web.app._fetch_llm_models", fake_fetch)
    result = asyncio.run(web_app.list_llm_models({"llm_config": {}}))

    assert result == {
        "ok": True,
        "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
        "selected": "deepseek-v4-pro",
    }
    assert captured["api_key"] == "saved-secret"
    assert "saved-secret" not in json.dumps(result)
