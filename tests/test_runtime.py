from __future__ import annotations

import json
import zipfile
from pathlib import Path

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


def test_detect_toolchain_reports_missing_java_and_docker_server(monkeypatch):
    monkeypatch.setattr("agent.runtime.toolchain.shutil.which", lambda cmd: {"docker": "docker.exe", "git": "git.exe"}.get(cmd))

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
    )

    assert bundle.plan.manifest_path.exists()
    assert bundle.materialized_workflow_path.exists()
    assert bundle.converter_config_path.exists()
    assert json.loads(bundle.converter_config_path.read_text(encoding="utf-8"))["generate_fragpipe_search_result"]["need"] is True
