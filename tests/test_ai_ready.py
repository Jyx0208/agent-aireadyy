from pathlib import Path

import pandas as pd

from agent.ai_ready.exporter import export_ai_ready_bundle


def test_export_ai_ready_bundle_adds_provenance_columns(tmp_path: Path):
    msdt_path = tmp_path / "input_msdt.parquet"
    df = pd.DataFrame(
        {
            "scan": [1001],
            "precursor_sequence": [["PEPTIDE"]],
            "proteins": [["P12345"]],
            "label": [[1]],
            "precursor_mz": [500.2],
            "rt": [12.5],
            "mz_array": [[100.0, 200.0]],
            "intensity_array": [[1000.0, 2000.0]],
        }
    )
    df.to_parquet(msdt_path)

    out = export_ai_ready_bundle(
        msdt_path=msdt_path,
        output_dir=tmp_path / "ai_ready",
        project_accession="PXD123456",
        source_file="sample.raw",
        attribute_evidence={"acquisition_mode": "DDA"},
        decision_trace={"workflow": "Default.workflow"},
        run_manifest={"task_id": "task-004"},
    )

    exported = pd.read_parquet(out)
    assert exported.loc[0, "project_accession"] == "PXD123456"
    assert exported.loc[0, "source_file"] == "sample.raw"
    assert exported.loc[0, "attribute_evidence_json"] == '{"acquisition_mode": "DDA"}'
