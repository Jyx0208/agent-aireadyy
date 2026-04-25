from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

from agent.decision.dda import plan_dda_execution
from agent.execution.bundle import materialize_dda_task_bundle
from agent.input.normalizer import normalize_input
from agent.models import (
    AttributeSet,
    AttributeValue,
    MetadataValue,
    ProjectCandidate,
    ProjectContext,
    ProjectResolution,
)
from agent.runtime.bootstrap import bootstrap_msdt_converter_from_zip
from agent.runtime.toolchain import detect_toolchain


def _attributes() -> AttributeSet:
    return AttributeSet(
        acquisition_mode=AttributeValue(value="DDA", confidence=1.0, source="sdrf", evidence_excerpt="DDA", conflict_flag=False),
        species=AttributeValue(value="Homo sapiens", confidence=0.9, source="sdrf", evidence_excerpt="human", conflict_flag=False),
        instrument_name=AttributeValue(value="Orbitrap Fusion Lumos", confidence=0.9, source="sdrf", evidence_excerpt="instrument", conflict_flag=False),
        instrument_family=AttributeValue(value="orbitrap", confidence=0.9, source="rule", evidence_excerpt="instrument family", conflict_flag=False),
        enzyme=AttributeValue(value="Lys-C", confidence=0.9, source="file_name_rule", evidence_excerpt="WT_5_Lys-c.raw", conflict_flag=False),
        labeling_strategy=AttributeValue(value="label-free", confidence=0.8, source="default", evidence_excerpt="default", conflict_flag=False),
        fixed_mods=AttributeValue(value=["C[57.02]"], confidence=0.7, source="default", evidence_excerpt="mods", conflict_flag=False),
        variable_mods=AttributeValue(value=["M[15.99]"], confidence=0.7, source="default", evidence_excerpt="mods", conflict_flag=False),
        fractionation_hint=AttributeValue(value=None, confidence=0.0, source="none", evidence_excerpt="", conflict_flag=False),
        search_parameter_hints=AttributeValue(value={"precursor_tol": "20ppm"}, confidence=0.6, source="rule", evidence_excerpt="profile", conflict_flag=False),
    )


def _reviewed_fasta(tmp_path: Path) -> Path:
    path = tmp_path / "reviewed_reference.fasta"
    path.write_text(">sp|P1|REVIEWED_TEST\nMPEPTIDEK\n", encoding="utf-8")
    return path


def test_detect_toolchain_reports_missing_java_and_docker_server(monkeypatch):
    monkeypatch.setattr(
        "agent.runtime.toolchain.shutil.which",
        lambda cmd: {"docker": "docker.exe", "git": "git.exe", "msconvert": "msconvert.exe"}.get(cmd),
    )

    def fake_run(*args, **kwargs):
        class Result:
            returncode = 1
            stdout = "29.3.1|"
            stderr = "daemon missing"

        return Result()

    monkeypatch.setattr("agent.runtime.toolchain.subprocess.run", fake_run)

    report = detect_toolchain()

    assert report.git_available is True
    assert report.docker_cli_available is True
    assert report.docker_daemon_available is False
    assert report.java_available is False
    assert report.msconvert_available is True


def test_bootstrap_msdt_converter_from_zip_extracts_repo(tmp_path: Path):
    zip_path = tmp_path / "msdt.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("MSDT-Converter-main/convert.py", "print('ok')\n")
        zf.writestr("MSDT-Converter-main/scripts/generate_msdt.py", "print('script')\n")

    destination = tmp_path / "external"
    repo_root = bootstrap_msdt_converter_from_zip(zip_bytes=zip_path.read_bytes(), destination=destination)

    assert repo_root.name == "MSDT-Converter"
    assert (repo_root / "convert.py").exists()
    assert (repo_root / "scripts" / "generate_msdt.py").exists()


