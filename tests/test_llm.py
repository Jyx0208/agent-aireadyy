from __future__ import annotations

from agent.inference.rules import infer_attributes
from agent.llm.reasoner import OpenAICompatibleReasoner, _metadata_context_text, confirm_no_sdrf_parameters, confirm_sdrf_parameters
from agent.models import AttributeValue, MetadataValue, ProjectContext
from agent.orchestrator.pipeline import AgentService


class FakeReasoner:
    def __init__(self):
        self.calls = 0

    def confirm_search_parameters(self, context, attributes):
        self.calls += 1
        return {
            "search_parameter_hints": AttributeValue(
                value={
                    "precursor_tol": "10ppm",
                    "fragment_tol": "20ppm",
                    "missed_cleavages": 2,
                    "data_family": "thermo_raw",
                },
                confidence=0.88,
                source="llm_confirmed",
                evidence_excerpt="Orbitrap high-resolution DDA with Lys-C suggests 10/20 ppm and 2 missed cleavages.",
                conflict_flag=False,
            ),
            "enzyme": AttributeValue(
                value="Lys-C",
                confidence=0.91,
                source="llm_confirmed",
                evidence_excerpt="Protocol explicitly mentions Lys-C digestion.",
                conflict_flag=False,
            ),
        }


class FailingReasoner:
    def confirm_search_parameters(self, context, attributes):
        raise RuntimeError("LLM unavailable")


class FakeSdrfReasoner:
    def __init__(self):
        self.calls = 0

    def confirm_search_parameters(self, context, attributes):
        self.calls += 1
        return {
            "acquisition_mode": AttributeValue(
                value="DIA",
                confidence=0.98,
                source="llm_confirmed",
                evidence_excerpt="DIA benchmark file name and project experiment type indicate DIA.",
                conflict_flag=False,
            ),
            "species": AttributeValue(
                value="Homo sapiens; Saccharomyces cerevisiae; Escherichia coli",
                confidence=0.97,
                source="llm_confirmed",
                evidence_excerpt="Multiple matched SDRF rows describe a benchmark mixture.",
                conflict_flag=False,
            ),
            "instrument_name": AttributeValue(
                value="Orbitrap Astral",
                confidence=0.96,
                source="llm_confirmed",
                evidence_excerpt="comment[instrument] normalizes to Orbitrap Astral.",
                conflict_flag=False,
            ),
            "enzyme": AttributeValue(
                value="Trypsin/P",
                confidence=0.96,
                source="llm_confirmed",
                evidence_excerpt="comment[cleavage agent details] NT=Trypsin/P.",
                conflict_flag=False,
            ),
            "fixed_mods": AttributeValue(
                value=["C[57.02]"],
                confidence=0.95,
                source="llm_confirmed",
                evidence_excerpt="comment[modification parameters] indicates carbamidomethyl C fixed.",
                conflict_flag=False,
            ),
            "search_parameter_hints": AttributeValue(
                value={"missed_cleavages": 1},
                confidence=0.9,
                source="llm_confirmed",
                evidence_excerpt="benchmark SDRF and metadata point to one missed cleavage.",
                conflict_flag=False,
            ),
        }


