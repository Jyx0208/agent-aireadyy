from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from agent.ai_ready.agent_run_peaklist import generate_agent_run_peaklist
from agent.ai_ready.fragment_intensity_exporter import _load_peaklists, _match_spectrum
from agent.cli import app


def _write_msdt(path: Path, *, rows: int = 1, points: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        [
            {
                "scan": 101 + index,
                "charge": 2,
                "precursor_mz": 500.2 + index,
                "rt": 12.5 + index,
                "mz_array": [98.06004 + point for point in range(points)],
                "intensity_array": [1000.0 - point for point in range(points)],
                "precursor_sequence": "PEPTIDEK",
                "fragmentation_method": "HCD",
            }
            for index in range(rows)
        ]
    )
    frame.to_parquet(path, index=False)
    return path


def test_generate_agent_run_peaklist_from_msdt_parquet(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_msdt(run_dir / "msdt" / "sample_fp_msdt.parquet")

    result = generate_agent_run_peaklist(
        agent_run_dir=run_dir,
        output_dir=tmp_path / "peaklist",
        source="msdt",
    )

    assert result.status == "completed"
    assert result.spectra_written == 1
    peaklist = Path(result.peaklist_path or "")
    assert peaklist.exists()
    text = peaklist.read_text(encoding="utf-8")
    assert "TITLE=sample.00101.00101.2" in text
    assert "SCANS=101" in text
    assert "ACTIVATION=HCD" in text
    spectra, warnings = _load_peaklists([peaklist])
    assert not warnings
    assert _match_spectrum("sample.00101.00101.2", spectra) is not None
    assert _match_spectrum("sample.101.101.2", spectra) is not None


def test_generate_agent_run_peaklist_from_rawspectrum_tsv(tmp_path: Path):
    run_dir = tmp_path / "run"
    spectrum_tsv = run_dir / "sample_rawspectrum.tsv"
    spectrum_tsv.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "scan": "26911",
                "precursor_mz": "500.2",
                "precursor_charge": "3",
                "rt": "37.55",
                "mz_array": "100.1,200.2,300.3",
                "intensity_array": "1000,900,800",
            }
        ]
    ).to_csv(spectrum_tsv, sep="\t", index=False)

    result = generate_agent_run_peaklist(
        agent_run_dir=run_dir,
        output_dir=tmp_path / "peaklist",
        source="rawspectrum",
    )

    assert result.status == "completed"
    assert result.source == "rawspectrum"
    assert result.spectra_written == 1
    peaklist = Path(result.peaklist_path or "")
    text = peaklist.read_text(encoding="utf-8")
    assert "SCANS=26911" in text
    assert "CHARGE=3+" in text
    spectra, warnings = _load_peaklists([peaklist])
    assert not warnings
    assert _match_spectrum("controllerType=0 controllerNumber=1 scan=26911", spectra) is not None


def test_generate_agent_run_peaklist_respects_size_guard(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_msdt(run_dir / "msdt" / "sample_fp_msdt.parquet", rows=1)

    result = generate_agent_run_peaklist(
        agent_run_dir=run_dir,
        output_dir=tmp_path / "peaklist",
        source="msdt",
        max_output_mb=1,
    )

    assert result.status == "completed"

    _write_msdt(run_dir / "msdt" / "sample_fp_msdt.parquet", rows=1, points=40000)
    blocked = generate_agent_run_peaklist(
        agent_run_dir=run_dir,
        output_dir=tmp_path / "blocked",
        source="msdt",
        max_output_mb=1,
    )

    assert blocked.status == "blocked"
    assert any(item.startswith("estimated_output_too_large") for item in blocked.blockers)


def test_generate_agent_run_peaklist_cli_writes_reports(tmp_path: Path):
    run_dir = tmp_path / "run"
    _write_msdt(run_dir / "msdt" / "sample_fp_msdt.parquet")
    output_dir = tmp_path / "peaklist"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "generate-agent-run-peaklist",
            "--agent-run-dir",
            str(run_dir),
            "--output-dir",
            str(output_dir),
            "--source",
            "msdt",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "completed"
    assert Path(payload["peaklist_path"]).exists()
    assert (output_dir / "agent_run_peaklist_report.json").exists()
    assert (output_dir / "agent_run_peaklist_report.md").exists()
