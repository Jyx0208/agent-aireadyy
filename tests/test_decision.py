from pathlib import Path

from agent.decision import dda
from agent.decision.dda import plan_dda_execution
from agent.models import (
    AttributeSet,
    AttributeValue,
    MetadataValue,
    ProjectContext,
    ProjectResolution,
)


def test_workspace_root_supports_installed_wheel_layout(tmp_path: Path, monkeypatch) -> None:
    installed_root = tmp_path / "site-packages"
    module_path = installed_root / "agent" / "decision" / "dda.py"
    (installed_root / "profiles" / "fragpipe").mkdir(parents=True)
    module_path.parent.mkdir(parents=True)
    module_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(dda, "__file__", str(module_path))

    assert dda._workspace_root() == installed_root


def test_packaged_fragpipe_workflows_are_not_placeholder_stubs() -> None:
    workflow_dir = dda._workspace_root() / "profiles" / "fragpipe"
    workflow_paths = sorted(workflow_dir.glob("*.workflow"))

    assert workflow_paths
    for workflow_path in workflow_paths:
        text = workflow_path.read_text(encoding="utf-8")
        assert workflow_path.stat().st_size > 5_000
        assert "__REPLACE_WITH_DECOY_FASTA__" not in text
        assert "database.decoy-tag=" in text
        assert "msfragger." in text


def test_packaged_fasta_profiles_do_not_ship_placeholder_fastas() -> None:
    fasta_dir = dda._workspace_root() / "profiles" / "fasta"

    assert not list(fasta_dir.glob("*.fasta"))


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
            value={"precursor_tol": "20ppm", "recommended_workflow_name": "Default.workflow"},
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
    assert plan.fasta_path.name == "uniprot_human_UP000005640.fasta"
    assert plan.fasta_download_url is not None
    assert "rest.uniprot.org" in plan.fasta_download_url
    assert "UP000005640" in plan.fasta_download_url
    assert plan.fragpipe_workflow_path.name == "Default.workflow"
    assert plan.manifest_path.name == "fragpipe-files.fp-manifest"
    assert plan.expected_pin_glob.endswith("sample_edited.pin")
    assert plan.output_paths["fp_msdt"].suffix == ".parquet"


def test_plan_dda_execution_uses_search_threads_from_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_SEARCH_THREADS", "2")

    plan = plan_dda_execution(
        task_id="task-threads",
        source_file_name="sample.raw",
        source_data_path="/data/sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=_dda_attributes(),
        output_dir=tmp_path,
    )

    assert plan.thread_num == 2


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

    assert plan.fasta_path.name == "uniprot_mouse_UP000000589.fasta"
    assert plan.fasta_selection_mode == "inferred"
    assert plan.fasta_download_url is not None
    assert "UP000000589" in plan.fasta_download_url


