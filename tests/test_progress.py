from __future__ import annotations

from pathlib import Path

from agent.cli import ConsoleReporter
from agent.progress import render_download_progress


def test_render_download_progress_includes_percent_speed_and_eta():
    event = {
        "kind": "download_progress",
        "label": "sample.raw",
        "downloaded": 5 * 1024 * 1024,
        "total": 20 * 1024 * 1024,
        "speed_bps": 2 * 1024 * 1024,
        "eta_seconds": 7.5,
        "complete": False,
    }

    line = render_download_progress(event, width=20)

    assert "25.0%" in line
    assert "2.0 MB/s" in line
    assert "ETA 00:07" in line
    assert "sample.raw" in line
    assert "[" in line and "]" in line


def test_console_reporter_renders_progress_in_place(monkeypatch):
    calls: list[tuple[str, bool, bool]] = []

    def fake_echo(message: str, err: bool = False, nl: bool = True) -> None:
        calls.append((message, err, nl))

    monkeypatch.setattr("agent.cli.typer.echo", fake_echo)

    reporter = ConsoleReporter()
    reporter(
        {
            "kind": "download_progress",
            "label": "sample.raw",
            "downloaded": 10,
            "total": 100,
            "speed_bps": 10.0,
            "eta_seconds": 9.0,
            "complete": False,
        }
    )
    reporter("Next step")

    assert calls[0][1] is True
    assert calls[0][2] is False
    assert calls[0][0].startswith("\r")
    assert calls[1][0] == ""
    assert calls[1][2] is True
    assert calls[2][0] == "Next step"


def test_console_reporter_clears_trailing_progress_characters(monkeypatch):
    calls: list[tuple[str, bool, bool]] = []

    def fake_echo(message: str, err: bool = False, nl: bool = True) -> None:
        calls.append((message, err, nl))

    monkeypatch.setattr("agent.cli.typer.echo", fake_echo)

    reporter = ConsoleReporter()
    reporter(
        {
            "kind": "download_progress",
            "label": "very-long-file-name.raw",
            "downloaded": 50,
            "total": 100,
            "speed_bps": 10.0,
            "eta_seconds": 5.0,
            "complete": False,
        }
    )
    reporter(
        {
            "kind": "download_progress",
            "label": "a.raw",
            "downloaded": 60,
            "total": 100,
            "speed_bps": 10.0,
            "eta_seconds": 4.0,
            "complete": False,
        }
    )

    assert calls[1][0].startswith("\r")
    assert calls[1][0].endswith(" ")


def test_console_reporter_writes_runtime_log(monkeypatch, tmp_path: Path):
    calls: list[tuple[str, bool, bool]] = []

    def fake_echo(message: str, err: bool = False, nl: bool = True) -> None:
        calls.append((message, err, nl))

    monkeypatch.setattr("agent.cli.typer.echo", fake_echo)

    log_path = tmp_path / "runtime.log"
    reporter = ConsoleReporter(log_path=log_path)
    reporter("step one")
    reporter(
        {
            "kind": "download_progress",
            "label": "sample.raw",
            "downloaded": 50,
            "total": 100,
            "speed_bps": 10.0,
            "eta_seconds": 5.0,
            "complete": True,
        }
    )
    reporter("step two")

    content = log_path.read_text(encoding="utf-8")
    assert "step one" in content
    assert "sample.raw" in content
    assert "step two" in content


def test_console_reporter_skips_duplicate_progress_frames(monkeypatch, tmp_path: Path):
    calls: list[tuple[str, bool, bool]] = []

    def fake_echo(message: str, err: bool = False, nl: bool = True) -> None:
        calls.append((message, err, nl))

    monkeypatch.setattr("agent.cli.typer.echo", fake_echo)

    log_path = tmp_path / "runtime.log"
    reporter = ConsoleReporter(log_path=log_path)
    event = {
        "kind": "download_progress",
        "label": "sample.raw",
        "downloaded": 5 * 1024 * 1024,
        "total": 20 * 1024 * 1024,
        "speed_bps": 2 * 1024 * 1024,
        "eta_seconds": 7.5,
        "complete": False,
    }

    reporter(event)
    reporter(dict(event))

    assert calls == [(calls[0][0], True, False)]
    assert log_path.read_text(encoding="utf-8").count("sample.raw") == 1
