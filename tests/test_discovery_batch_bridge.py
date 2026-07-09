from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent.cli import app
from agent.discovery.batch_bridge import (
    build_batch_result_report,
    build_batch_submission_report,
    normalize_batch_parameters_request,
    redact_batch_request,
    write_batch_result_report,
    write_batch_submission_report,
)


def _request(inputs: list[str] | str | None = None) -> dict[str, object]:
    return {
        "inputs": inputs if inputs is not None else ["a.raw", "b.raw"],
        "submitter": "discovery_handoff",
        "repository": "pride",
        "run_mode": "parameters",
        "resource_policy": "balanced",
        "jobs": 2,
    }


def test_normalize_batch_parameters_request_accepts_multiline_inputs():
    payload = normalize_batch_parameters_request(_request("a.raw\n\nb.raw\n"))

    assert payload["inputs"] == ["a.raw", "b.raw"]
    assert payload["run_mode"] == "parameters"
    assert payload["jobs"] == 2


def test_normalize_batch_parameters_request_preserves_discovery_input_records():
    payload = normalize_batch_parameters_request(
        {
            **_request(["a.raw", "b.raw"]),
            "input_records": [
                {"file_name": "a.raw", "project_accession": "PXD_A", "download_url": "https://example.test/a.raw"},
                {"file_name": "b.raw", "project_accession": "PXD_B", "download_url": "https://example.test/b.raw"},
            ],
        }
    )

    assert payload["inputs"] == ["a.raw", "b.raw"]
    assert payload["input_record_mode"] == "discovery_handoff_v1"
    assert [record["project_accession"] for record in payload["input_records"]] == ["PXD_A", "PXD_B"]


def test_normalize_batch_parameters_request_can_derive_inputs_from_records():
    payload = normalize_batch_parameters_request(
        {
            "input_records": [
                {"file_name": "a.raw", "project_accession": "PXD_A"},
                {"file_name": "b.raw", "project_accession": "PXD_B"},
            ],
            "run_mode": "parameters",
        }
    )

    assert payload["inputs"] == ["a.raw", "b.raw"]
    assert len(payload["input_records"]) == 2


def test_batch_submission_dry_run_ready(tmp_path: Path):
    report = build_batch_submission_report(_request(), output_dir=tmp_path)

    assert report.status in {"ready", "blocked"}
    assert report.execute is False
    assert report.input_count == 2
    if report.status == "ready":
        assert report.next_step == "run_with_execute_flag"


def test_batch_submission_blocks_empty_inputs(tmp_path: Path):
    report = build_batch_submission_report(_request([]), output_dir=tmp_path)

    assert report.status == "blocked"
    assert report.input_count == 0
    assert report.blocking_issues == ["Batch request has no inputs."]