def test_plan_dda_execution_maps_rat_species_to_uniprot_download_target(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Rattus norvegicus",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="Rattus norvegicus",
        conflict_flag=False,
    )

    plan = plan_dda_execution(
        task_id="task-rat",
        source_file_name="sample.raw",
        source_data_path="/data/sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is False
    assert plan.fasta_path == tmp_path / "fasta" / "uniprot_rat_UP000002494.fasta"
    assert plan.fasta_selection_mode == "inferred"
    assert plan.fasta_download_url is not None
    assert "UP000002494" in plan.fasta_download_url


def test_plan_dda_execution_uses_combined_fasta_for_known_multi_species_mixture(tmp_path: Path):
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

    assert plan.fasta_path.name == "uniprot_human_UP000005640_yeast_UP000002311_ecoli_k12_UP000000625.fasta"
    assert plan.fasta_selection_mode == "inferred"
    assert plan.fasta_download_url is not None
    assert "UP000005640" in plan.fasta_download_url
    assert "UP000002311" in plan.fasta_download_url
    assert "UP000000625" in plan.fasta_download_url
    assert plan.needs_review is False


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
    assert "仅支持 DDA" in plan.blocking_issues[0]
    assert "Spectronaut" in plan.blocking_issues[0] or "DIA-NN" in plan.blocking_issues[0]


def test_plan_dda_execution_rejects_swath_as_dia(tmp_path: Path):
    """SWATH 是 DIA 的另一种名称，也应被阻断。"""
    attributes = _dda_attributes()
    attributes.acquisition_mode = AttributeValue(
        value="SWATH-MS",
        confidence=1.0,
        source="sdrf",
        evidence_excerpt="SWATH-MS acquisition",
        conflict_flag=False,
    )

    plan = plan_dda_execution(
        task_id="task-swath",
        source_file_name="swath_sample.raw",
        source_data_path="/data/swath_sample.mzML",
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
    attributes.search_parameter_hints = AttributeValue(
        value={"recommended_workflow_name": "LFQ-MBR.workflow"},
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="LLM confirmed DDA-PASEF workflow",
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
    assert plan.fragpipe_workflow_path.name == "LFQ-MBR.workflow"


def test_plan_dda_execution_defaults_to_uniprot_species_fasta_over_project_fasta(tmp_path: Path):
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

    assert plan.fasta_path.name == "uniprot_human_UP000005640.fasta"
    assert plan.fasta_selection_mode == "inferred"
    assert plan.fasta_download_url is not None
    assert "rest.uniprot.org" in plan.fasta_download_url
    assert "UP000005640" in plan.fasta_download_url


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
        prefer_project_fasta=True,
    )

    assert plan.needs_review is True
    assert any("多个项目 FASTA" in issue for issue in plan.blocking_issues)


def test_plan_dda_execution_prefers_uniprot_species_fasta_over_non_uniprot_llm_url_and_project_fastas(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.enzyme = AttributeValue(
        value="Asp-N",
        confidence=1.0,
        source="sdrf",
        evidence_excerpt="NT=Asp-N;AC=MS:1001304",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "LFQ-MBR.workflow",
            "recommended_fasta_name": "ensembl_Homo_sapiens.GRCh38.pep.all.fa",
            "recommended_fasta_url": "ftp://ftp.ensembl.org/pub/release-83/fasta/homo_sapiens/pep/Homo_sapiens.GRCh38.pep.all.fa.gz",
            "recommended_fasta_source": "Ensembl",
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="dataProcessingProtocol: Ensembl human proteome database (release-83, GRCh38)",
        conflict_flag=False,
    )
    context = ProjectContext(
        project_accession="PXD010154",
        file_name="01753_H12_P018443_S00_N01_R1.raw",
        sdrf_rows=[{"comment[data file]": "01753_H12_P018443_S00_N01_R1.raw"}],
        project_files=[
            {
                "fileName": "human_reference.fasta",
                "publicFileLocations": [
                    {"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2019/07/PXD010154/human_reference.fasta"}
                ],
            },
            {
                "fileName": "contaminants.fasta",
                "publicFileLocations": [
                    {"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2019/07/PXD010154/contaminants.fasta"}
                ],
            },
        ],
    )

    plan = plan_dda_execution(
        task_id="task-protocol-fasta",
        source_file_name="01753_H12_P018443_S00_N01_R1.raw",
        source_data_path=tmp_path / "01753_H12_P018443_S00_N01_R1.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is False
    assert plan.fasta_selection_mode == "inferred"
    assert plan.fasta_path.name == "uniprot_human_UP000005640.fasta"
    assert plan.fasta_download_url is not None
    assert "rest.uniprot.org" in plan.fasta_download_url
    assert "UP000005640" in plan.fasta_download_url
    assert not plan.blocking_issues


def test_plan_dda_execution_can_prefer_project_fasta_when_user_selects_it(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "Default.workflow",
            "recommended_fasta_name": "llm_reference.fasta",
            "recommended_fasta_url": "https://example.org/llm_reference.fasta",
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="LLM recommended FASTA",
        conflict_flag=False,
    )
    context = ProjectContext(
        project_accession="PXD000012",
        file_name="sample.raw",
        project_files=[
            {
                "fileName": "project_reference.fasta",
                "publicFileLocations": [
                    {"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD000012/project_reference.fasta"}
                ],
            }
        ],
    )

    plan = plan_dda_execution(
        task_id="task-project-fasta",
        source_file_name="sample.raw",
        source_data_path=tmp_path / "sample.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
        prefer_project_fasta=True,
    )

    assert plan.needs_review is False
    assert plan.fasta_selection_mode == "reproduced"
    assert plan.fasta_path == tmp_path / "fasta" / "project_reference.fasta"
    assert plan.fasta_download_url == "https://ftp.pride.ebi.ac.uk/pride/data/archive/2024/01/PXD000012/project_reference.fasta"


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
        value={"database": "GiardiaDB Assemblage A release 34", "recommended_workflow_name": "Default.workflow"},
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
        value={"recommended_workflow_name": "TMT10.workflow"},
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

    assert plan.fragpipe_workflow_path.name == "TMT10.workflow"


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

    assert plan.needs_review is True
    assert any("不存在" in issue for issue in plan.blocking_issues)


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
    attributes.species = AttributeValue(
        value="Homo sapiens; Mus musculus",
        confidence=0.5,
        source="pride.organisms",
        evidence_excerpt="multiple organisms",
        conflict_flag=True,
    )
    attributes.instrument_name = AttributeValue(
        value="Orbitrap Fusion Lumos; Q Exactive",
        confidence=0.5,
        source="pride.instruments",
        evidence_excerpt="multiple instruments",
        conflict_flag=True,
    )
    attributes.instrument_family = AttributeValue(
        value="unknown",
        confidence=0.4,
        source="pride.instruments",
        evidence_excerpt="multiple instruments",
        conflict_flag=True,
    )
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


def test_plan_dda_execution_trusts_high_confidence_llm_species_for_multi_species_project_without_sdrf(
    tmp_path: Path,
):
    source_name = "20191002_EXP1_Evo1_AMV_TMT11prot_Rat_SetC_21min_46fracs_36.raw"
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Rattus norvegicus",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="LLM resolved the target file as rat",
        conflict_flag=True,
    )
    attributes.instrument_name = AttributeValue(
        value="Orbitrap Exploris 480",
        confidence=0.95,
        source="mzml",
        evidence_excerpt="mzML instrumentConfiguration Orbitrap Exploris 480",
        conflict_flag=False,
    )
    attributes.instrument_family = AttributeValue(
        value="orbitrap",
        confidence=0.95,
        source="mzml",
        evidence_excerpt="mzML analyzer orbitrap",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "Default.workflow",
            "recommended_fasta_name": "uniprot-rat-reviewed.fasta",
            "recommended_fasta_url": "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/reference_proteomes/Rodentia/UP000002494/UP000002494_10116.fasta.gz",
            "recommended_fasta_source": "UniProt",
        },
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="LLM recommended rat UniProt reference proteome",
        conflict_flag=False,
    )
    context = ProjectContext(
        project_accession="PXD016662",
        file_name=source_name,
        metadata={
            "organisms": MetadataValue(
                value=["Rattus norvegicus", "Homo sapiens"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            )
        },
        sdrf_rows=[],
        project_files=[],
    )

    plan = plan_dda_execution(
        task_id="task-rat-multi-species",
        source_file_name=source_name,
        source_data_path=tmp_path / "20191002_EXP1_Evo1_AMV_TMT11prot_Rat_SetC_21min_46fracs_36.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is False
    assert plan.blocking_issues == []
    assert plan.fasta_path == tmp_path / "fasta" / "uniprot_rat_UP000002494.fasta"
    assert plan.fasta_download_url is not None
    assert "rest.uniprot.org" in plan.fasta_download_url
    assert "UP000002494" in plan.fasta_download_url
    assert "reviewed%3Atrue" in plan.fasta_download_url
    assert "ftp.uniprot.org" not in plan.fasta_download_url


def test_plan_dda_execution_uses_species_uniprot_url_for_rat_taxonomy_hint_without_sdrf(
    tmp_path: Path,
):
    source_name = "20191002_EXP1_Evo1_AMV_TMT11prot_Rat_SetC_21min_46fracs_36.raw"
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Rattus norvegicus",
        confidence=0.8,
        source="llm_confirmed",
        evidence_excerpt="Organisms include rat and human; target file likely rat",
        conflict_flag=True,
    )
    attributes.instrument_name = AttributeValue(
        value="Orbitrap Exploris 480",
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="PRIDE instrument field: Orbitrap Exploris 480",
        conflict_flag=False,
    )
    attributes.instrument_family = AttributeValue(
        value="orbitrap",
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="Orbitrap Exploris 480",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "Default.workflow",
            "recommended_fasta_name": "uniprot_rat_2024.fasta",
            "recommended_fasta_url": "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/taxonomic_divisions/uniprot_taxonomy_10116.fasta",
            "recommended_fasta_source": "UniProt",
        },
        confidence=0.6,
        source="rule",
        evidence_excerpt="Default parameters for Orbitrap DDA",
        conflict_flag=False,
    )
    context = ProjectContext(
        project_accession="PXD016662",
        file_name=source_name,
        metadata={
            "organisms": MetadataValue(
                value=["Rattus norvegicus", "Homo sapiens"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            )
        },
        sdrf_rows=[],
        project_files=[],
    )

    plan = plan_dda_execution(
        task_id="task-rat-taxonomy-url",
        source_file_name=source_name,
        source_data_path=tmp_path / "20191002_EXP1_Evo1_AMV_TMT11prot_Rat_SetC_21min_46fracs_36.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is False
    assert plan.blocking_issues == []
    assert plan.fasta_path == tmp_path / "fasta" / "uniprot_rat_UP000002494.fasta"
    assert plan.fasta_download_url is not None
    assert plan.fasta_download_url == "https://rest.uniprot.org/uniprotkb/stream?compressed=false&format=fasta&query=%28proteome%3AUP000002494%29%20AND%20%28reviewed%3Atrue%29"


def test_plan_dda_execution_resolves_pxd016662_rat_file_species_conflict_from_file_name(tmp_path: Path):
    source_name = "20191002_EXP1_Evo1_AMV_TMT11prot_Rat_SetC_21min_46fracs_36.raw"
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Rattus norvegicus (rat); Homo sapiens (human)",
        confidence=0.5,
        source="pride.organisms",
        evidence_excerpt="Project-level organisms list rat and human.",
        conflict_flag=True,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "TMT10.workflow",
            "recommended_fasta_name": "Rattus_norvegicus_uniprot_2024.fasta",
            "recommended_fasta_url": "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/reference_proteomes/Eukaryota/UP000002494/UP000002494_10116.fasta.gz",
            "recommended_fasta_source": "UniProt",
        },
        confidence=0.8,
        source="llm_confirmed",
        evidence_excerpt="Rat TMT file.",
        conflict_flag=False,
    )
    context = ProjectContext(
        project_accession="PXD016662",
        file_name=source_name,
        metadata={
            "organisms": MetadataValue(
                value=["Rattus norvegicus (rat)", "Homo sapiens (human)"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            )
        },
        sdrf_rows=[],
        project_files=[],
    )

    plan = plan_dda_execution(
        task_id="task-pxd016662-rat-tmt",
        source_file_name=source_name,
        source_data_path=tmp_path / "20191002_EXP1_Evo1_AMV_TMT11prot_Rat_SetC_21min_46fracs_36.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is False
    assert plan.blocking_issues == []
    assert plan.fasta_path == tmp_path / "fasta" / "uniprot_rat_UP000002494.fasta"


def test_plan_dda_execution_corrects_pxd016662_non_rat_file_to_human_fasta(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Rattus norvegicus",
        confidence=0.8,
        source="llm_confirmed",
        evidence_excerpt="LLM incorrectly inferred rat for a non-rat PXD016662 file.",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "LFQ-phospho.workflow",
            "recommended_fasta_name": "UP000002494_10116.fasta",
            "recommended_fasta_url": "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/reference_proteomes/Eukaryota/UP000002494/UP000002494_10116.fasta.gz",
            "recommended_fasta_source": "UniProt",
        },
        confidence=0.8,
        source="llm_confirmed",
        evidence_excerpt="LLM returned a rat FASTA hint.",
        conflict_flag=False,
    )
    context = ProjectContext(
        project_accession="PXD016662",
        file_name="20190914_EXP1_Evo1_AMV_LFQPhos_200ug_30ul-TiIMACHP_200ul_RT_01.raw",
        metadata={
            "organisms": MetadataValue(
                value=["Rattus norvegicus (rat)", "Homo sapiens (human)"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            )
        },
        sdrf_rows=[],
        project_files=[],
    )

    plan = plan_dda_execution(
        task_id="task-pxd016662-human-phospho",
        source_file_name="20190914_EXP1_Evo1_AMV_LFQPhos_200ug_30ul-TiIMACHP_200ul_RT_01.raw",
        source_data_path=tmp_path / "20190914_EXP1_Evo1_AMV_LFQPhos_200ug_30ul-TiIMACHP_200ul_RT_01.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.fasta_path == tmp_path / "fasta" / "uniprot_human_UP000005640.fasta"
    assert plan.fasta_download_url is not None
    assert "UP000005640" in plan.fasta_download_url
    assert "UP000002494" not in plan.fasta_download_url


def test_plan_dda_execution_uses_combined_reviewed_uniprot_for_resolved_multi_species_without_sdrf(
    tmp_path: Path,
):
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Rattus norvegicus (rat); Homo sapiens (human)",
        confidence=0.8,
        source="llm_confirmed",
        evidence_excerpt="PRIDE organisms field lists both rat and human.",
        conflict_flag=False,
    )
    attributes.instrument_name = AttributeValue(
        value="Orbitrap Exploris 480",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="PRIDE instrument field: Orbitrap Exploris 480",
        conflict_flag=False,
    )
    attributes.instrument_family = AttributeValue(
        value="orbitrap",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="Orbitrap Exploris 480",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "Default.workflow",
            "recommended_fasta_name": "uniprot-human+rat-2019-01",
            "recommended_fasta_url": "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/reference_proteomes/Homo_sapiens+Rattus_norvegicus.fasta",
            "recommended_fasta_source": "UniProt",
        },
        confidence=0.8,
        source="llm_confirmed",
        evidence_excerpt="Use both species listed in PRIDE.",
        conflict_flag=False,
    )
    context = ProjectContext(
        project_accession="PXD_MULTI",
        file_name="20190524_EXP1_Evo2_DBJ_LFQprot_SDS_500ng_15000_21min_CV40_01.raw",
        metadata={
            "organisms": MetadataValue(
                value=["Rattus norvegicus (rat)", "Homo sapiens (human)"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            )
        },
        sdrf_rows=[],
        project_files=[],
    )

    plan = plan_dda_execution(
        task_id="task-human-rat-combined",
        source_file_name="20190524_EXP1_Evo2_DBJ_LFQprot_SDS_500ng_15000_21min_CV40_01.raw",
        source_data_path=tmp_path / "20190524_EXP1_Evo2_DBJ_LFQprot_SDS_500ng_15000_21min_CV40_01.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is False
    assert plan.blocking_issues == []
    assert plan.fasta_path == tmp_path / "fasta" / "uniprot_human_UP000005640_rat_UP000002494.fasta"
    assert plan.fasta_download_url is not None
    assert "rest.uniprot.org" in plan.fasta_download_url
    assert "UP000005640" in plan.fasta_download_url
    assert "UP000002494" in plan.fasta_download_url
    assert "reviewed%3Atrue" in plan.fasta_download_url
    assert "ftp.uniprot.org" not in plan.fasta_download_url


def test_plan_dda_execution_uses_all_llm_uniprot_proteome_ids_for_hye_mixture(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Escherichia coli; Homo sapiens; Saccharomyces cerevisiae",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="SDRF rows describe the HYE benchmark mixture.",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "Default.workflow",
            "recommended_fasta_name": "H_sapiens_Yeast_Ecoli_2024.fasta",
            "recommended_fasta_url": "https://uniprot.org/proteomes/UP000005640+UP000002311+UP000000625.fasta",
            "recommended_fasta_source": "UniProt",
        },
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="Use reviewed Human/Yeast/E. coli UniProt proteomes.",
        conflict_flag=False,
    )

    plan = plan_dda_execution(
        task_id="task-hye-fasta",
        source_file_name="LFQ_Orbitrap_GP_QC_400_500.raw",
        source_data_path=tmp_path / "LFQ_Orbitrap_GP_QC_400_500.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.fasta_path == tmp_path / "fasta" / "uniprot_human_UP000005640_yeast_UP000002311_ecoli_k12_UP000000625.fasta"
    assert plan.fasta_download_url is not None
    assert "UP000005640" in plan.fasta_download_url
    assert "UP000002311" in plan.fasta_download_url
    assert "UP000000625" in plan.fasta_download_url


def test_plan_dda_execution_prefers_species_fasta_over_conflicting_llm_uniprot_id(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Oryza sativa (rice)",
        confidence=0.95,
        source="llm_confirmed",
        evidence_excerpt="PRIDE organisms field: Oryza sativa (rice).",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "LFQ-ubiquitin.workflow",
            "recommended_fasta_name": "Oryza_sativa_uniprot_2024.fasta",
            "recommended_fasta_url": "https://www.uniprot.org/proteomes/UP000000763",
            "recommended_fasta_source": "UniProt",
        },
        confidence=0.8,
        source="llm_confirmed",
        evidence_excerpt="LLM returned a UniProt URL but the proteome ID conflicts with rice.",
        conflict_flag=False,
    )

    plan = plan_dda_execution(
        task_id="task-rice-conflicting-fasta",
        source_file_name="Ubi-MSP1ox-F1-R1.raw",
        source_data_path=tmp_path / "Ubi-MSP1ox-F1-R1.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.fasta_path == tmp_path / "fasta" / "uniprot_oryza_sativa_UP000059680.fasta"
    assert plan.fasta_download_url is not None
    assert "UP000059680" in plan.fasta_download_url
    assert "UP000000763" not in plan.fasta_download_url


def test_plan_dda_execution_allows_multi_metadata_when_file_level_values_are_resolved(tmp_path: Path):
    attributes = _dda_attributes()
    attributes.species = AttributeValue(
        value="Homo sapiens",
        confidence=1.0,
        source="user_review",
        evidence_excerpt="用户选择 Homo sapiens",
        conflict_flag=False,
    )
    attributes.instrument_name = AttributeValue(
        value="Q Exactive HF",
        confidence=1.0,
        source="mzml",
        evidence_excerpt="mzML instrumentConfiguration MS:1002523",
        conflict_flag=False,
    )
    attributes.instrument_family = AttributeValue(
        value="orbitrap",
        confidence=1.0,
        source="mzml",
        evidence_excerpt="mzML analyzer orbitrap",
        conflict_flag=False,
    )
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
                value=["Orbitrap Fusion Lumos", "Q Exactive HF"],
                source="pride.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
    )

    plan = plan_dda_execution(
        task_id="task-multi-metadata-resolved",
        source_file_name="sample.raw",
        source_data_path=tmp_path / "sample.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is False
    assert not any("多个物种" in issue or "多个仪器" in issue for issue in plan.blocking_issues)


def test_plan_dda_execution_allows_multi_instrument_same_family_when_search_tolerances_are_resolved(
    tmp_path: Path,
):
    attributes = _dda_attributes()
    attributes.instrument_name = AttributeValue(
        value="LTQ Orbitrap Elite; Q Exactive",
        confidence=0.8,
        source="pride.instruments",
        evidence_excerpt="Project instruments are both Orbitrap-family instruments",
        conflict_flag=False,
    )
    attributes.instrument_family = AttributeValue(
        value="orbitrap",
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="Both listed instruments are Orbitrap-family instruments",
        conflict_flag=False,
    )
    attributes.search_parameter_hints = AttributeValue(
        value={
            "recommended_workflow_name": "Default.workflow",
            "precursor_tol": "20 ppm",
            "fragment_tol": "0.5 Da",
        },
        confidence=0.9,
        source="llm_confirmed",
        evidence_excerpt="LLM resolved Orbitrap search tolerances",
        conflict_flag=False,
    )
    context = ProjectContext(
        project_accession="PXD000900",
        file_name="HeLa_ArgC-Try_CID_1.raw",
        metadata={
            "organisms": MetadataValue(
                value=["Homo sapiens"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            ),
            "instruments": MetadataValue(
                value=["LTQ Orbitrap Elite", "Q Exactive"],
                source="pride.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
        sdrf_rows=[],
    )

    plan = plan_dda_execution(
        task_id="task-hela-arg-c-try",
        source_file_name="HeLa_ArgC-Try_CID_1.raw",
        source_data_path=tmp_path / "HeLa_ArgC-Try_CID_1.mzML",
        project_resolution=ProjectResolution.empty(),
        project_context=context,
        attributes=attributes,
        output_dir=tmp_path,
    )

    assert plan.needs_review is False
    assert not any("多个仪器" in issue for issue in plan.blocking_issues)


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
