from __future__ import annotations

import asyncio
import os
import zipfile
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from agent.web.app import StderrCapture, WebReporter, _create_task_inner, _tasks
from agent.web.app import _build_review_summary, _start_ready_queued_tasks, _strip_ansi
from agent.web.app import _try_start_queued_task, download_results, get_task, health


async def _llm_ok(_config):
    return True, "ok"


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


def test_download_progress_updates_single_visible_log_line():
    task_id = "progress-upsert-test"
    _tasks[task_id] = {"logs": deque(maxlen=10)}
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

        first, second = list(_tasks[task_id]["logs"])
        assert first["key"] == second["key"]
        assert first["replace"] is True
        assert second["replace"] is True
        assert "Homo_sapiens_reviewed.fasta" in second["message"]
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


def test_download_results_attaches_temp_zip_cleanup(tmp_path):
    task_id = "download-test"
    output_dir = tmp_path / "result"
    output_dir.mkdir()
    (output_dir / "result.txt").write_text("ok", encoding="utf-8")
    _tasks[task_id] = {
        "task_id": task_id,
        "input_value": "../sample.raw",
        "output_dir": str(output_dir),
    }
    response = None
    try:
        response = asyncio.run(download_results(task_id))

        assert response.background is not None
        assert "sample_results.zip" in response.headers["content-disposition"]
        with zipfile.ZipFile(response.path) as archive:
            assert archive.read("result.txt") == b"ok"
    finally:
        _tasks.pop(task_id, None)
        if response is not None and hasattr(response, "path"):
            Path(response.path).unlink(missing_ok=True)
