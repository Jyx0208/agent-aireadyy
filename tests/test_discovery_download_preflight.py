from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.discovery.download_preflight import classify_pride_file_name, preflight_pride_download_candidates


def _file(name: str, size: int) -> dict[str, Any]:
    return {
        "fileName": name,
        "fileSizeBytes": size,
        "publicFileLocations": [{"value": f"https://ftp.pride.ebi.ac.uk/pride/data/archive/{name}"}],
    }


class FakePrideClient:
    def __init__(self) -> None:
        self.closed = False
        self.projects = {
            "PXD000900": {"accession": "PXD000900", "title": "Excluded old HeLa"},
            "PXD100001": {"accession": "PXD100001", "title": "Small mzML DDA phospho"},
            "PXD100002": {"accession": "PXD100002", "title": "Direct export pair"},
        }
        self.files = {
            "PXD000900": [_file("excluded.mzML", 10)],
            "PXD100001": [
                _file("small_sample.mzML", 100 * 1024 * 1024),
                _file("large_sample.raw", 900 * 1024 * 1024),
                _file("report.xlsx", 1000),
            ],
            "PXD100002": [
                _file("combined_psm.tsv", 1024),
                _file("spectra.mgf", 20 * 1024 * 1024),
            ],
        }

    def search_projects(self, keyword: str, page_size: int = 100) -> list[dict[str, Any]]:
        return [self.projects["PXD000900"], self.projects["PXD100001"], self.projects["PXD100002"]]

    def get_project(self, accession: str) -> dict[str, Any]:
        return self.projects[accession]

    def list_project_files(
        self,
        accession: str,
        keyword: str | None = None,
        page_size: int = 1000,
        max_files: int | None = None,
    ) -> list[dict[str, Any]]:
        records = self.files[accession]
        return records[:max_files] if max_files is not None else records

    def close(self) -> None:
        self.closed = True


def test_classify_pride_file_name_roles() -> None:
    assert classify_pride_file_name("sample.mzML") == "preferred_acquisition"
    assert classify_pride_file_name("sample.raw") == "acquisition"
    assert classify_pride_file_name("spectra.mgf") == "peaklist_mgf"
    assert classify_pride_file_name("combined_psm.tsv") == "search_result_table"
    assert classify_pride_file_name("result.mzid") == "search_result_mzid"
    assert classify_pride_file_name("report.xlsx") == "other_result_or_archive"


def test_preflight_pride_download_candidates_writes_safe_candidates(tmp_path: Path) -> None:
    client = FakePrideClient()

    result = preflight_pride_download_candidates(
        output_dir=tmp_path,
        queries=["phospho"],
        max_projects=5,
        max_files_per_project=10,
        max_file_mb=500,
        client=client,
    )

    assert result["status"] == "completed"
    assert result["projects_seen"] == 2
    assert result["small_download_candidates"] == 1
    assert result["direct_export_candidate_projects"] == 1
    assert "PXD000900" not in {project["project_accession"] for project in result["projects"]}

    small_csv = tmp_path / "pride_download_preflight_small_download_candidates.csv"
    assert small_csv.exists()
    assert "small_sample.mzML" in small_csv.read_text(encoding="utf-8")
    assert "large_sample.raw" not in small_csv.read_text(encoding="utf-8")

    assert (tmp_path / "pride_download_preflight.json").exists()
    assert (tmp_path / "pride_download_preflight_files.csv").exists()
