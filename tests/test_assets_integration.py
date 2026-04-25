import json
from pathlib import Path

import pytest

from agent.input.normalizer import normalize_input
from agent.models import (
    AttributeSet,
    AttributeValue,
    FileAsset,
    MetadataValue,
    ProjectCandidate,
    ProjectContext,
    ProjectResolution,
)
from agent.orchestrator.pipeline import AgentService, ReviewRequiredError


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


def _attributes_requiring_search_review() -> AttributeSet:
    attrs = _attributes()
    return attrs.model_copy(
        update={
            "search_parameter_hints": AttributeValue(
                value={"precursor_tol": "10 ppm", "fragment_tol": "0.02 Da", "database": "reviewed db"},
                confidence=0.9,
                source="llm_confirmed",
                evidence_excerpt="LLM-confirmed parameters require human review.",
                conflict_flag=True,
            )
        }
    )


def _reviewed_fasta(tmp_path: Path) -> Path:
    path = tmp_path / "reviewed_reference.fasta"
    path.write_text(">sp|P1|REVIEWED_TEST\nMPEPTIDEK\n", encoding="utf-8")
    return path


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
    assert any(line.startswith("\u9879\u76ee\u89e3\u6790\u6458\u8981\uff1a") for line in messages)
    assert any(line.startswith("\u9879\u76ee\u5143\u6570\u636e\u6458\u8981\uff1a") for line in messages)
    assert any(line.startswith("\u5c5e\u6027\u5224\u65ad\uff1a") for line in messages)
    assert any(line.startswith("\u641c\u5e93\u53c2\u6570\u5224\u65ad\uff1a") for line in messages)
    assert any(line.startswith("\u6267\u884c\u8ba1\u5212\uff1a") for line in messages)


def test_prepare_pride_msdt_docker_input_uses_only_file_name(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None)
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
        project_files=[],
    )

    monkeypatch.setattr(service, "resolve_project", lambda _: resolution)
    monkeypatch.setattr(service, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(service, "infer_attributes", lambda *_: _attributes())

    def fake_prepare_asset(asset):
        prepared_path = tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.mzML"
        prepared_path.parent.mkdir(parents=True, exist_ok=True)
        prepared_path.write_text("mzml", encoding="utf-8")
        return prepared_path

    monkeypatch.setattr(
        service,
        "resolve_asset",
        lambda *args, **kwargs: FileAsset(
            original_file_name="WT_5_Lys-c.raw",
            resolved_asset_type="mzml",
            matched_project_file="WT_5_Lys-c.mzML",
            download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.mzML",
            local_path=tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.mzML",
            prepared_path=tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.mzML",
            requires_conversion=False,
            asset_confidence=1.0,
            match_type="stem",
        ),
    )
    monkeypatch.setattr(service, "prepare_asset", fake_prepare_asset)

    bundle, result, prepared_path = service.prepare_pride_msdt_docker_input(
        task=task,
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
    )

    assert result.resolution.primary_project.project_accession == "PXD123456"
    assert prepared_path.name == "WT_5_Lys-c.mzML"
    assert bundle.converter_config_path.exists()
    config = json.loads(bundle.converter_config_path.read_text(encoding="utf-8"))
    assert config["generate_rawspectrum"]["data_path"].startswith("/workspace/")
    assert config["generate_fragpipe_search_result"]["workflow_path"].startswith("/workspace/workflows/")
    assert (tmp_path / "task_out" / "sage" / "sage_config.json").exists()


def test_prepare_pride_msdt_docker_input_continues_after_search_parameter_confirmation(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None)
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
    context = ProjectContext(project_accession="PXD123456", file_name="WT_5_Lys-c.raw", metadata={}, project_files=[])

    monkeypatch.setattr(service, "resolve_project", lambda _: resolution)
    monkeypatch.setattr(service, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(service, "infer_attributes", lambda *_: _attributes_requiring_search_review())
    monkeypatch.setattr(
        service,
        "resolve_asset",
        lambda *args, **kwargs: FileAsset(
            original_file_name="WT_5_Lys-c.raw",
            resolved_asset_type="mzml",
            matched_project_file="WT_5_Lys-c.mzML",
            download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.mzML",
            local_path=tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.mzML",
            prepared_path=tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.mzML",
            requires_conversion=False,
            asset_confidence=1.0,
            match_type="stem",
        ),
    )

    def fake_prepare_asset(asset):
        prepared_path = tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.mzML"
        prepared_path.parent.mkdir(parents=True, exist_ok=True)
        prepared_path.write_text("mzml", encoding="utf-8")
        return prepared_path

    monkeypatch.setattr(service, "prepare_asset", fake_prepare_asset)
    confirmations = []

    bundle, result, _ = service.prepare_pride_msdt_docker_input(
        task=task,
        output_dir=tmp_path / "task_out",
        reviewed_fasta_path=_reviewed_fasta(tmp_path),
        confirm_search_parameters=lambda reviewed_result: confirmations.append(reviewed_result) or True,
    )

    assert confirmations
    assert bundle.plan.needs_review is False
    assert result.plan.needs_review is False
    assert not any("搜库参数需要人工复核" in issue for issue in result.plan.blocking_issues)


def test_prepare_pride_msdt_docker_input_stops_before_download_when_plan_needs_review(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None)
    task = normalize_input("WT_5_Lys-c.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD_TOPDOWN",
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
        project_accession="PXD_TOPDOWN",
        file_name="WT_5_Lys-c.raw",
        metadata={
            "experimentTypes": MetadataValue(
                value=["Top-down proteomics"],
                source="pride.experimentTypes",
                source_level="project",
                completeness=1.0,
            )
        },
        project_files=[],
    )

    monkeypatch.setattr(service, "resolve_project", lambda _: resolution)
    monkeypatch.setattr(service, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(service, "infer_attributes", lambda *_: _attributes())
    monkeypatch.setattr(
        service,
        "resolve_asset",
        lambda *args, **kwargs: FileAsset(
            original_file_name="WT_5_Lys-c.raw",
            resolved_asset_type="raw",
            matched_project_file="WT_5_Lys-c.raw",
            download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.raw",
            local_path=tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.raw",
            prepared_path=tmp_path / "task_out" / "assets" / "prepared" / "WT_5_Lys-c.mzML",
            requires_conversion=True,
            asset_confidence=1.0,
            match_type="exact",
        ),
    )
    monkeypatch.setattr(service, "prepare_asset", lambda *_: (_ for _ in ()).throw(AssertionError("should not download")))

    with pytest.raises(ReviewRequiredError):
        service.prepare_pride_msdt_docker_input(task=task, output_dir=tmp_path / "task_out")

    assert (tmp_path / "task_out" / "review_queue.json").exists()
    assert not (tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.raw").exists()
