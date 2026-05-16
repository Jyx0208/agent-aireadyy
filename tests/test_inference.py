from agent.inference.rules import infer_attributes
from agent.models import MetadataValue, ProjectContext
from agent.orchestrator.pipeline import AgentService


def test_infer_attributes_prefers_sdrf_evidence():
    context = ProjectContext(
        project_accession="PXD000001",
        file_name="sample.raw",
        sdrf_rows=[
            {
                "comment[data file]": "sample.raw",
                "comment[proteomics data acquisition method]": "DDA",
                "characteristics[organism]": "Homo sapiens",
                "comment[instrument]": "Orbitrap Fusion Lumos",
                "comment[cleavage agent details]": "Trypsin",
            }
        ],
        metadata={
            "projectDescription": MetadataValue(
                value="This DIA study used a Q Exactive instrument.",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.6,
            ),
        },
    )

    attrs = infer_attributes(context)

    assert attrs.acquisition_mode.value == "DDA"
    assert attrs.acquisition_mode.source == "sdrf"
    assert attrs.species.value == "Homo sapiens"
    assert attrs.instrument_name.value == "Orbitrap Fusion Lumos"
    assert attrs.enzyme.value == "Trypsin"


def test_infer_attributes_uses_word_boundaries_for_dia_detection():
    context = ProjectContext(
        project_accession="PXD000002",
        file_name="sample.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="We measured diameter changes in a human cohort.",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            ),
            "sampleProcessingProtocol": MetadataValue(
                value="Trypsin digestion followed by Orbitrap analysis.",
                source="pride.sampleProcessingProtocol",
                source_level="project",
                completeness=0.8,
            ),
            "organisms": MetadataValue(
                value=["Homo sapiens"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            ),
            "instruments": MetadataValue(
                value=["Orbitrap Exploris 480"],
                source="pride.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
    )

    attrs = infer_attributes(context)

    assert attrs.acquisition_mode.value != "DIA"
    assert attrs.acquisition_mode.conflict_flag is False


def test_infer_attributes_uses_file_name_hints_when_metadata_is_sparse():
    context = ProjectContext(
        project_accession="PXD000003",
        file_name="WT_5_Lys-c.raw",
        metadata={},
    )

    attrs = infer_attributes(context)

    assert attrs.enzyme.value == "Lys-C"
    assert attrs.enzyme.source == "file_name_rule"


def test_infer_attributes_preserves_repository_metadata_sources():
    context = ProjectContext(
        repository="massive",
        project_accession="MSV000101857",
        file_name="RN5_neg.mzML",
        metadata={
            "organisms": MetadataValue(
                value=["environmental samples <Bacillariophyta>"],
                source="massive.organisms",
                source_level="project",
                completeness=1.0,
            ),
            "instruments": MetadataValue(
                value=["Orbitrap Exploris 240"],
                source="massive.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
    )

    attrs = infer_attributes(context)

    assert attrs.species.source == "massive.organisms"
    assert attrs.instrument_name.source == "massive.instruments"


def test_infer_attributes_marks_massive_metabolomics_dataset_as_unsupported():
    context = ProjectContext(
        repository="massive",
        project_accession="MSV000101849",
        file_name="pos_inf_non_8629_male_65_192_B_H9.raw",
        metadata={
            "projectDescription": MetadataValue(
                value=(
                    "Untargeted HILIC positive LC-MS metabolomics data generated from serum samples "
                    "for small-molecule biomarkers."
                ),
                source="massive.description",
                source_level="project",
                completeness=1.0,
            ),
            "keywords": MetadataValue(
                value=["metabolomics", "DatasetType:Metabolomics"],
                source="massive.keywords",
                source_level="project",
                completeness=1.0,
            ),
            "organisms": MetadataValue(
                value=["Homo sapiens", "Trypanosoma cruzi"],
                source="massive.organisms",
                source_level="project",
                completeness=1.0,
            ),
            "instruments": MetadataValue(
                value=["Q Exactive Plus"],
                source="massive.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
        project_files=[
            {
                "fileName": "pos_inf_non_8629_male_65_192_B_H9.raw",
                "logicalPath": "raw/Untarget_HILICpos_raw/pos_inf_non_8629_male_65_192_B_H9.raw",
            }
        ],
    )

    attrs = infer_attributes(context)

    assert attrs.acquisition_mode.value == "unsupported"
    assert attrs.acquisition_mode.source == "unsupported_assay_rule"
    assert attrs.search_parameter_hints.value["data_family"] == "metabolomics"
    assert attrs.search_parameter_hints.conflict_flag is True


def test_infer_attributes_marks_massive_lipidomics_dataset_as_unsupported():
    context = ProjectContext(
        repository="massive",
        project_accession="MSV000101772",
        file_name="C30pos_MC00138_S001_GFP_8w_HFD_1.raw",
        metadata={
            "projectDescription": MetadataValue(
                value=(
                    "Lipidomics raw data run on plasma samples from Trp53(R270H/+) mice "
                    "under control or high-fat diet."
                ),
                source="massive.description",
                source_level="project",
                completeness=1.0,
            ),
            "keywords": MetadataValue(
                value=["Cancer", "High-fat diet", "DatasetType:Metabolomics"],
                source="massive.keywords",
                source_level="project",
                completeness=1.0,
            ),
            "organisms": MetadataValue(
                value=["Mus musculus"],
                source="massive.organisms",
                source_level="project",
                completeness=1.0,
            ),
            "instruments": MetadataValue(
                value=["Orbitrap ID-X"],
                source="massive.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
        project_files=[
            {
                "fileName": "C30pos_MC00138_S001_GFP_8w_HFD_1.raw",
                "logicalPath": "raw/C30pos_MC00138_S001_GFP_8w_HFD_1.raw",
            }
        ],
    )

    attrs = infer_attributes(context)

    assert attrs.acquisition_mode.value == "unsupported"
    assert attrs.search_parameter_hints.value["data_family"] == "metabolomics"


def test_infer_attributes_does_not_block_multiomics_project_when_target_has_proteomics_context():
    context = ProjectContext(
        repository="massive",
        project_accession="MSV000000001",
        file_name="proteomics_fraction_01.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="Integrated proteomics and metabolomics study.",
                source="massive.description",
                source_level="project",
                completeness=1.0,
            ),
            "keywords": MetadataValue(
                value=["DatasetType:Proteomics", "DatasetType:Metabolomics"],
                source="massive.keywords",
                source_level="project",
                completeness=1.0,
            ),
            "organisms": MetadataValue(
                value=["Homo sapiens"],
                source="massive.organisms",
                source_level="project",
                completeness=1.0,
            ),
            "instruments": MetadataValue(
                value=["Q Exactive Plus"],
                source="massive.instruments",
                source_level="project",
                completeness=1.0,
            ),
            "sampleProcessingProtocol": MetadataValue(
                value="Proteins were digested with trypsin before LC-MS/MS analysis.",
                source="massive.protocol",
                source_level="project",
                completeness=1.0,
            ),
        },
    )

    attrs = infer_attributes(context)

    assert attrs.acquisition_mode.value != "unsupported"
    assert attrs.enzyme.value == "Trypsin"


def test_agent_service_skips_llm_for_massive_metabolomics_context():
    class FailingReasoner:
        def confirm_search_parameters(self, context, attributes):
            raise AssertionError("LLM should not be called for unsupported metabolomics datasets")

    service = AgentService(llm_reasoner=FailingReasoner())
    context = ProjectContext(
        repository="massive",
        project_accession="MSV000101849",
        file_name="pos_inf_non_8629_male_65_192_B_H9.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="Untargeted HILIC positive LC-MS metabolomics data.",
                source="massive.description",
                source_level="project",
                completeness=1.0,
            ),
            "keywords": MetadataValue(
                value=["DatasetType:Metabolomics"],
                source="massive.keywords",
                source_level="project",
                completeness=1.0,
            ),
        },
    )

    attrs = service.infer_attributes(context)

    assert attrs.acquisition_mode.value == "unsupported"


def test_infer_attributes_defaults_to_dda_in_orbitrap_proteomics_context():
    context = ProjectContext(
        project_accession="PXD000004",
        file_name="sample.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="Immunoprecipitation followed by mass spectrometry analysis to identify binding partners.",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            ),
            "sampleProcessingProtocol": MetadataValue(
                value="Trypsin digestion followed by LC-MS/MS analysis.",
                source="pride.sampleProcessingProtocol",
                source_level="project",
                completeness=0.8,
            ),
            "organisms": MetadataValue(
                value=["Mus musculus (mouse)"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            ),
            "instruments": MetadataValue(
                value=["LTQ Orbitrap Velos"],
                source="pride.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
    )

    attrs = infer_attributes(context)

    assert attrs.acquisition_mode.value == "DDA"
    assert attrs.acquisition_mode.source == "rule_fallback"


def test_infer_attributes_marks_species_ambiguous_when_project_has_multiple_organisms_without_sdrf():
    context = ProjectContext(
        project_accession="PXD000005",
        file_name="sample.raw",
        metadata={
            "organisms": MetadataValue(
                value=["Homo sapiens", "Escherichia coli"],
                source="pride.organisms",
                source_level="project",
                completeness=1.0,
            ),
        },
    )

    attrs = infer_attributes(context)

    assert attrs.species.conflict_flag is True
    assert "Homo sapiens" in str(attrs.species.value)
    assert "Escherichia coli" in str(attrs.species.value)


def test_infer_attributes_marks_instrument_ambiguous_when_project_has_multiple_instruments_without_sdrf():
    context = ProjectContext(
        project_accession="PXD000006",
        file_name="sample.raw",
        metadata={
            "instruments": MetadataValue(
                value=["Orbitrap Exploris 480", "timsTOF Pro"],
                source="pride.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
    )

    attrs = infer_attributes(context)

    assert attrs.instrument_name.conflict_flag is True
    assert attrs.instrument_family.conflict_flag is True
    assert attrs.instrument_family.value == "unknown"


def test_infer_attributes_uses_cid_file_name_to_disambiguate_orbitrap_project_instruments():
    context = ProjectContext(
        project_accession="PXD000900",
        file_name="HeLa_ArgC-Try_CID_1.raw",
        metadata={
            "instruments": MetadataValue(
                value=["LTQ Orbitrap Elite", "Q Exactive"],
                source="pride.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
    )

    attrs = infer_attributes(context)

    assert attrs.instrument_name.value == "LTQ Orbitrap Elite"
    assert attrs.instrument_name.source == "file_name_instrument_rule"
    assert attrs.instrument_name.conflict_flag is False
    assert attrs.instrument_family.value == "orbitrap"
