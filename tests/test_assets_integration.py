from pathlib import Path

from agent.input.normalizer import normalize_input
from agent.models import (
    AttributeSet,
    AttributeValue,
    MetadataValue,
    ProjectCandidate,
    ProjectContext,
    ProjectResolution,
)
from agent.orchestrator.pipeline import AgentService


def _attributes() -> AttributeSet:
    return AttributeSet(
        acquisition_mode=AttributeValue(value="DDA", confidence=1.0, source="rule", evidence_excerpt="DDA", conflict_flag=False),
        species=AttributeValue(value="Homo sapiens", confidence=0.9, source="rule", evidence_excerpt="human", conflict_flag=False),
        instrument_name=AttributeValue(value="Orbitrap Fusion Lumos", confidence=0.9, source="rule", evidence_excerpt="instrument", conflict_flag=False),
        instrument_family=AttributeValue(value="orbitrap", confidence=0.9, source="rule", evidence_excerpt="family", conflict_flag=False),
        enzyme=AttributeValue(value="Lys-C", confidence=0.9, source="file_name_rule", evidence_excerpt="WT_5_Lys-c.raw", conflict_flag=False),
        labeling_strategy=AttributeValue(value="label-free", confidence=0.8, source="default", evidence_excerpt="default", conflict_flag=False),
        fixed_mods=AttributeValue(value=["C[57.02]"], confidence=0.7, source="default", evidence_excerpt="mods", conflict_flag=False),
        variable_mods=AttributeValue(value=["M[15.99]"], confidence=0.7, source="default", evidence_excerpt="mods", conflict_flag=False),
        fractionation_hint=AttributeValue(value=None, confidence=0.0, source="none", evidence_excerpt="", conflict_flag=False),
        search_parameter_hints=AttributeValue(value={"precursor_tol": "20ppm"}, confidence=0.6, source="rule", evidence_excerpt="profile", conflict_flag=False),
    )


def test_plan_dda_run_from_pride_asset_uses_resolved_prepared_path(tmp_path: Path, monkeypatch):
    messages: list[str] = []
    service = AgentService(pride_client=None, reporter=messages.append)
    task = normalize_input("WT_5_Lys-c.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD123456",
            matched_file="WT_5_Lys-c.raw",
            match_type="exact",
            match_score=100,
            evidence=["exact match"],
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
                value="Lys-C Orbitrap human proteomics",
                source="pride.projectDescription",
                source_level="project",
                completeness=0.8,
            )
        },
        project_files=[
            {
                "fileName": "WT_5_Lys-c.raw",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.raw"}],
            },
            {
                "fileName": "WT_5_Lys-c.mzML",
                "publicFileLocations": [{"value": "ftp://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.mzML"}],
            },
        ],
    )

    monkeypatch.setattr(service, "resolve_project", lambda _: resolution)
    monkeypatch.setattr(service, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(service, "infer_attributes", lambda *_: _attributes())

    result = service.plan_dda_run_from_pride(task=task, output_dir=tmp_path)

    assert result.asset.resolved_asset_type == "mzml"
    assert result.attributes.enzyme.value == "Lys-C"
    assert result.plan.source_data_path == result.asset.prepared_path
    assert result.plan.source_data_path.name == "WT_5_Lys-c.mzML"
    assert any("Decision summary:" in line for line in messages)
    assert any("Metadata summary:" in line for line in messages)
    assert any("Attribute decision:" in line for line in messages)
    assert any("Search decision:" in line for line in messages)
    assert any("Execution decision:" in line for line in messages)
