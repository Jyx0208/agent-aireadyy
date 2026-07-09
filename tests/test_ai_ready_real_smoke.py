from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.real_smoke import run_ai_ready_real_smoke
from agent.cli import app


def _write_tsv(path: Path, rows: list[dict[str, object]]) -> Path:
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)
    return path


def _write_mgf(path: Path, *, scan: int = 101) -> Path:
    path.write_text(
        "\n".join(
            [
                "BEGIN IONS",
                f"TITLE=scan={scan}",
                f"SCANS={scan}",
                "PEPMASS=500.2",
                "CHARGE=2+",
                "98.06004 1000",
                "147.11280 500",
                "END IONS",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_real_smoke_runs_rt_and_denovo_from_local_search_dir(tmp_path: Path):
    search_dir = tmp_path / "search"
    search_dir.mkdir()
    _write_tsv(
        search_dir / "psm.tsv",
        [
            {
                "Peptide": "PEPTIDEK",
                "Modified Peptide": "PEPTIDEK",
                "Charge": 2,
                "Spectrum": "scan=101",
                "Retention": 12.5,
                "PSM Q-Value": 0.001,
                "PeptideProphet Probability": 0.95,
            }
        ],
    )
    _write_mgf(search_dir / "spectra.mgf")

    result = run_ai_ready_real_smoke(
        search_dir=search_dir,
        task_types=["rt_prediction", "denovo"],
        output_dir=tmp_path / "real_smoke",
    )

    by_task = {item.task_type: item for item in result.task_results}
    assert result.status == "completed"
    assert by_task["rt_prediction"].status == "completed"
    assert by_task["denovo"].status == "completed"
    assert by_task["rt_prediction"].rows_out == 1
    assert by_task["denovo"].rows_out == 1
    assert (tmp_path / "real_smoke" / "real_smoke_summary.json").exists()
    assert (tmp_path / "real_smoke" / "discovery_feedback_preview.json").exists()


def test_real_smoke_blocks_peaklist_tasks_when_mgf_missing(tmp_path: Path):
    _write_tsv(
        tmp_path / "psm.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Spectrum": "scan=101", "PSM Q-Value": 0.001}],
    )

    result = run_ai_ready_real_smoke(
        search_dir=tmp_path,
        task_types=["fragment_intensity_prediction", "denovo", "ptm_denovo"],
        output_dir=tmp_path / "real_smoke",
    )

    assert result.status == "blocked"
    for item in result.task_results:
        assert item.status == "blocked"
        assert "needs_peaklist" in item.blockers


def test_real_smoke_cli_writes_summary(tmp_path: Path):
    _write_tsv(
        tmp_path / "peptide.tsv",
        [{"Peptide": "PEPTIDEK", "Charge": 2, "Retention": 12.5, "PSM Q-Value": 0.001}],
    )
    output_dir = tmp_path / "real_smoke"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "run-ai-ready-real-smoke",
            "--search-dir",
            str(tmp_path),
            "--task-type",
            "rt_prediction",
            "--output-dir",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "completed"
    assert (output_dir / "real_smoke_summary.json").exists()
    assert (output_dir / "real_smoke_report.md").exists()