def test_materialize_dda_task_bundle_writes_runtime_files(tmp_path: Path):
    task = normalize_input("WT_5_Lys-c.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact"],
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
                value="Lys-C digest, Orbitrap Fusion Lumos, Homo sapiens.",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.9,
            )
        },
    )

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=_attributes(),
        source_data_path=tmp_path / "WT_5_Lys-c.mzML",
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    assert not bundle.plan.manifest_path.exists()
    assert bundle.materialized_workflow_path.exists()
    assert bundle.materialized_workflow_path.parent.name == "workflows"
    assert bundle.materialized_fasta_path.exists()
    sage_config = json.loads((tmp_path / "task_out" / "sage" / "sage_config.json").read_text(encoding="utf-8"))
    assert sage_config["database"]["enzyme"]["cleave_at"] == "K"
    assert bundle.converter_config_path.exists()
    assert json.loads(bundle.converter_config_path.read_text(encoding="utf-8"))["generate_fragpipe_search_result"]["need"] is True


def test_materialize_dda_task_bundle_copies_external_source_into_task_root(tmp_path: Path):
    task = normalize_input("WT_5_Lys-c.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact"],
            metadata_consistency=1.0,
        ),
        alternative_projects=[],
        resolution_reason="exact match",
        resolution_confidence=1.0,
        needs_review=False,
    )
    context = ProjectContext(project_accession="PXD123456", file_name="WT_5_Lys-c.raw")
    source_path = tmp_path / "external_data" / "WT_5_Lys-c.mzML"
    source_path.parent.mkdir()
    source_path.write_text("<mzML />", encoding="utf-8")

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=_attributes(),
        source_data_path=source_path,
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    assert bundle.plan.source_data_path == tmp_path / "task_out" / "input" / "WT_5_Lys-c.mzML"
    assert bundle.plan.source_data_path.read_text(encoding="utf-8") == "<mzML />"


def test_materialize_dda_task_bundle_applies_fragpipe_workflow_attributes(tmp_path: Path):
    task = normalize_input("WT_5_Lys-c.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact"],
            metadata_consistency=1.0,
        ),
        alternative_projects=[],
        resolution_reason="exact match",
        resolution_confidence=1.0,
        needs_review=False,
    )
    context = ProjectContext(project_accession="PXD123456", file_name="WT_5_Lys-c.raw")
    attributes = _attributes()
    attributes.search_parameter_hints = AttributeValue(
        value={
            "precursor_mass_tolerance": "10 ppm",
            "fragment_mass_tolerance": "0.5 Da",
            "missed_cleavages": 2,
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="Mascot search used 10 ppm precursor, 0.5 Da fragment, 2 missed cleavages.",
        conflict_flag=False,
    )
    attributes.fixed_mods = AttributeValue(
        value=[],
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="No fixed modifications listed.",
        conflict_flag=False,
    )
    attributes.variable_mods = AttributeValue(
        value=[
            "Carbamidomethyl (C)",
            "Acetyl (Protein N-term)",
            "Formyl (N-term)",
            "Oxidation (M)",
            "Deamidated (NQ)",
            "Pyro-Glu (N-term Q)",
        ],
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="Variable modifications confirmed.",
        conflict_flag=False,
    )

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=attributes,
        source_data_path=tmp_path / "WT_5_Lys-c.mzML",
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    source_workflow = bundle.plan.fragpipe_workflow_path.read_text(encoding="utf-8")
    workflow_text = bundle.materialized_workflow_path.read_text(encoding="utf-8")

    assert workflow_text != source_workflow
    assert "msfragger.search_enzyme_name_1=lysc" in workflow_text
    assert "msfragger.precursor_mass_lower=-10" in workflow_text
    assert "msfragger.fragment_mass_units=0" in workflow_text


def test_materialize_dda_task_bundle_applies_aspn_to_fragpipe_workflow(tmp_path: Path):
    task = normalize_input("ASP-N_F4-R1.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="ASP-N_F4-R1.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact"],
            metadata_consistency=1.0,
        ),
        alternative_projects=[],
        resolution_reason="exact match",
        resolution_confidence=1.0,
        needs_review=False,
    )
    context = ProjectContext(project_accession="PXD123456", file_name="ASP-N_F4-R1.raw")
    attributes = _attributes()
    attributes.enzyme = AttributeValue(value="Asp-N", confidence=0.98, source="llm_confirmed", evidence_excerpt="ASP-N")

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=attributes,
        source_data_path=tmp_path / "ASP-N_F4-R1.mzML",
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    workflow_text = bundle.materialized_workflow_path.read_text(encoding="utf-8")
    assert "msfragger.search_enzyme_name_1=aspn" in workflow_text
    assert "msfragger.search_enzyme_cut_1=D" in workflow_text
    assert "msfragger.search_enzyme_sense_1=N" in workflow_text


