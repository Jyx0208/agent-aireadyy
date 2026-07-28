from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from agent.ai_ready.psm_scoring_exporter import export_psm_scoring_ai_ready
from agent.ai_ready.release_predicates import (
    evaluate_export_science,
    evaluate_leakage_gate,
    evaluate_release,
    exporter_status_from_rows,
)
from agent.ai_ready.rt_exporter import export_rt_ai_ready


def test_exporter_status_from_rows_zero_not_completed():
    assert exporter_status_from_rows(0) == "export_empty"
    assert exporter_status_from_rows(3) == "completed"


def test_leakage_not_evaluated_blocks_pre_release():
    blockers = evaluate_leakage_gate({}, horizon="pre_release")
    assert "leakage_not_evaluated" in blockers

    decision = evaluate_release(
        horizon="pre_release",
        rows_out=10,
        parquet_exists=True,
        leakage_risk={"status": "not_evaluated"},
        task_type="denovo",
    )
    assert not decision.ok
    assert "leakage_not_evaluated" in decision.blockers


def test_leakage_warn_blocks_pre_release_not_ai_table_green():
    warn_report = {"status": "warn", "issue_counts": {"project": 1}}
    pre = evaluate_leakage_gate(warn_report, horizon="pre_release")
    assert any("leakage_warn" in b for b in pre)

    table = evaluate_release(
        horizon="ai_ready_table",
        rows_out=5,
        parquet_exists=True,
        leakage_risk=warn_report,
        task_type="denovo",
    )
    # warn does not block ai_ready_table in current API
    assert table.ok or "leakage_warn" not in table.blockers


def test_rt_unit_unknown_fails_science_contract(tmp_path: Path):
    search = tmp_path / "rt.tsv"
    pd.DataFrame(
        [
            {
                "Peptide": "PEPTIDEK",
                "Charge": 2,
                "Retention": 12.5,
                "Spectrum": "scan=1",
                "PSM Q-Value": 0.001,
            }
        ]
    ).to_csv(search, sep="	", index=False)
    result = export_rt_ai_ready([search], tmp_path / "out")
    assert result.rows_out == 1
    assert result.status == "completed"
    payload = json.loads(Path(result.report_json).read_text(encoding="utf-8"))
    assert payload["rt_unit_source"] in {"inferred_default", "unknown", "mixed"}
    science = evaluate_export_science("rt_prediction", export_report=payload)
    assert "rt_unit_unknown" in science.blockers
    assert not science.ok


def test_rt_unit_explicit_minute_passes_unit_gate(tmp_path: Path):
    search = tmp_path / "rt.tsv"
    pd.DataFrame(
        [
            {
                "Sequence": "PEPTIDEK",
                "Charge": 2,
                "Retention Time (min)": 11.0,
                "Spectrum ID": "scan=1",
                "PSM Q-Value": 0.001,
            }
        ]
    ).to_csv(search, sep="	", index=False)
    result = export_rt_ai_ready([search], tmp_path / "out")
    payload = json.loads(Path(result.report_json).read_text(encoding="utf-8"))
    assert payload["rt_unit_source"] == "column_explicit"
    science = evaluate_export_science("rt_prediction", export_report=payload)
    assert "rt_unit_unknown" not in science.blockers


def test_psm_no_decoy_fails_release(tmp_path: Path):
    search = tmp_path / "psm.tsv"
    pd.DataFrame(
        [
            {
                "Peptide": "PEPTIDEK",
                "Charge": 2,
                "Spectrum": "scan=1",
                "Decoy": "false",
                "Hyperscore": 40.0,
            }
        ]
    ).to_csv(search, sep="	", index=False)
    result = export_psm_scoring_ai_ready([search], tmp_path / "psm")
    assert result.rows_out == 1
    assert result.decoy_count == 0
    assert result.status == "completed"
    payload = json.loads(Path(result.report_json).read_text(encoding="utf-8"))
    science = evaluate_export_science("psm_scoring", export_report=payload)
    assert "psm_no_decoy" in science.blockers
    decision = evaluate_release(
        horizon="pre_release",
        rows_out=result.rows_out,
        parquet_exists=True,
        leakage_risk={"status": "pass"},
        task_type="psm_scoring",
        export_report=payload,
        integrity_status="verified",
    )
    assert not decision.ok
    assert "psm_no_decoy" in decision.blockers


def test_psm_target_and_decoy_pass_science(tmp_path: Path):
    search = tmp_path / "psm.tsv"
    pd.DataFrame(
        [
            {
                "Peptide": "PEPTIDEK",
                "Charge": 2,
                "Spectrum": "scan=1",
                "Decoy": "false",
                "Hyperscore": 40.0,
            },
            {
                "Peptide": "DECOYK",
                "Charge": 2,
                "Spectrum": "scan=2",
                "Decoy": "true",
                "Hyperscore": 5.0,
            },
        ]
    ).to_csv(search, sep="	", index=False)
    result = export_psm_scoring_ai_ready([search], tmp_path / "psm")
    assert result.target_count == 1
    assert result.decoy_count == 1
    payload = json.loads(Path(result.report_json).read_text(encoding="utf-8"))
    science = evaluate_export_science("psm_scoring", export_report=payload)
    assert science.ok
    assert not science.blockers


def test_zero_row_export_status_not_completed(tmp_path: Path):
    search = tmp_path / "emptyish.tsv"
    pd.DataFrame(
        [
            {
                "Peptide": "",
                "Charge": "",
                "Spectrum": "",
                "Decoy": "false",
            }
        ]
    ).to_csv(search, sep="	", index=False)
    result = export_psm_scoring_ai_ready([search], tmp_path / "psm")
    assert result.rows_out == 0
    assert result.status == "export_empty"
