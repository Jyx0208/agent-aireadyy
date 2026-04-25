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


def _reviewed_fasta(tmp_path: Path) -> Path:
    path = tmp_path / "reviewed_reference.fasta"
    path.write_text(">sp|P1|REVIEWED_TEST\nMPEPTIDEK\n", encoding="utf-8")
    return path

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
    assert plan.expected_pin_glob.endswith("sample_edited.pin")
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
    assert plan.fasta_download_url is not None
    assert "UP000000589" in plan.fasta_download_url


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


def test_plan_dda_execution_uses_wiff2mzml_mode_for_converted_sciex_input(tmp_path: Path):
    attributes = _dda_attributes()

    plan = plan_dda_execution(
        task_id="task-wiff",
        source_file_name="sample.wiff",
        source_data_path=tmp_path / "sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )
    assert plan.raw_data_type == "wiff2mzml"
    assert plan.output_paths["fp_msdt"].name == "sample_sage_msdt.parquet"
    assert plan.output_paths["fp_msdt"].name == "sample_sage_msdt.parquet"


def test_plan_dda_execution_allows_mgf_direct_msdt_conversion(tmp_path: Path):
    attributes = _dda_attributes()
    plan = plan_dda_execution(
        task_id="task-mgf",
        source_file_name="sample.mgf",
        source_data_path=tmp_path / "sample.mgf",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.raw_data_type == "mgf"
    assert plan.needs_review is False
    assert plan.output_paths["fp_msdt"].name == "sample_mgf_msdt.parquet"


def test_plan_dda_execution_requires_review_for_mzid_result_file(tmp_path: Path):
    attributes = _dda_attributes()
    plan = plan_dda_execution(
        task_id="task-mzid",
        source_file_name="sample.mzid",
        source_data_path=tmp_path / "sample.mzid",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.raw_data_type == "mzid"
    assert plan.needs_review is True
    assert any("mzid" in issue.lower() or "mzIdentML" in issue for issue in plan.blocking_issues)


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


def test_plan_dda_execution_accepts_dda_pasef_as_dda(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.acquisition_mode = AttributeValue(
        value="DDA-PASEF",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="TIMS DDA-PASEF",
        conflict_flag=False,
    )
    attributes.species = AttributeValue(
        value="Acinetobacter baumannii",
        confidence=1.0,
        source="sdrf",
        evidence_excerpt="Acinetobacter baumannii",
        conflict_flag=False,
    )
    attributes.instrument_name = AttributeValue(
        value="Bruker timsTOF Pro 2",
        confidence=0.95,
        source="sdrf",
        evidence_excerpt="timsTOF Pro 2",
        conflict_flag=False,
    )
    attributes.instrument_family = AttributeValue(
        value="timsTOF",
        confidence=0.95,
        source="rule",
        evidence_excerpt="timsTOF Pro 2",
        conflict_flag=False,
    )

    plan = plan_dda_execution(
        task_id="task-dda-pasef",
        source_file_name="tims_21nov0901_Slot1-6_1_313.mzML",
        source_data_path=tmp_path / "tims_21nov0901_Slot1-6_1_313.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    assert plan.needs_review is False
    assert plan.fragpipe_workflow_path.name == "LFQ_DDA_generic.workflow"


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
    assert any("多个项目 FASTA" in issue for issue in plan.blocking_issues)


def test_plan_dda_execution_requires_review_for_no_sdrf_unsupported_species_default_fasta(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Giardia intestinalis assemblage A",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="Giardia",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={"database": "GiardiaDB Assemblage A release 34"},
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="database hint",
        conflict_flag=False,
    )
    context = ProjectContext(
        project_accession="PXD019949",
        file_name="ASP-N_F4-R1.raw",
        metadata={
            "organisms": MetadataValue(
                value=["Giardia intestinalis assemblage A"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            )
        },
        sdrf_rows=[],
        project_files=[],
    )

    plan = plan_dda_execution(
        task_id="task-no-sdrf-no-fasta",
        source_file_name="ASP-N_F4-R1.raw",
        source_data_path=tmp_path / "ASP-N_F4-R1.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.fasta_selection_mode == "defaulted"
    assert plan.needs_review is True
    assert any("占位" in issue for issue in plan.blocking_issues)


def test_plan_dda_execution_uses_existing_llm_recommended_workflow(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.search_parameter_hints = AttributeValue(
        value={"recommended_workflow_name": "TMT_DDA_generic.workflow"},
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="LLM confirmed TMT workflow",
        conflict_flag=False,
    )

    plan = plan_dda_execution(
        task_id="task-llm-workflow",
        source_file_name="sample.raw",
        source_data_path=tmp_path / "sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.fragpipe_workflow_path.name == "TMT_DDA_generic.workflow"


def test_plan_dda_execution_ignores_unknown_llm_recommended_workflow(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.search_parameter_hints = AttributeValue(
        value={"recommended_workflow_name": "../unknown.workflow"},
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="LLM suggested unavailable workflow",
        conflict_flag=False,
    )

    plan = plan_dda_execution(
        task_id="task-unknown-llm-workflow",
        source_file_name="sample.raw",
        source_data_path=tmp_path / "sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.fragpipe_workflow_path.name == "LFQ_DDA_human_noNQ.workflow"


def test_plan_dda_execution_requires_review_for_top_down_project(tmp_path: Path):
    attributes = _dda_attributes()
    context = ProjectContext(
        project_accession="PXD_TOPDOWN",
        file_name="sample.raw",
        metadata={
            "experimentTypes": MetadataValue(
                value=["Top-down proteomics"],
                source="pride.experimentTypes",
                source_level="project",
                completeness=1.0,
            )
        },
    )

    plan = plan_dda_execution(
        task_id="task-topdown",
        source_file_name="sample.raw",
        source_data_path=tmp_path / "sample.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is True
    assert any("Top-down" in issue for issue in plan.blocking_issues)


def test_plan_dda_execution_requires_review_for_multi_metadata_without_sdrf(tmp_path: Path):
    attributes = _dda_attributes()
    context = ProjectContext(
        project_accession="PXD_MULTI",
        file_name="sample.raw",
        metadata={
            "organisms": MetadataValue(
                value=["Homo sapiens", "Mus musculus"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            ),
            "instruments": MetadataValue(
                value=["Orbitrap Fusion Lumos", "Q Exactive"],
                source="pride.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
    )

    plan = plan_dda_execution(
        task_id="task-multi-metadata",
        source_file_name="sample.raw",
        source_data_path=tmp_path / "sample.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is True
    assert any("多个物种" in issue for issue in plan.blocking_issues)
    assert any("多个仪器" in issue for issue in plan.blocking_issues)


def test_plan_dda_execution_names_query_url_reviewed_fasta_with_fasta_suffix(tmp_path: Path):
    attributes = _dda_attributes()
    plan = plan_dda_execution(
        task_id="task-reviewed-url",
        source_file_name="sample.raw",
        source_data_path=tmp_path / "sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
        reviewed_fasta_url="https://rest.uniprot.org/uniprotkb/stream?compressed=false&format=fasta&query=%28proteome%3AUP000002311%29",
        accept_search_parameter_review=True,
    )

    assert plan.fasta_path.name == "reviewed_reference.fasta"
    assert plan.fasta_selection_mode == "reviewed"
    assert plan.needs_review is False
