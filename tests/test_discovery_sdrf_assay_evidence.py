from __future__ import annotations

from typing import Any

from agent.discovery.models import DatasetRequest
from agent.discovery.pride_discovery import discover_pride_dataset
from agent.metadata.context import load_sdrf_rows


class _SdrfAssayPrideClient:
    def __init__(self, sdrf_text: str, *, file_name: str = "sample_01.raw") -> None:
        self.sdrf_text = sdrf_text
        self.file_name = file_name
        self.project = {
            "accession": "PXD055544",
            "title": "Human class I immunopeptidomics",
            "projectDescription": "HLA class I ligandome acquired by DDA HCD.",
            "sampleProcessingProtocol": "HLA immunoprecipitation",
            "dataProcessingProtocol": "DDA HCD acquisition",
            "keywords": ["immunopeptidomics", "HLA ligandome", "DDA"],
            "experimentTypes": [{"name": "shotgun proteomics"}],
            "organisms": [{"name": "Homo sapiens"}],
            "instruments": [{"name": "Orbitrap Exploris 480"}],
        }
        self.raw_file = {
            "fileName": file_name,
            "fileSizeBytes": 1024,
            "publicFileLocations": [{"value": f"https://example.test/{file_name}"}],
        }
        self.sdrf_file = {
            "fileName": "PXD055544.sdrf.csv",
            "fileSizeBytes": 256,
            "publicFileLocations": [{"value": "https://example.test/PXD055544.sdrf.csv"}],
        }

    def get_project(self, _accession: str) -> dict[str, Any]:
        return self.project

    def list_project_files(
        self,
        _accession: str,
        *,
        keyword: str | None = None,
        max_files: int | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        files = [self.sdrf_file] if keyword == "sdrf" else [self.raw_file, self.sdrf_file]
        return files if max_files is None else files[:max_files]

    def download_text(self, _url: str) -> str:
        return self.sdrf_text


def _discover_file(sdrf_text: str, *, file_name: str = "sample_01.raw"):
    client = _SdrfAssayPrideClient(sdrf_text, file_name=file_name)
    request = DatasetRequest(
        goal="immunopeptidomics",
        species=["human"],
        max_projects=1,
        max_files=4,
        max_files_per_project=4,
    )
    manifest = discover_pride_dataset(
        request,
        client=client,  # type: ignore[arg-type]
        candidate_records=[client.project],
    )
    return next(file for file in manifest.files if file.file_name == file_name)


def test_load_sdrf_rows_detects_csv_delimiter() -> None:
    rows = load_sdrf_rows(
        "source name,comment[data file],assay\n"
        "sample-1,PXD073612_sample_01.raw,Immunopeptidomics_class_I\n"
    )

    assert rows == [
        {
            "source name": "sample-1",
            "comment[data file]": "PXD073612_sample_01.raw",
            "assay": "Immunopeptidomics_class_I",
        }
    ]


def test_matched_immunopeptidomics_assay_becomes_auditable_file_evidence() -> None:
    file = _discover_file(
        "source name,comment[data file],assay,comment[instrument],"
        "comment[fragmentation method],comment[data acquisition method]\n"
        "sample-1,sample_01.raw,Immunopeptidomics_class_I,"
        "Orbitrap Exploris 480,HCD,DDA\n"
    )

    assay_evidence = [item for item in file.evidence if item.source == "immunopeptidomics"]
    assert file.sdrf_match_status == "matched"
    assert any(
        item.field == "sdrf:assay" and item.text == "Immunopeptidomics_class_I"
        for item in assay_evidence
    )
    assert "strong_immunopeptide_evidence" in file.validity_reasons
    assert "project_level_immunopeptide_evidence" not in file.validity_reasons


def test_unmatched_sdrf_assay_keeps_file_in_review() -> None:
    file = _discover_file(
        "source name,comment[data file],assay\n"
        "sample-elsewhere,another_file.raw,Immunopeptidomics_class_I\n",
        file_name="immunopeptidomics_sample_01.raw",
    )

    assert file.sdrf_match_status == "no_file_match"
    # sdrf_no_file_match is soft under Wave A: domain evidence may yield weak_keep.
    assert file.validity_status in {"needs_review", "weak_keep"}
    assert "sdrf_no_file_match" in file.validity_reasons
    assert not any(
        item.source == "immunopeptidomics" and item.field.startswith("sdrf:")
        for item in file.evidence
    )


def test_conflicting_matched_sdrf_assays_keep_file_in_review() -> None:
    file = _discover_file(
        "source name,comment[data file],assay,comment[instrument],"
        "comment[fragmentation method],comment[data acquisition method]\n"
        "sample-1,sample_01.raw,Immunopeptidomics_class_I,"
        "Orbitrap Exploris 480,HCD,DDA\n"
        "sample-2,sample_01.raw,Shotgun_proteomics,"
        "Orbitrap Exploris 480,HCD,DDA\n"
    )

    assert file.sdrf_match_status == "matched"
    assert file.validity_status == "needs_review"
    assert file.needs_review is True
    assert "conflicting_sdrf_assay_evidence" in file.validity_reasons
    assert any(item.source == "sdrf_assay_conflict" for item in file.evidence)
    assert not any(
        item.source == "immunopeptidomics" and item.field.startswith("sdrf:")
        for item in file.evidence
    )