def test_materialize_dda_task_bundle_downloads_reproduced_project_fasta(tmp_path: Path, monkeypatch):
    task = normalize_input("WT_5_Lys-c.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact"],
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
        project_files=[
            {
                "fileName": "project_reference.fasta",
                "publicFileLocations": [
                    {"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD123456/project_reference.fasta"}
                ],
            }
        ],
    )

    class FakePrideClient:
        def __init__(self):
            self.closed = False

        @staticmethod
        def first_download_url(file_record):
            return "https://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD123456/project_reference.fasta"

        def download_to_path(self, url, target_path, report=None):
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_path).write_text(">sp|P1|\nPEPTIDE\n", encoding="utf-8")
            return Path(target_path)

        def close(self):
            self.closed = True

    monkeypatch.setattr("agent.execution.bundle.PrideClient", FakePrideClient)

    plan = plan_dda_execution(
        task_id="task-project-fasta-bundle",
        source_file_name="WT_5_Lys-c.raw",
        source_data_path=tmp_path / "WT_5_Lys-c.mzML",
        project_resolution=resolution,
        project_context=context,
        attributes=_attributes(),
        output_dir=tmp_path / "task_out",
    )

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=_attributes(),
        source_data_path=tmp_path / "WT_5_Lys-c.mzML",
        output_dir=tmp_path / "task_out",
    )

    assert plan.fasta_selection_mode == "reproduced"
    assert bundle.materialized_fasta_path.exists()
    assert bundle.materialized_fasta_path.name == "project_reference.fasta"


def test_materialize_dda_task_bundle_decompresses_reproduced_project_fasta_gz(tmp_path: Path, monkeypatch):
    task = normalize_input("WT_5_Lys-c.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact"],
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
        project_files=[
            {
                "fileName": "project_reference.fasta.gz",
                "publicFileLocations": [
                    {"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD123456/project_reference.fasta.gz"}
                ],
            }
        ],
    )

    class FakePrideClient:
        @staticmethod
        def first_download_url(file_record):
            return "https://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD123456/project_reference.fasta.gz"

        def download_to_path(self, url, target_path, report=None):
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_path).write_bytes(gzip.compress(b">sp|P1|\nPEPTIDE\n"))
            return Path(target_path)

        def close(self):
            pass

    monkeypatch.setattr("agent.execution.bundle.PrideClient", FakePrideClient)

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=_attributes(),
        source_data_path=tmp_path / "WT_5_Lys-c.mzML",
        output_dir=tmp_path / "task_out",
    )

    assert bundle.materialized_fasta_path.name == "project_reference.fasta"
    assert bundle.materialized_fasta_path.read_text(encoding="utf-8") == ">sp|P1|\nPEPTIDE\n"


def _minimal_plan(tmp_path: Path, workflow_path: Path):
    from agent.models import DdaExecutionPlan

    return DdaExecutionPlan(
        task_id="task-001",
        source_file_name="sample.raw",
        source_data_path=tmp_path / "sample.mzML",
        raw_data_type="mzml",
        fasta_path=tmp_path / "reference.fasta",
        fasta_selection_mode="defaulted",
        fragpipe_workflow_path=workflow_path,
        manifest_path=tmp_path / "fragpipe" / "fragpipe-files.fp-manifest",
        converter_config_path=tmp_path / "converter_config.json",
        rawspectrum_output_path=tmp_path / "rawspectrum" / "sample_rawspectrum.parquet",
        fragpipe_workdir=tmp_path / "fragpipe",
        expected_pin_path=tmp_path / "fragpipe" / "exp" / "sample.mzML_edited.pin",
        expected_pin_glob=str(tmp_path / "fragpipe" / "exp" / "sample.mzML_edited.pin"),
        output_paths={
            "fp_msdt": tmp_path / "msdt" / "sample_fp_msdt.parquet",
            "ai_ready": tmp_path / "ai_ready" / "sample_ai_ready.parquet",
            "run_log": tmp_path / "logs" / "run.log",
        },
        needs_review=False,
    )


