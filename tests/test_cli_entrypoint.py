from __future__ import annotations

import subprocess
import sys


def test_python_module_cli_entrypoint_shows_help():
    result = subprocess.run(
        [sys.executable, "-m", "agent.cli", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "PRIDE-first AI-ready data agent" in result.stdout
    assert "one-click-run" in result.stdout
