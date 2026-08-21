from __future__ import annotations

import json
import subprocess

import httpx

from agent.errors import build_error_record, classify_error, write_error_record


def test_error_record_redacts_api_keys_and_omits_traceback_by_default(tmp_path):
    exc = RuntimeError("request failed with sk-secret-token-1234567890 in payload")

    record = build_error_record(exc, stage="planning", input_file="sample.raw")
    path = write_error_record(tmp_path / "error.json", record)

    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["category"] == "unknown"
    assert data["stage"] == "planning"
    assert data["input_file"] == "sample.raw"
    assert "sk-secret" not in text
    assert "[redacted-api-key]" in text
    assert "traceback" not in data


def test_classify_http_timeout_and_auth_errors():
    request = httpx.Request("GET", "https://www.ebi.ac.uk/pride/ws/archive/v2/projects/PXD000001")
    response = httpx.Response(401, request=request, text="unauthorized")
    auth_error = httpx.HTTPStatusError("bad auth", request=request, response=response)
    timeout_error = httpx.ReadTimeout("read timed out", request=request)

    auth = classify_error(auth_error, stage="llm_check")
    timeout = classify_error(timeout_error, stage="resolution")

    assert auth.category == "auth"
    assert auth.retryable is False
    assert "API Key" in auth.public_message
    assert timeout.category == "timeout"
    assert timeout.retryable is True
    assert "超时" in timeout.public_message


def test_classify_common_runtime_failures():
    docker = RuntimeError("permission denied while trying to connect to the docker API at unix:///var/run/docker.sock")
    memory = RuntimeError("MSFragger reported Insufficient memory!")
    missing_tool = FileNotFoundError("No such file or directory: 'msconvert'")
    nonzero = subprocess.CalledProcessError(1, ["docker", "run"], output="generate msdt fail")

    assert classify_error(docker).category == "docker_permission"
    assert classify_error(memory).category == "insufficient_memory"
    assert classify_error(missing_tool).category == "missing_tool"
    assert classify_error(nonzero).category == "process_failed"


def test_classify_docker_registry_pull_failure_separately_from_daemon():
    error = subprocess.CalledProcessError(
        125,
        ["docker", "run"],
        output=(
            "docker: Error response from daemon: failed to resolve reference "
            '"docker.io/chambm/pwiz-skyline-i-agree-to-the-vendor-licenses:latest": '
            'Head "https://registry-1.docker.io/v2/": connectex timeout'
        ),
    )

    result = classify_error(error, stage="batch_item")

    assert result.category == "docker_image_unavailable"
    assert "ProteoWizard" in result.public_message


def test_classify_network_error_uses_readable_public_message():
    request = httpx.Request("GET", "https://ftp.pride.ebi.ac.uk/")
    error = httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]", request=request)

    classified = classify_error(error, stage="resolution")

    assert classified.category == "network"
    assert classified.public_message == "网络连接失败。"
    assert "网络" in classified.operator_hint
