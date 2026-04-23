from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def export_ai_ready_bundle(
    msdt_path: str | Path,
    output_dir: str | Path,
    project_accession: str,
    source_file: str,
    attribute_evidence: dict[str, Any],
    decision_trace: dict[str, Any],
    run_manifest: dict[str, Any],
) -> Path:
    msdt_path = Path(msdt_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    frame = pd.read_parquet(msdt_path).copy()
    frame["project_accession"] = project_accession
    frame["source_file"] = source_file
    frame["attribute_evidence_json"] = json.dumps(attribute_evidence, ensure_ascii=False, sort_keys=True)
    frame["decision_trace_json"] = json.dumps(decision_trace, ensure_ascii=False, sort_keys=True)
    frame["run_manifest_json"] = json.dumps(run_manifest, ensure_ascii=False, sort_keys=True)

    output_path = output_dir / f"{msdt_path.stem}_ai_ready.parquet"
    frame.to_parquet(output_path)
    return output_path
