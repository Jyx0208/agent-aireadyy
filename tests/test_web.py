from __future__ import annotations

import asyncio
import json
import os
import time
import zipfile
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.web.app as web_app
from agent.web.app import StderrCapture, WebReporter, _create_task_inner, _tasks
from agent.web.app import _build_review_summary, _cleanup_expired_results, _list_public_results, _zip_output_dir
from agent.web.app import _start_ready_queued_tasks, _strip_ansi
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


def test_strip_ansi_removes_llm_color_codes():
    raw = "\x1b[0m\x1b[90mrecommended\x1b[0m_fasta_url"

    assert _strip_ansi(raw) == "recommended_fasta_url"


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
                "label": "Homo_sapiens_reviewed.fasta",
                "downloaded": 1 * 1024 * 1024,
                "total": 20 * 1024 * 1024,
                "speed_bps": 1 * 1024 * 1024,
                "complete": False,
            }
        )
        reporter(
            {
                "kind": "download_progress",
                "label": "Homo_sapiens_reviewed.fasta",
                "downloaded": 2 * 1024 * 1024,
                "total": 20 * 1024 * 1024,
                "speed_bps": 1 * 1024 * 1024,
                "complete": False,
            }
        )
        reporter(
            {
                "kind": "download_progress",
                "label": "Homo_sapiens_reviewed.fasta",
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
        assert "下载完成" in second["message"]
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

    serialized = str(history)
    assert history["active_tasks"][0]["submitter"] == "Alice"
    assert history["results"][0]["submitter"] == "Bob"
    assert "sk-active-secret" not in serialized
    assert "api_key" not in serialized
    assert "llm_config" not in serialized


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


def test_web_task_requires_each_user_api_key_in_request(monkeypatch):
    monkeypatch.setattr("agent.web.app._check_llm_api", _llm_ok, raising=False)
    monkeypatch.setenv("AGENT_LLM_API_KEY", "sk-server-global")
    monkeypatch.delenv("AGENT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)
    monkeypatch.delenv("AGENT_LLM_TIMEOUT", raising=False)

    result = asyncio.run(_create_task_inner({"input_value": "../outside", "llm_config": {}}))

    assert result == {"error": "请先填写本次任务使用的 API Key"}


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
        assert output_dir.parts == ("runs", "outside")
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
        assert not_ready == {"error": "ZIP package is not ready. Wait for packaging to finish before downloading."}
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
    (output_dir / "ai_ready" / "sample_ai_ready.parquet").write_text("ai", encoding="utf-8")
    (output_dir / "msdt" / "sample_fp_msdt.parquet").write_text("msdt", encoding="utf-8")
    (output_dir / "assets" / "downloads" / "sample.raw").write_text("raw", encoding="utf-8")
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
        assert ".download_cache/results-compressed.zip" not in names
        assert "assets/downloads/sample.raw" not in names
        assert "fragpipe/exp/sample.pin" not in names
    finally:
        _tasks.pop(task_id, None)
