from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

from agent.decision.dda import plan_dda_execution
from agent.execution.bundle import materialize_dda_task_bundle
from agent.execution.workflow import materialize_workflow_with_attributes
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
        search_parameter_hints=AttributeValue(value={"precursor_tol": "20ppm", "recommended_workflow_name": "Default.workflow"}, confidence=0.6, source="rule", evidence_excerpt="profile", conflict_flag=False),
    )


def _reviewed_fasta(tmp_path: Path) -> Path:
    path = tmp_path / "reviewed_reference.fasta"
    path.write_text(">sp|P1|REVIEWED_TEST\nMPEPTIDEK\n", encoding="utf-8")
    return path


def _resolved_resolution() -> ProjectResolution:
    return ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["test exact match"],
            metadata_consistency=1.0,
        ),
        alternative_projects=[],
        resolution_reason="test exact match",
        resolution_confidence=1.0,
        needs_review=False,
    )


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


def test_detect_toolchain_reports_missing_pwiz_image(monkeypatch):
    monkeypatch.setattr(
        "agent.runtime.toolchain.shutil.which",
        lambda cmd: {
            "docker": "docker.exe",
            "git": "git.exe",
        }.get(cmd),
    )

    def fake_run(command, **_kwargs):
        class Result:
            returncode = 0
            stdout = "28.5.1|28.5.1"
            stderr = ""

        if command[1:3] == ["image", "inspect"]:
            Result.returncode = 1
            Result.stdout = ""
            Result.stderr = "No such image"
        return Result()

    monkeypatch.setattr("agent.runtime.toolchain.subprocess.run", fake_run)

    report = detect_toolchain()

    assert report.docker_daemon_available is True
    assert report.docker_pwiz_image_available is False


def test_detect_toolchain_uses_configured_msconvert(monkeypatch, tmp_path):
    executable = tmp_path / "msconvert.exe"
    executable.write_bytes(b"test")
    monkeypatch.setenv("AGENT_MSCONVERT_EXECUTABLE", str(executable))
    monkeypatch.setattr(
        "agent.runtime.toolchain.shutil.which",
        lambda cmd: {"git": "git.exe"}.get(cmd),
    )

    report = detect_toolchain()

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


def test_bootstrap_msdt_converter_from_zip_rejects_path_traversal(tmp_path: Path):
    zip_path = tmp_path / "bad-msdt.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../evil/convert.py", "print('bad')\n")

    destination_parent = tmp_path / "safe"
    destination_parent.mkdir()
    keep_file = destination_parent / "keep.txt"
    keep_file.write_text("keep", encoding="utf-8")

    try:
        bootstrap_msdt_converter_from_zip(zip_bytes=zip_path.read_bytes(), destination=destination_parent / "external")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal archive should be rejected")

    assert keep_file.read_text(encoding="utf-8") == "keep"


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
            "recommended_workflow_name": "Default.workflow",
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


