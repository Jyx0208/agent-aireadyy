from __future__ import annotations

import subprocess
from pathlib import Path
import json

import pandas as pd

from agent.input.normalizer import normalize_input
from agent.models import (
    AttributeSet,
    AttributeValue,
    FileAsset,
    MetadataValue,
    ProjectCandidate,
    ProjectContext,
    ProjectResolution,
)
from agent.orchestrator.pipeline import AgentService


class _DummyReasoner:
    def confirm_search_parameters(self, context, attributes):
        return {}
from agent.execution.bundle import materialize_dda_task_bundle
from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner


def _attributes() -> AttributeSet:
    return AttributeSet(
        acquisition_mode=AttributeValue(value="DDA", confidence=1.0, source="rule", evidence_excerpt="DDA", conflict_flag=False),
        species=AttributeValue(value="Homo sapiens", confidence=0.9, source="rule", evidence_excerpt="human", conflict_flag=False),
        instrument_name=AttributeValue(value="Orbitrap Fusion Lumos", confidence=0.9, source="rule", evidence_excerpt="instrument", conflict_flag=False),
        instrument_family=AttributeValue(value="orbitrap", confidence=0.9, source="rule", evidence_excerpt="family", conflict_flag=False),
        enzyme=AttributeValue(value="Lys-C", confidence=0.9, source="file_name_rule", evidence_excerpt="WT_5_Lys-c.raw", conflict_flag=False),
        labeling_strategy=AttributeValue(value="label-free", confidence=0.8, source="default", evidence_excerpt="default", conflict_flag=False),
        fixed_mods=AttributeValue(value=["C[57.02]"], confidence=0.7, source="default", evidence_excerpt="mods", conflict_flag=False),
        variable_mods=AttributeValue(value=["M[15.99]"], confidence=0.7, source="default", evidence_excerpt="mods", conflict_flag=False),
        fractionation_hint=AttributeValue(value=None, confidence=0.0, source="none", evidence_excerpt="", conflict_flag=False),
        search_parameter_hints=AttributeValue(value={"precursor_tol": "20ppm", "recommended_workflow_name": "Default.workflow"}, confidence=0.6, source="rule", evidence_excerpt="profile", conflict_flag=False),
    )


def _reviewed_fasta(tmp_path: Path) -> Path:
    path = tmp_path / "reviewed_reference.fasta"
    path.write_text(">sp|P1|REVIEWED_TEST\nMPEPTIDEK\n", encoding="utf-8")
    return path