def test_llm_confirmation_updates_search_hints_when_no_sdrf():
    context = ProjectContext(
        project_accession="PXD000010",
        file_name="WT_5_Lys-c.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="Samples were analyzed on an Orbitrap Fusion Lumos after Lys-C digestion.",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            ),
            "instruments": MetadataValue(
                value=["Orbitrap Fusion Lumos"],
                source="pride.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
        sdrf_rows=[],
    )

    base = infer_attributes(context)
    reasoner = FakeReasoner()
    confirmed = confirm_no_sdrf_parameters(context, base, llm_reasoner=reasoner)

    assert reasoner.calls == 1
    assert confirmed.search_parameter_hints.value["precursor_tol"] == "10ppm"
    assert "data_family" in confirmed.search_parameter_hints.value
    assert confirmed.search_parameter_hints.source == "llm_confirmed"
    assert confirmed.enzyme.value == "Lys-C"
    assert confirmed.enzyme.source == "llm_confirmed"


def test_llm_confirmation_keeps_fasta_and_workflow_recommendations_in_hints():
    context = ProjectContext(
        project_accession="PXD000010",
        file_name="ASP-N_F4-R1.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="Asp-N Giardia proteomics searched against GiardiaDB.",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            )
        },
        sdrf_rows=[],
    )
    base = infer_attributes(context)

    class FastaWorkflowReasoner:
        def confirm_search_parameters(self, context, attributes):
            return {
                "search_parameter_hints": AttributeValue(
                    value={
                        "database": "GiardiaDB Assemblage A WB release 34",
                        "recommended_fasta_name": "GiardiaDB-34_AassemblageA_AnnotatedProteins.fasta",
                        "recommended_fasta_url": None,
                        "recommended_fasta_source": "project protocol names GiardiaDB release 34",
                        "recommended_workflow_name": "LFQ_DDA_generic.workflow",
                        "workflow_rationale": "label-free DDA raw file",
                    },
                    confidence=0.9,
                    source="llm_confirmed",
                    evidence_excerpt="Project description and target file imply Asp-N LFQ DDA against GiardiaDB.",
                    conflict_flag=False,
                )
            }

    confirmed = confirm_no_sdrf_parameters(context, base, llm_reasoner=FastaWorkflowReasoner())

    hints = confirmed.search_parameter_hints.value
    assert hints["recommended_fasta_name"] == "GiardiaDB-34_AassemblageA_AnnotatedProteins.fasta"
    assert hints["recommended_workflow_name"] == "LFQ_DDA_generic.workflow"


def test_no_sdrf_llm_context_includes_parameter_and_fasta_files():
    context = ProjectContext(
        project_accession="PXD000020",
        file_name="sample_F1_R1.mzML",
        metadata={
            "projectDescription": MetadataValue(
                value="DDA phosphoproteomics searched with MSFragger using trypsin.",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            )
        },
        project_files=[
            {"fileName": "sample_F1_R1.mzML"},
            {"fileName": "fragpipe.workflow"},
            {"fileName": "fragger.params"},
            {"fileName": "rice_reference.fasta"},
        ],
    )

    text = _metadata_context_text(context)

    assert "parameter_or_workflow_files: fragpipe.workflow; fragger.params" in text
    assert "fasta_files: rice_reference.fasta" in text


def test_openai_compatible_reasoner_defaults_to_gpt_5_4():
    reasoner = OpenAICompatibleReasoner(api_key="test-key")

    assert reasoner.model == "gpt-5.4"


def test_llm_confirmation_is_skipped_when_sdrf_exists():
    context = ProjectContext(
        project_accession="PXD000011",
        file_name="sample.raw",
        metadata={},
        sdrf_rows=[{"comment[data file]": "sample.raw"}],
    )

    base = infer_attributes(context)
    reasoner = FakeReasoner()
    confirmed = confirm_no_sdrf_parameters(context, base, llm_reasoner=reasoner)

    assert reasoner.calls == 0
    assert confirmed == base


def test_llm_confirmation_emits_report_messages():
    context = ProjectContext(
        project_accession="PXD000012",
        file_name="sample.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="DDA Orbitrap proteomics study in human samples.",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            )
        },
        sdrf_rows=[],
    )
    base = infer_attributes(context)
    reasoner = FakeReasoner()
    messages: list[str] = []

    confirm_no_sdrf_parameters(context, base, llm_reasoner=reasoner, report=messages.append)

    assert any("\u672a\u627e\u5230 SDRF \u884c" in line for line in messages)
    assert any("\u6b63\u5728\u8c03\u7528\u5927\u6a21\u578b" in line for line in messages)
    assert any("\u5927\u6a21\u578b\u786e\u8ba4\u7ed3\u679c\u5df2\u5408\u5e76" in line for line in messages)


def test_llm_confirmation_blocks_no_sdrf_when_reasoner_fails():
    context = ProjectContext(
        project_accession="PXD000014",
        file_name="sample.raw",
        metadata={},
        sdrf_rows=[],
    )
    base = infer_attributes(context)
    messages: list[str] = []

    confirmed = confirm_no_sdrf_parameters(
        context,
        base,
        llm_reasoner=FailingReasoner(),
        report=messages.append,
    )

    assert confirmed.search_parameter_hints.conflict_flag is True
    assert confirmed.search_parameter_hints.source == "llm_required"
    assert "llm_confirmation_error" in confirmed.search_parameter_hints.value
    assert any("\u5927\u6a21\u578b\u786e\u8ba4\u5931\u8d25" in line for line in messages)


