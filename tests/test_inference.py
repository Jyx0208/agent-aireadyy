from agent.inference.rules import infer_attributes
from agent.models import MetadataValue, ProjectContext


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