def test_batch_submission_execute_posts_when_ready(tmp_path: Path):
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_preflight(**kwargs):
        return {
            "status": "ok",
            "run_mode": kwargs["run_mode"],
            "repository": kwargs["repository"],
            "input_count": len(kwargs["inputs"]),
            "blocking_issues": [],
            "warnings": [],
            "checks": [],
        }

    def fake_post(url: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((url, payload))
        return {"batch_id": "batch123", "status": "queued"}

    report = build_batch_submission_report(
        {**_request(), "llm_config": {"api_key": "sk-test", "base_url": "https://api.deepseek.com", "model": "deepseek-chat", "timeout": "60"}},
        output_dir=tmp_path,
        execute=True,
        web_url="http://web.local",
        preflight_runner=fake_preflight,
        http_post_json=fake_post,
    )

    assert report.status == "submitted"
    assert report.next_step == "watch_batch_status"
    assert report.response == {"batch_id": "batch123", "status": "queued"}
    assert calls[0][0] == "http://web.local/api/batches/parameters"
    assert calls[0][1]["inputs"] == ["a.raw", "b.raw"]
    assert calls[0][1]["llm_config"]["api_key"] == "sk-test"


def test_batch_submission_execute_posts_discovery_input_records(tmp_path: Path):
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_preflight(**kwargs):
        return {
            "status": "ok",
            "run_mode": kwargs["run_mode"],
            "repository": kwargs["repository"],
            "input_count": len(kwargs["inputs"]),
            "blocking_issues": [],
            "warnings": [],
            "checks": [],
        }

    report = build_batch_submission_report(
        {
            **_request(["a.raw"]),
            "input_records": [{"file_name": "a.raw", "project_accession": "PXD_A"}],
            "llm_config": {"api_key": "sk-test"},
        },
        output_dir=tmp_path,
        execute=True,
        web_url="http://web.local",
        preflight_runner=fake_preflight,
        http_post_json=lambda url, payload: calls.append((url, payload)) or {"batch_id": "batch123"},
    )

    assert report.status == "submitted"
    assert calls[0][1]["input_records"][0]["project_accession"] == "PXD_A"


def test_batch_submission_execute_does_not_post_when_blocked(tmp_path: Path):
    calls: list[tuple[str, dict[str, object]]] = []

    report = build_batch_submission_report(
        _request([]),
        output_dir=tmp_path,
        execute=True,
        http_post_json=lambda url, payload: calls.append((url, payload)) or {},
    )

    assert report.status == "blocked"
    assert calls == []


def test_batch_submission_execute_blocks_without_llm_config(tmp_path: Path, monkeypatch):
    for name in ["DEEPSEEK_API_KEY", "AGENT_LLM_API_KEY", "OPENAI_API_KEY"]:
        monkeypatch.delenv(name, raising=False)

    report = build_batch_submission_report(_request(), output_dir=tmp_path, execute=True)

    assert report.status == "blocked"
    assert "No llm_config.api_key is available for Web batch submission." in report.blocking_issues


def test_redact_batch_request_removes_api_key():
    redacted = redact_batch_request(
        {"inputs": ["a.raw"], "llm_config": {"api_key": "sk-secret", "model": "deepseek-chat"}}
    )

    assert redacted["llm_config"]["api_key"] == "***redacted***"


def test_write_batch_submission_report_outputs_normalized_request(tmp_path: Path):
    paths = write_batch_submission_report(
        {
            **_request("a.raw\nb.raw"),
            "llm_config": {"api_key": "sk-secret", "base_url": "https://api.deepseek.com"},
        },
        tmp_path,
    )

    report = json.loads(paths["batch_submission_report"].read_text(encoding="utf-8"))
    normalized = json.loads(paths["normalized_batch_request"].read_text(encoding="utf-8"))
    assert report["input_count"] == 2
    assert normalized["inputs"] == ["a.raw", "b.raw"]
    assert normalized["llm_config"]["api_key"] == "***redacted***"


def test_submit_discovery_batch_request_cli_dry_run(tmp_path: Path):
    request_json = tmp_path / "batch_parameters_request.json"
    request_json.write_text(json.dumps(_request()), encoding="utf-8")
    output_dir = tmp_path / "submission"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "submit-discovery-batch-request",
            "--request",
            str(request_json),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["execute"] is False
    assert payload["input_count"] == 2
    assert (output_dir / "batch_submission_report.json").exists()
    assert (output_dir / "normalized_batch_parameters_request.json").exists()


def test_build_batch_result_report_counts_item_statuses(tmp_path: Path):
    excel = tmp_path / "benchmark_results.xlsx"
    excel.write_bytes(b"xlsx")
    manifest = {
        "batch_id": "batch-1",
        "status": "completed",
        "run_mode": "parameters",
        "repository": "pride",
        "excel_path": str(excel),
        "items": [
            {"index": 1, "input": "a.raw", "status": "completed", "output_dir": "items/a"},
            {"index": 2, "input": "b.raw", "status": "failed", "output_dir": "items/b", "error": "no project"},
            {"index": 3, "input": "c.raw", "status": "needs_review", "output_dir": "items/c", "error": "ambiguous"},
        ],
    }

    report = build_batch_result_report(manifest)

    assert report.item_count == 3
    assert report.completed_items == 1
    assert report.failed_items == 1
    assert report.needs_review_items == 1
    assert report.success_rate == 0.333333
    assert report.excel_exists is True
    assert report.status_counts == {"completed": 1, "failed": 1, "needs_review": 1}
    assert report.error_counts == {"ambiguous": 1, "no project": 1}


def test_write_batch_result_report_outputs_json_and_csv(tmp_path: Path):
    manifest = {
        "batch_id": "batch-1",
        "status": "completed",
        "items": [
            {"index": 1, "input": "a.raw", "status": "completed", "output_dir": "items/a"},
        ],
    }

    paths = write_batch_result_report(manifest, tmp_path)

    report = json.loads(paths["batch_result_report"].read_text(encoding="utf-8"))
    assert report["success_rate"] == 1.0
    assert paths["batch_result_items"].read_text(encoding="utf-8").splitlines()[0] == (
        "index,input,status,output_dir,error,started_at,finished_at"
    )


def test_summarize_discovery_batch_cli_writes_report(tmp_path: Path):
    manifest_path = tmp_path / "batch_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "batch_id": "batch-1",
                "status": "completed",
                "items": [
                    {"index": 1, "input": "a.raw", "status": "completed", "output_dir": "items/a"},
                    {"index": 2, "input": "b.raw", "status": "completed", "output_dir": "items/b"},
                ],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "summary"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "summarize-discovery-batch",
            "--batch-manifest",
            str(manifest_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["success_rate"] == 1.0
    assert payload["completed_items"] == 2
    assert (output_dir / "batch_result_report.json").exists()
    assert (output_dir / "batch_result_items.csv").exists()
