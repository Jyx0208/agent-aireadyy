from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from agent.cli import app
from agent.control_plane.models import OpenAIAgentsDiscoveryResult


def test_python_module_cli_entrypoint_shows_help():
    result = subprocess.run(
        [sys.executable, "-m", "agent.cli", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "PRIDE-first AI-ready data agent" in result.stdout
    assert "one-click-run" in result.stdout


def test_agents_discover_cli_passes_dynamic_limits(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(**kwargs: Any) -> OpenAIAgentsDiscoveryResult:
        captured.update(kwargs)
        return OpenAIAgentsDiscoveryResult(
            status="completed",
            run_id="cli_multi_agent",
            output_dir=str(tmp_path / "out"),
            state_db=str(tmp_path / "state.sqlite"),
        )

    monkeypatch.setattr("agent.cli.run_openai_agents_discovery", fake_run)
    result = CliRunner().invoke(
        app,
        [
            "agents-discover-dataset",
            "--prompt",
            "Find human plasma DDA data",
            "--output-dir",
            str(tmp_path / "out"),
            "--discovery-mode",
            "multi_agent",
            "--max-query-units",
            "24",
            "--max-repository-requests",
            "120",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["mode"] == "multi_agent"
    assert captured["dynamic_limits"].max_query_units == 24
    assert captured["dynamic_limits"].max_repository_requests == 120