def _write_minimal_dda_mzml(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mzML xmlns="http://psi.hupo.org/ms/mzml">
  <run id="run1">
    <spectrumList count="2">
      <spectrum id="scan=1">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="1"/>
      </spectrum>
      <spectrum id="scan=2">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="2"/>
      </spectrum>
    </spectrumList>
  </run>
</mzML>
""",
        encoding="utf-8",
    )


def test_run_pride_dda_msdt_docker_executes_runner(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None, llm_reasoner=_DummyReasoner())
    task = normalize_input("WT_5_Lys-c.raw")

    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact match"],
            metadata_consistency=1.0,
        ),
        alternative_projects=[],
        resolution_reason="exact match",
        resolution_confidence=1.0,
        needs_review=False,
    )
    context = ProjectContext(
        project_accession="PXD123456",
        file_name="WT_5_Lys-c.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="Lys-C Orbitrap human proteomics",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            )
        },
        project_files=[],
    )
    asset = FileAsset(
        original_file_name="WT_5_Lys-c.raw",
        resolved_asset_type="mzml",
        matched_project_file="WT_5_Lys-c.mzML",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.mzML",
        local_path=tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.mzML",
        prepared_path=tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.mzML",
        requires_conversion=False,
        asset_confidence=1.0,
        match_type="stem",
    )

    monkeypatch.setattr(service, "resolve_project", lambda *_: resolution)
    monkeypatch.setattr(service, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(service, "infer_attributes", lambda *_: _attributes())
    monkeypatch.setattr(service, "resolve_asset", lambda *args, **kwargs: asset)

    def fake_prepare_asset(asset_to_prepare, converter=None):
        _write_minimal_dda_mzml(asset_to_prepare.local_path)
        return asset_to_prepare.local_path

    monkeypatch.setattr(service, "prepare_asset", fake_prepare_asset)

    called = {}

    class FakeDockerRunner:
        def __init__(self, image: str, report=None):
            called["image"] = image
            called["report"] = report

        def run(self, bundle):
            called["bundle"] = bundle
            bundle.plan.rawspectrum_output_path.parent.mkdir(parents=True, exist_ok=True)
            bundle.plan.rawspectrum_output_path.write_text("rawspectrum", encoding="utf-8")
            bundle.plan.expected_pin_path.parent.mkdir(parents=True, exist_ok=True)
            bundle.plan.expected_pin_path.write_text("pin", encoding="utf-8")
            bundle.plan.output_paths["fp_msdt"].parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"scan": [1], "label": ["PEPTIDE"]}).to_parquet(bundle.plan.output_paths["fp_msdt"])
            return subprocess.CompletedProcess(args=["docker"], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent.orchestrator.pipeline.DockerMSDTConverterRunner", FakeDockerRunner)

    manifest = service.run_pride_dda_msdt_docker(
        task=task,
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    assert manifest.status == "completed"
    assert called["image"] == "guomics2017/msdt-converter:v1.3"
    assert called["bundle"].converter_config_path.exists()
    assert Path(manifest.outputs["fp_msdt"]).exists()
    assert Path(manifest.outputs["ai_ready"]).exists()
    assert Path(manifest.outputs["run_log"]).read_text(encoding="utf-8") == "ok"


def test_run_pride_dda_msdt_docker_marks_failed_when_msdt_output_missing(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None, llm_reasoner=_DummyReasoner())
    task = normalize_input("WT_5_Lys-c.raw")

    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact match"],
            metadata_consistency=1.0,
        ),
        alternative_projects=[],
        resolution_reason="exact match",
        resolution_confidence=1.0,
        needs_review=False,
    )
    context = ProjectContext(
        project_accession="PXD123456",
        file_name="WT_5_Lys-c.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="Lys-C Orbitrap human proteomics",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            )
        },
        project_files=[],
    )
    asset = FileAsset(
        original_file_name="WT_5_Lys-c.raw",
        resolved_asset_type="mzml",
        matched_project_file="WT_5_Lys-c.mzML",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.mzML",
        local_path=tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.mzML",
        prepared_path=tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.mzML",
        requires_conversion=False,
        asset_confidence=1.0,
        match_type="stem",
    )

    monkeypatch.setattr(service, "resolve_project", lambda *_: resolution)
    monkeypatch.setattr(service, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(service, "infer_attributes", lambda *_: _attributes())
    monkeypatch.setattr(service, "resolve_asset", lambda *args, **kwargs: asset)

    def fake_prepare_asset(asset_to_prepare, converter=None):
        _write_minimal_dda_mzml(asset_to_prepare.local_path)
        return asset_to_prepare.local_path

    monkeypatch.setattr(service, "prepare_asset", fake_prepare_asset)

    class FakeDockerRunner:
        def __init__(self, image: str, report=None):
            pass

        def run(self, bundle):
            return subprocess.CompletedProcess(args=["docker"], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("agent.orchestrator.pipeline.DockerMSDTConverterRunner", FakeDockerRunner)

    manifest = service.run_pride_dda_msdt_docker(
        task=task,
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    assert manifest.status == "failed"
    assert any("Missing required output" in note and "MSDT parquet" in note for note in manifest.notes)


def test_docker_runner_uses_materialized_workflow_path_in_config(tmp_path: Path):
    task = normalize_input("WT_5_Lys-c.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact match"],
            metadata_consistency=1.0,
        ),
        alternative_projects=[],
        resolution_reason="exact match",
        resolution_confidence=1.0,
        needs_review=False,
    )
    context = ProjectContext(
        project_accession="PXD123456",
        file_name="WT_5_Lys-c.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="Lys-C Orbitrap human proteomics",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            )
        },
        project_files=[],
    )
    source_data_path = tmp_path / "task_out" / "assets" / "prepared" / "WT_5_Lys-c.mzML"
    _write_minimal_dda_mzml(source_data_path)

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=_attributes(),
        source_data_path=source_data_path,
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )
    runner = DockerMSDTConverterRunner()

    config_path = runner.write_container_config(bundle)
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config["generate_fragpipe_search_result"]["workflow_path"].startswith("/workspace/workflows/")
    assert config["generate_fragpipe_search_result"]["workflow_path"] != "/workspace/fragpipe/Default.workflow"
