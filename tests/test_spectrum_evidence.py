from __future__ import annotations

from pathlib import Path

import pandas as pd

from agent.ai_ready.spectrum_evidence import profile_spectrum_evidence


def test_profile_spectrum_evidence_reads_mgf_activation(tmp_path: Path):
    mgf = tmp_path / "spectra.mgf"
    mgf.write_text(
        "\n".join(
            [
                "BEGIN IONS",
                "TITLE=scan=101",
                "SCANS=101",
                "ACTIVATION=HCD",
                "100.0 200.0",
                "END IONS",
                "",
            ]
        ),
        encoding="utf-8",
    )

    profile = profile_spectrum_evidence([mgf])

    assert profile.status == "completed"
    assert profile.fragmentation_methods == ["HCD"]
    assert profile.fragmentation_method_counts == {"HCD": 1}
    assert profile.fragmentation_evidence_level == "spectrum"


def test_profile_spectrum_evidence_truncates_mgf_scan(tmp_path: Path):
    mgf = tmp_path / "spectra.mgf"
    blocks = []
    for scan in range(3):
        blocks.extend(["BEGIN IONS", f"TITLE=scan={scan}", "ACTIVATION=CID", "100.0 200.0", "END IONS", ""])
    mgf.write_text("\n".join(blocks), encoding="utf-8")

    profile = profile_spectrum_evidence([mgf], max_spectra=1)

    assert profile.spectra_scanned == 1
    assert "spectrum_evidence_scan_truncated" in profile.warnings


def test_profile_spectrum_evidence_reads_parquet_fragmentation_column(tmp_path: Path):
    table = tmp_path / "msdt.parquet"
    pd.DataFrame(
        [
            {"scan": 101, "fragmentation_method": "HCD"},
            {"scan": 102, "activation_method": "CID"},
        ]
    ).to_parquet(table, index=False)

    profile = profile_spectrum_evidence([table])

    assert profile.fragmentation_method_counts == {"CID": 1, "HCD": 1}
    assert profile.fragmentation_evidence_level == "mixed"
