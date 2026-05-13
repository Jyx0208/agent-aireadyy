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
