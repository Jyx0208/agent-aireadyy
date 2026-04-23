from pathlib import Path

from agent.decision.dda import plan_dda_execution
from agent.models import (
    AttributeSet,
    AttributeValue,
    MetadataValue,
    ProjectContext,
    ProjectResolution,
)


def _dda_attributes() -> AttributeSet:
    return AttributeSet(
        acquisition_mode=AttributeValue(
            value="DDA",
            confidence=1.0,
            source="sdrf",
            evidence_excerpt="DDA",
            conflict_flag=False,
        ),
        species=AttributeValue(
            value="Homo sapiens",
            confidence=0.9,
            source="sdrf",
            evidence_excerpt="Homo sapiens",
            conflict_flag=False,
        ),
        instrument_name=AttributeValue(
            value="Orbitrap Fusion Lumos",
            confidence=0.9,
            source="sdrf",
            evidence_excerpt="Orbitrap Fusion Lumos",
            conflict_flag=False,
        ),
        instrument_family=AttributeValue(
            value="orbitrap",
            confidence=0.9,
            source="rule",
            evidence_excerpt="Orbitrap Fusion Lumos",
            conflict_flag=False,
        ),
        enzyme=AttributeValue(
            value="Trypsin",
            confidence=0.9,
            source="sdrf",
            evidence_excerpt="Trypsin",
            conflict_flag=False,
        ),
        labeling_strategy=AttributeValue(
            value="label-free",
            confidence=0.7,
            source="default",
            evidence_excerpt="no labeling keywords found",
            conflict_flag=False,
        ),
        fixed_mods=AttributeValue(
            value=["C[57.02]"],
            confidence=0.7,
            source="default",
            evidence_excerpt="trypsin default fixed mods",
            conflict_flag=False,
        ),
        variable_mods=AttributeValue(
            value=["M[15.99]"],
            confidence=0.7,
            source="default",
            evidence_excerpt="oxidation default",
            conflict_flag=False,
        ),
        fractionation_hint=AttributeValue(
            value=None,
            confidence=0.0,
            source="none",
            evidence_excerpt="",
            conflict_flag=False,
        ),
        search_parameter_hints=AttributeValue(
            value={"precursor_tol": "20ppm"},
            confidence=0.6,
            source="rule",
            evidence_excerpt="Orbitrap default profile",
            conflict_flag=False,
        ),
    )


def test_plan_dda_execution_generates_converter_compatible_paths(tmp_path: Path):
    attributes = _dda_attributes()
    plan = plan_dda_execution(
        task_id="task-001",
        source_file_name="sample.raw",
        source_data_path="/data/sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.raw_data_type == "mzml"
    assert plan.fasta_path.name == "Homo_sapiens_reviewed.fasta"
    assert plan.fragpipe_workflow_path.name == "LFQ_DDA_human_noNQ.workflow"
    assert plan.manifest_path.name == "fragpipe-files.fp-manifest"
    assert plan.expected_pin_glob.endswith("sample.mzML_edited.pin")
    assert plan.output_paths["fp_msdt"].suffix == ".parquet"


def test_plan_dda_execution_maps_mouse_species_alias_to_mouse_fasta(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Mus musculus (mouse)",
        confidence=0.9,
        source="pride.organisms",
        evidence_excerpt="Mus musculus (mouse)",
        conflict_flag=False,
    )

    plan = plan_dda_execution(
        task_id="task-mouse",
        source_file_name="sample.raw",
        source_data_path="/data/sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.fasta_path.name == "Mus_musculus_reviewed.fasta"
    assert plan.fasta_selection_mode == "inferred"


def test_plan_dda_execution_uses_generic_fasta_for_multi_species_mixture(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Homo sapiens; Saccharomyces cerevisiae; Escherichia coli",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="Benchmark mixture",
        conflict_flag=False,
    )

    plan = plan_dda_execution(
        task_id="task-mixture",
        source_file_name="sample.raw",
        source_data_path="/data/sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.fasta_path.name == "generic_reference_with_contaminants.fasta"
    assert plan.fasta_selection_mode == "defaulted"


def test_plan_dda_execution_rejects_dia_for_strict_msdt(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.acquisition_mode = AttributeValue(
        value="DIA",
        confidence=1.0,
        source="sdrf",
        evidence_excerpt="DIA",
        conflict_flag=False,
    )

    plan = plan_dda_execution(
        task_id="task-002",
        source_file_name="sample.raw",
        source_data_path="/data/sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is True
    assert "DIA" in plan.blocking_issues[0]


def test_plan_dda_execution_prefers_project_fasta_over_species_guess(tmp_path: Path):
    attributes = _dda_attributes()
    context = ProjectContext(
        project_accession="PXD000010",
        file_name="sample.raw",
        metadata={
            "organisms": MetadataValue(
                value=["Homo sapiens"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            )
        },
        project_files=[
            {
                "fileName": "human_project_reference.fasta",
                "publicFileLocations": [
                    {"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD000010/human_project_reference.fasta"}
                ],
            }
        ],
    )

    plan = plan_dda_execution(
        task_id="task-project-fasta",
        source_file_name="sample.raw",
        source_data_path="/data/sample.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.fasta_path.name == "human_project_reference.fasta"
    assert plan.fasta_selection_mode == "reproduced"
    assert plan.fasta_download_url == "https://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD000010/human_project_reference.fasta"


def test_plan_dda_execution_requires_review_when_multiple_project_fastas_exist(tmp_path: Path):
    attributes = _dda_attributes()
    context = ProjectContext(
        project_accession="PXD000011",
        file_name="sample.raw",
        project_files=[
            {
                "fileName": "human_reference.fasta",
                "publicFileLocations": [
                    {"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD000011/human_reference.fasta"}
                ],
            },
            {
                "fileName": "contaminants.fasta",
                "publicFileLocations": [
                    {"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD000011/contaminants.fasta"}
                ],
            },
        ],
    )

    plan = plan_dda_execution(
        task_id="task-multi-project-fasta",
        source_file_name="sample.raw",
        source_data_path="/data/sample.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is True
    assert any("FASTA" in issue for issue in plan.blocking_issues)