def test_materialize_dda_task_bundle_applies_llm_workflow_parameter_overrides_for_multi_enzyme(tmp_path: Path):
    task = normalize_input("HeLa_ArgC-Try_CID_1.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file=task.file_name,
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
    context = ProjectContext(project_accession="PXD123456", file_name=task.file_name)
    attributes = _attributes()
    attributes.enzyme = AttributeValue(
        value="Trypsin and Arg-C",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="ArgC-Try file name",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "Default.workflow",
            "workflow_parameter_overrides": {
                "msfragger.misc.fragger.enzyme-dropdown-1": "stricttrypsin",
                "msfragger.search_enzyme_name_1": "stricttrypsin",
                "msfragger.search_enzyme_cut_1": "KR",
                "msfragger.search_enzyme_sense_1": "C",
                "msfragger.misc.fragger.enzyme-dropdown-2": "Arg-C",
                "msfragger.search_enzyme_name_2": "Arg-C",
                "msfragger.search_enzyme_cut_2": "R",
                "msfragger.search_enzyme_sense_2": "C",
                "msfragger.num_enzyme_termini": 2,
                "msfragger.allowed_missed_cleavage_1": 3,
                "msfragger.misc.fragger.digest-mass-lo": 600,
                "msfragger.misc.fragger.digest-mass-hi": 4000,
            },
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="LLM adjusted Default.workflow for Trypsin + Arg-C digest.",
        conflict_flag=False,
    )

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=attributes,
        source_data_path=tmp_path / "HeLa_ArgC-Try_CID_1.mzML",
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    workflow_text = bundle.materialized_workflow_path.read_text(encoding="utf-8")
    assert "msfragger.search_enzyme_name_1=stricttrypsin" in workflow_text
    assert "msfragger.search_enzyme_cut_1=KR" in workflow_text
    assert "msfragger.search_enzyme_name_2=Arg-C" in workflow_text
    assert "msfragger.search_enzyme_cut_2=R" in workflow_text
    assert "msfragger.num_enzyme_termini=2" in workflow_text
    assert "msfragger.allowed_missed_cleavage_1=3" in workflow_text
    assert "msfragger.misc.fragger.digest-mass-lo=600" in workflow_text
    assert "msfragger.misc.fragger.digest-mass-hi=4000" in workflow_text


def test_materialize_dda_task_bundle_keeps_multi_enzyme_dropdowns_consistent(tmp_path: Path):
    task = normalize_input("HeLa_ArgC-Try_CID_1.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file=task.file_name,
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
    context = ProjectContext(project_accession="PXD123456", file_name=task.file_name)
    attributes = _attributes()
    attributes.enzyme = AttributeValue(
        value="Arg-C; Trypsin",
        confidence=0.95,
        source="file_name",
        evidence_excerpt="HeLa_ArgC-Try_CID_1.raw",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "Default.workflow",
            "workflow_parameter_overrides": {
                "msfragger.search_enzyme_name_1": "stricttrypsin",
                "msfragger.search_enzyme_cut_1": "KR",
                "msfragger.search_enzyme_sense_1": "C",
                "msfragger.search_enzyme_name_2": "Arg-C",
                "msfragger.search_enzyme_cut_2": "R",
                "msfragger.search_enzyme_sense_2": "C",
                "msfragger.num_enzyme_termini": 2,
            },
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="LLM adjusted Default.workflow for Trypsin + Arg-C digest.",
        conflict_flag=False,
    )

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=attributes,
        source_data_path=tmp_path / "HeLa_ArgC-Try_CID_1.mzML",
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    workflow_text = bundle.materialized_workflow_path.read_text(encoding="utf-8")
    assert "msfragger.misc.fragger.enzyme-dropdown-1=stricttrypsin" in workflow_text
    assert "msfragger.search_enzyme_name_1=stricttrypsin" in workflow_text
    assert "msfragger.search_enzyme_cut_1=KR" in workflow_text
    assert "msfragger.misc.fragger.enzyme-dropdown-2=argc" in workflow_text
    assert "msfragger.search_enzyme_name_2=Arg-C" in workflow_text
    assert "msfragger.search_enzyme_cut_2=R" in workflow_text


def test_materialize_dda_task_bundle_derives_trypsin_lysc_dual_digest_overrides(tmp_path: Path):
    task = normalize_input("semantic_digest.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file=task.file_name,
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
    context = ProjectContext(project_accession="PXD123456", file_name=task.file_name)
    attributes = _attributes()
    attributes.enzyme = AttributeValue(
        value="Trypsin/Lys-C",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="Lysine-specific endoproteinase followed by trypsin.",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={"recommended_workflow_name": "Default.workflow", "missed_cleavages": 3},
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="Semantic dual digest inference.",
        conflict_flag=False,
    )

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=attributes,
        source_data_path=tmp_path / "semantic_digest.mzML",
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    workflow_text = bundle.materialized_workflow_path.read_text(encoding="utf-8")
    assert "msfragger.misc.fragger.enzyme-dropdown-1=stricttrypsin" in workflow_text
    assert "msfragger.search_enzyme_name_1=stricttrypsin" in workflow_text
    assert "msfragger.search_enzyme_cut_1=KR" in workflow_text
    assert "msfragger.search_enzyme_sense_1=C" in workflow_text
    assert "msfragger.misc.fragger.enzyme-dropdown-2=lysc" in workflow_text
    assert "msfragger.search_enzyme_name_2=lysc" in workflow_text
    assert "msfragger.search_enzyme_cut_2=K" in workflow_text
    assert "msfragger.search_enzyme_sense_2=C" in workflow_text
    assert "msfragger.num_enzyme_termini=2" in workflow_text
    assert "msfragger.allowed_missed_cleavage_1=3" in workflow_text


def test_workflow_parameter_overrides_ignore_unknown_msfragger_keys(tmp_path: Path):
    source = tmp_path / "Default.workflow"
    source.write_text(
        "\n".join(
            [
                "msfragger.allowed_missed_cleavage_1=2",
                "msfragger.search_enzyme_cut_2=",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    destination = tmp_path / "out.workflow"
    attributes = _attributes()
    attributes.search_parameter_hints = AttributeValue(
        value={
            "workflow_parameter_overrides": {
                "msfragger.allowed_missed_cleavage": 4,
                "msfragger.precursor_mass_tolerance": "10ppm",
                "msfragger.allowed_missed_cleavage_1": 3,
                "msfragger.search_enzyme_cut_2": "R",
            }
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="LLM proposed exact and inexact FragPipe keys.",
        conflict_flag=False,
    )

    materialize_workflow_with_attributes(source, destination, attributes)

    workflow_text = destination.read_text(encoding="utf-8")
    assert "msfragger.allowed_missed_cleavage=4" not in workflow_text
    assert "msfragger.precursor_mass_tolerance=10ppm" not in workflow_text
    assert "msfragger.allowed_missed_cleavage_1=3" in workflow_text
    assert "msfragger.search_enzyme_cut_2=R" in workflow_text


def test_workflow_parameter_overrides_strip_units_from_numeric_fragpipe_fields(tmp_path: Path):
    source = tmp_path / "Default.workflow"
    source.write_text(
        "\n".join(
            [
                "msfragger.fragment_mass_tolerance=20",
                "msfragger.fragment_mass_units=1",
                "msfragger.precursor_true_tolerance=20",
                "msfragger.precursor_true_units=1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    destination = tmp_path / "out.workflow"
    attributes = _attributes()
    attributes.search_parameter_hints = AttributeValue(
        value={
            "workflow_parameter_overrides": {
                "msfragger.fragment_mass_tolerance": "0.02Da",
                "msfragger.fragment_mass_units": "Da",
                "msfragger.precursor_true_tolerance": "10ppm",
                "msfragger.precursor_true_units": "ppm",
            }
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="LLM proposed tolerance values with units.",
        conflict_flag=False,
    )

    materialize_workflow_with_attributes(source, destination, attributes)

    workflow_text = destination.read_text(encoding="utf-8")
    assert "msfragger.fragment_mass_tolerance=0.02" in workflow_text
    assert "msfragger.fragment_mass_units=0" in workflow_text
    assert "msfragger.precursor_true_tolerance=10" in workflow_text
    assert "msfragger.precursor_true_units=1" in workflow_text
    assert "0.02Da" not in workflow_text
    assert "10ppm" not in workflow_text


def test_search_hint_missed_cleavages_accepts_common_range_text(tmp_path: Path):
    source = tmp_path / "Default.workflow"
    source.write_text("msfragger.allowed_missed_cleavage_1=2\n", encoding="utf-8")
    destination = tmp_path / "out.workflow"
    attributes = _attributes()
    attributes.search_parameter_hints = AttributeValue(
        value={
            "missed_cleavages": "3-4",
            "recommended_workflow_name": "Default.workflow",
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="Multi-enzyme digest recommendation.",
        conflict_flag=False,
    )

    materialize_workflow_with_attributes(source, destination, attributes)

    workflow_text = destination.read_text(encoding="utf-8")
    assert "msfragger.allowed_missed_cleavage_1=4" in workflow_text


def test_search_hint_missed_cleavages_ignores_unparseable_text(tmp_path: Path):
    source = tmp_path / "Default.workflow"
    source.write_text("msfragger.allowed_missed_cleavage_1=2\n", encoding="utf-8")
    destination = tmp_path / "out.workflow"
    attributes = _attributes()
    attributes.search_parameter_hints = AttributeValue(
        value={
            "missed_cleavages": "not specified",
            "recommended_workflow_name": "Default.workflow",
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="Ambiguous project metadata.",
        conflict_flag=False,
    )

    materialize_workflow_with_attributes(source, destination, attributes)

    workflow_text = destination.read_text(encoding="utf-8")
    assert "msfragger.allowed_missed_cleavage_1=2" in workflow_text
    assert "not specified" not in workflow_text


def test_materialize_dda_task_bundle_applies_configured_fragpipe_ram(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_FRAGPIPE_RAM_GB", "6")

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

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=_attributes(),
        source_data_path=tmp_path / "WT_5_Lys-c.mzML",
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    workflow_text = bundle.materialized_workflow_path.read_text(encoding="utf-8")
    assert "workflow.ram=6" in workflow_text


def test_materialize_dda_task_bundle_preserves_tmt_search_mods_and_disables_integrator_without_annotation(tmp_path: Path):
    task = normalize_input("20191002_EXP1_Evo1_AMV_TMT11prot_Rat_SetC_21min_46fracs_36.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD016662",
            matched_file=task.file_name,
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
    context = ProjectContext(project_accession="PXD016662", file_name=task.file_name)
    attributes = _attributes()
    attributes.species = AttributeValue(value="Rattus norvegicus", confidence=0.9, source="llm_confirmed", evidence_excerpt="Rat")
    attributes.labeling_strategy = AttributeValue(value="TMT", confidence=0.95, source="llm_confirmed", evidence_excerpt="TMT11prot")
    attributes.fixed_mods = AttributeValue(value=["C[57.02]"], confidence=0.8, source="llm_confirmed", evidence_excerpt="generic fixed mods")
    attributes.variable_mods = AttributeValue(value=["M[15.99]"], confidence=0.8, source="llm_confirmed", evidence_excerpt="generic variable mods")
    attributes.search_parameter_hints = AttributeValue(
        value={"recommended_workflow_name": "TMT10-bridge.workflow", "precursor_tol": "20ppm", "fragment_tol": "20ppm"},
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="TMT bridge",
        conflict_flag=False,
    )

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=attributes,
        source_data_path=tmp_path / f"{Path(task.file_name).stem}.mzML",
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    workflow_text = bundle.materialized_workflow_path.read_text(encoding="utf-8")
    assert "tmtintegrator.run-tmtintegrator=false" in workflow_text
    assert "229.16293,K (lysine),true,-1" in workflow_text
    assert "229.16293,n^,true,1" in workflow_text


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
        prefer_project_fasta=True,
    )

    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=_attributes(),
        source_data_path=tmp_path / "WT_5_Lys-c.mzML",
        output_dir=tmp_path / "task_out",
        prefer_project_fasta=True,
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
        prefer_project_fasta=True,
    )

    assert bundle.materialized_fasta_path.name == "project_reference.fasta"
    assert bundle.materialized_fasta_path.read_text(encoding="utf-8") == ">sp|P1|\nPEPTIDE\n"


def test_materialize_dda_task_bundle_rejects_downloaded_non_fasta_content(tmp_path: Path, monkeypatch):
    task = normalize_input("WT_5_Lys-c.raw")
    context = ProjectContext(project_accession="PXD123456", file_name="WT_5_Lys-c.raw")
    attributes = _attributes()
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "Default.workflow",
            "recommended_fasta_name": "bad.fasta",
            "recommended_fasta_url": "https://rest.uniprot.org/uniprotkb/stream?compressed=false&format=fasta&query=%28proteome%3AUP000005640%29",
            "recommended_fasta_source": "UniProt",
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="bad download",
        conflict_flag=False,
    )

    class FakePrideClient:
        def download_to_path(self, url, target_path, report=None):
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_path).write_text("<html>not a FASTA</html>\n", encoding="utf-8")
            return Path(target_path)

        def close(self):
            pass

    monkeypatch.setattr("agent.execution.bundle.PrideClient", FakePrideClient)

    try:
        materialize_dda_task_bundle(
            task=task,
            project_resolution=_resolved_resolution(),
            project_context=context,
            attributes=attributes,
            source_data_path=tmp_path / "WT_5_Lys-c.mzML",
            output_dir=tmp_path / "task_out",
        )
    except ValueError as exc:
        assert "不是有效 FASTA" in str(exc)
    else:
        raise AssertionError("non-FASTA download should be rejected before MSDT Docker runs")


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


