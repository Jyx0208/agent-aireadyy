from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.pride_smoke_test as smoke


class FakeClient:
    def __init__(self):
        self.file_calls = []

    def search_projects(self, keyword: str, page_size: int = 100):
        return [
            {"accession": f"PXD_{keyword}_1"},
            {"accession": f"PXD_{keyword}_2"},
        ][:page_size]

    def list_project_files(self, accession: str, page_size: int = 1000, max_files: int | None = None, keyword: str | None = None):
        self.file_calls.append({"accession": accession, "page_size": page_size, "max_files": max_files, "keyword": keyword})
        return [
            {"fileName": f"{accession}_A.raw"},
            {"fileName": f"{accession}_B.txt"},
            {"fileName": f"{accession}_C.mzML"},
            {"fileName": f"{accession}_D.raw"},
        ]


def test_collect_pride_inputs_limits_projects_files_and_dedupes():
    client = FakeClient()

    inputs = smoke.collect_pride_inputs(
        client,
        keywords=["lfq", "tmt"],
        sample_size=3,
        projects_per_keyword=1,
        files_per_project=2,
    )

    assert inputs == ["PXD_lfq_1_A.raw", "PXD_lfq_1_C.mzML", "PXD_tmt_1_A.raw"]
    assert client.file_calls == [
        {"accession": "PXD_lfq_1", "page_size": 20, "max_files": 20, "keyword": None},
        {"accession": "PXD_tmt_1", "page_size": 20, "max_files": 20, "keyword": None},
    ]


def test_smoke_writer_uses_jsonl_and_summary_only_for_resolution_mode(tmp_path):
    writer = smoke.SmokeRunWriter(tmp_path, max_output_mb=1)
    writer.write_record({"input_file": "a.raw", "status": "resolved"})
    writer.write_error({"input_file": "b.raw", "status": "failed", "category": "timeout"})
    writer.write_summary({"total": 2, "status_counts": {"resolved": 1, "failed": 1}})

    assert json.loads((tmp_path / "records.jsonl").read_text(encoding="utf-8")) == {"input_file": "a.raw", "status": "resolved"}
    assert json.loads((tmp_path / "errors.jsonl").read_text(encoding="utf-8"))["category"] == "timeout"
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["total"] == 2
    assert not any(path.is_dir() for path in tmp_path.iterdir())


def test_smoke_writer_stops_when_output_budget_is_exceeded(tmp_path):
    writer = smoke.SmokeRunWriter(tmp_path, max_output_mb=0.0001)
    (tmp_path / "large.txt").write_text("x" * 1024, encoding="utf-8")

    with pytest.raises(smoke.DiskBudgetExceeded):
        writer.ensure_budget()