def test_llm_sdrf_confirmation_updates_file_level_attributes():
    context = ProjectContext(
        project_accession="PXD071205",
        file_name="LFQ_Astral_DIA_Optimized_DI_5min_250pg_Condition_C_REP5.raw",
        metadata={
            "experimentTypes": MetadataValue(
                value=["Data-independent acquisition"],
                source="pride.experimentTypes",
                source_level="project",
                completeness=1.0,
            ),
            "instruments": MetadataValue(
                value=["Orbitrap Astral"],
                source="pride.instruments",
                source_level="project",
                completeness=1.0,
            ),
        },
        sdrf_rows=[
            {
                "characteristics[organism]": "Escherichia coli",
                "comment[data file]": "LFQ_Astral_DIA_Optimized_DI_5min_250pg_Condition_C_REP5.raw",
                "comment[cleavage agent details]": "NT=Trypsin/P",
                "comment[instrument]": "NT=Orbitrap Astral;AC=MS:1003378",
            },
            {
                "characteristics[organism]": "Homo sapiens",
                "comment[data file]": "LFQ_Astral_DIA_Optimized_DI_5min_250pg_Condition_C_REP5.raw",
                "comment[cleavage agent details]": "NT=Trypsin/P",
                "comment[instrument]": "NT=Orbitrap Astral;AC=MS:1003378",
            },
        ],
    )
    base = infer_attributes(context)
    reasoner = FakeSdrfReasoner()
    messages: list[str] = []

    confirmed = confirm_sdrf_parameters(context, base, llm_reasoner=reasoner, report=messages.append)

    assert reasoner.calls == 1
    assert confirmed.acquisition_mode.value == "DIA"
    assert confirmed.acquisition_mode.source == "llm_confirmed"
    assert confirmed.species.value == "Homo sapiens; Saccharomyces cerevisiae; Escherichia coli"
    assert confirmed.instrument_name.value == "Orbitrap Astral"
    assert confirmed.enzyme.value == "Trypsin/P"
    assert confirmed.fixed_mods.value == ["C[57.02]"]
    assert any("\u627e\u5230\u5339\u914d\u7684 SDRF \u884c" in line for line in messages)
    assert any("\u5927\u6a21\u578b SDRF \u6c47\u603b\u7ed3\u679c\u5df2\u5408\u5e76" in line for line in messages)


def test_agent_service_infer_attributes_uses_llm_when_no_sdrf():
    context = ProjectContext(
        project_accession="PXD000013",
        file_name="sample.raw",
        metadata={
            "projectDescription": MetadataValue(
                value="Samples were analyzed by LC-MS/MS on an Orbitrap Fusion Lumos after Lys-C digestion.",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            )
        },
        sdrf_rows=[],
    )
    reasoner = FakeReasoner()
    service = AgentService(pride_client=None, llm_reasoner=reasoner)

    confirmed = service.infer_attributes(context)

    assert reasoner.calls == 1
    assert confirmed.enzyme.value == "Lys-C"
    assert confirmed.enzyme.source == "llm_confirmed"


def test_agent_service_infer_attributes_uses_llm_for_sdrf_summary():
    context = ProjectContext(
        project_accession="PXD071205",
        file_name="sample.raw",
        metadata={
            "experimentTypes": MetadataValue(
                value=["Data-independent acquisition"],
                source="pride.experimentTypes",
                source_level="project",
                completeness=1.0,
            )
        },
        sdrf_rows=[
            {"comment[data file]": "sample.raw", "characteristics[organism]": "Escherichia coli"},
            {"comment[data file]": "sample.raw", "characteristics[organism]": "Homo sapiens"},
        ],
    )
    reasoner = FakeSdrfReasoner()
    service = AgentService(pride_client=None, llm_reasoner=reasoner)

    confirmed = service.infer_attributes(context)

    assert reasoner.calls == 1
    assert confirmed.acquisition_mode.value == "DIA"
    assert confirmed.species.source == "llm_confirmed"
