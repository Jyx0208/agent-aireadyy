import json
from pathlib import Path

import pytest

from agent.assets.preparer import AssetPreparationError
from agent.input.normalizer import normalize_input
from agent.models import (
    AttributeSet,
    AttributeValue,
    FileAsset,
    MetadataValue,
    PridePlanResult,
    ProjectCandidate,
    ProjectContext,
    ProjectResolution,
)
from agent.decision.dda import plan_dda_execution
from agent.orchestrator.pipeline import AgentService, ReviewRequiredError


class _DummyReasoner:
    def confirm_search_parameters(self, context, attributes):
        return {}


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
        search_parameter_hints=AttributeValue(value={"precursor_tol": "20ppm", "recommended_workflow_name": "Default.workflow"}, confidence=0.6, source="rule", evidence_excerpt="profile", conflict_flag=False),
    )


def _attributes_requiring_search_review() -> AttributeSet:
    attrs = _attributes()
    return attrs.model_copy(
        update={
            "search_parameter_hints": AttributeValue(
                value={"precursor_tol": "10 ppm", "fragment_tol": "0.02 Da", "database": "reviewed db", "recommended_workflow_name": "Default.workflow"},
                confidence=0.9,
                source="llm_confirmed",
                evidence_excerpt="LLM-confirmed parameters require human review.",
                conflict_flag=True,
            )
        }
    )


def _mouse_attributes() -> AttributeSet:
    attrs = _attributes()
    return attrs.model_copy(
        update={
            "species": AttributeValue(
                value="Mus musculus",
                confidence=0.95,
                source="llm_confirmed",
                evidence_excerpt="mouse",
                conflict_flag=False,
            ),
            "search_parameter_hints": AttributeValue(
                value={
                    "database": "UniProt mouse UP000000589 merged with cRAP",
                    "recommended_fasta_name": "UP000000589_M_musculus.fasta",
                    "recommended_fasta_url": None,
                    "recommended_fasta_source": "LLM found UniProt proteome UP000000589 in protocol",
                    "recommended_workflow_name": "Default.workflow",
                },
                confidence=0.9,
                source="llm_confirmed",
                evidence_excerpt="mouse database",
                conflict_flag=False,
            ),
        }
    )


def _reviewed_fasta(tmp_path: Path) -> Path:
    path = tmp_path / "reviewed_reference.fasta"
    path.write_text(">sp|P1|REVIEWED_TEST\nMPEPTIDEK\n", encoding="utf-8")
    return path


def _resolved_resolution(project_accession: str = "PXD_TEST", matched_file: str = "sample.raw") -> ProjectResolution:
    return ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession=project_accession,
            matched_file=matched_file,
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


def _write_minimal_dda_mzml(path: Path, instrument: str = "Q Exactive HF") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<mzML xmlns="http://psi.hupo.org/ms/mzml">
  <instrumentConfigurationList count="1">
    <instrumentConfiguration id="IC1">
      <cvParam cvRef="MS" accession="MS:1002523" name="{instrument}" value=""/>
      <componentList count="1">
        <analyzer order="1">
          <cvParam cvRef="MS" accession="MS:1000484" name="orbitrap" value=""/>
        </analyzer>
      </componentList>
    </instrumentConfiguration>
  </instrumentConfigurationList>
  <run id="run1">
    <spectrumList count="2">
      <spectrum id="scan=1">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="1"/>
      </spectrum>
      <spectrum id="scan=2">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="2"/>
      </spectrum>
    </spectrumList>
  </run>
</mzML>
""",
        encoding="utf-8",
    )


def test_validate_prepared_data_blocks_ms1_only_mzml_before_fragpipe(tmp_path: Path):
    mzml = tmp_path / "ms1_only.mzML"
    mzml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<mzML xmlns="http://psi.hupo.org/ms/mzml">
  <run id="run1">
    <spectrumList count="1">
      <spectrum id="scan=1">
        <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="1"/>
      </spectrum>
    </spectrumList>
  </run>
</mzML>
""",
        encoding="utf-8",
    )
    attributes = _attributes()
    resolution = _resolved_resolution()
    context = ProjectContext(project_accession="PXD000001", file_name="sample.raw")
    plan = plan_dda_execution(
        task_id="task-ms1-only",
        source_file_name="sample.raw",
        source_data_path=mzml,
        project_resolution=resolution,
        attributes=attributes,
        output_dir=tmp_path,
        project_context=context,
    )
    result = PridePlanResult(
        resolution=resolution,
        context=context,
        asset=FileAsset(original_file_name="sample.raw", resolved_asset_type="raw"),
        attributes=attributes,
        plan=plan,
    )
    service = AgentService(llm_reasoner=_DummyReasoner())

    updated = service.validate_prepared_data_for_plan(result, mzml)

    assert updated.plan.needs_review is True
    assert any("no MS2 spectra" in issue for issue in updated.plan.blocking_issues)


def test_plan_dda_run_from_pride_asset_uses_resolved_prepared_path(tmp_path: Path, monkeypatch):
    messages: list[str] = []
    service = AgentService(pride_client=None, reporter=messages.append, llm_reasoner=_DummyReasoner())
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
    service = AgentService(pride_client=None, llm_reasoner=_DummyReasoner())
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
        _write_minimal_dda_mzml(prepared_path)
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
    assert (tmp_path / "task_out" / "agent_observation.json").exists()
    assert (tmp_path / "task_out" / "agent_plan.json").exists()
    assert (tmp_path / "task_out" / "agent_decision_trace.json").exists()
    observation = json.loads((tmp_path / "task_out" / "agent_observation.json").read_text(encoding="utf-8"))
    agent_plan = json.loads((tmp_path / "task_out" / "agent_plan.json").read_text(encoding="utf-8"))
    decision_trace = json.loads((tmp_path / "task_out" / "agent_decision_trace.json").read_text(encoding="utf-8"))
    assert observation["selected_project"]["project_accession"] == "PXD123456"
    assert agent_plan["selected_workflow"]["name"] == "Default.workflow"
    assert any(decision["decision_type"] == "enzyme_inference" for decision in decision_trace["decisions"])


def test_prepare_pride_msdt_docker_input_continues_after_search_parameter_confirmation(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None, llm_reasoner=_DummyReasoner())
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
        _write_minimal_dda_mzml(prepared_path)
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


def test_prepare_pride_msdt_docker_input_auto_downloads_species_fasta(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None, llm_reasoner=_DummyReasoner())
    task = normalize_input("mouse.raw")
    resolution = _resolved_resolution(project_accession="PXD_MOUSE", matched_file="mouse.raw")
    context = ProjectContext(project_accession="PXD_MOUSE", file_name="mouse.raw", metadata={}, project_files=[])

    monkeypatch.setattr(service, "resolve_project", lambda _: resolution)
    monkeypatch.setattr(service, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(service, "infer_attributes", lambda *_: _mouse_attributes())
    monkeypatch.setattr(
        service,
        "resolve_asset",
        lambda *args, **kwargs: FileAsset(
            original_file_name="mouse.raw",
            resolved_asset_type="mzml",
            matched_project_file="mouse.mzML",
            download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/mouse.mzML",
            local_path=tmp_path / "task_out" / "assets" / "downloads" / "mouse.mzML",
            prepared_path=tmp_path / "task_out" / "assets" / "downloads" / "mouse.mzML",
            requires_conversion=False,
            asset_confidence=1.0,
            match_type="stem",
        ),
    )

    def fake_prepare_asset(asset):
        prepared_path = tmp_path / "task_out" / "assets" / "downloads" / "mouse.mzML"
        _write_minimal_dda_mzml(prepared_path)
        return prepared_path

    class FakePrideClient:
        def download_to_path(self, url, target_path, report=None):
            assert "UP000000589" in url
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_path).write_text(">sp|P1|MOUSE\nMPEPTIDEK\n", encoding="utf-8")
            return Path(target_path)

        def close(self):
            pass

    monkeypatch.setattr(service, "prepare_asset", fake_prepare_asset)
    monkeypatch.setattr("agent.execution.bundle.PrideClient", FakePrideClient)

    bundle, result, _ = service.prepare_pride_msdt_docker_input(task=task, output_dir=tmp_path / "task_out")

    assert result.plan.fasta_download_url is not None
    assert "UP000000589" in result.plan.fasta_download_url
    assert bundle.materialized_fasta_path.name == "uniprot_mouse_UP000000589.fasta"
    assert bundle.materialized_fasta_path.exists()


def test_prepare_pride_msdt_docker_input_confirms_llm_proteome_id_fasta(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None, llm_reasoner=_DummyReasoner())
    task = normalize_input("mouse.raw")
    context = ProjectContext(project_accession="PXD_MOUSE", file_name="mouse.raw", metadata={}, project_files=[])
    resolve_calls = []

    def fake_resolve_project(_):
        resolve_calls.append(True)
        return _resolved_resolution(project_accession="PXD_MOUSE", matched_file="mouse.raw")

    monkeypatch.setattr(service, "resolve_project", fake_resolve_project)
    monkeypatch.setattr(service, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(service, "infer_attributes", lambda *_: _mouse_attributes())
    monkeypatch.setattr(
        service,
        "resolve_asset",
        lambda *args, **kwargs: FileAsset(
            original_file_name="mouse.raw",
            resolved_asset_type="mzml",
            matched_project_file="mouse.mzML",
            download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/mouse.mzML",
            local_path=tmp_path / "task_out" / "assets" / "downloads" / "mouse.mzML",
            prepared_path=tmp_path / "task_out" / "assets" / "downloads" / "mouse.mzML",
            requires_conversion=False,
            asset_confidence=1.0,
            match_type="stem",
        ),
    )

    def fake_prepare_asset(asset):
        prepared_path = tmp_path / "task_out" / "assets" / "downloads" / "mouse.mzML"
        _write_minimal_dda_mzml(prepared_path)
        return prepared_path

    class FakePrideClient:
        def download_to_path(self, url, target_path, report=None):
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_path).write_text(">sp|P1|MOUSE\nMPEPTIDEK\n", encoding="utf-8")
            return Path(target_path)

        def close(self):
            pass

    recommendations = []
    monkeypatch.setattr(service, "prepare_asset", fake_prepare_asset)
    monkeypatch.setattr("agent.execution.bundle.PrideClient", FakePrideClient)

    bundle, _, _ = service.prepare_pride_msdt_docker_input(
        task=task,
        output_dir=tmp_path / "task_out",
        confirm_llm_recommended_fasta=lambda recommendation: recommendations.append(recommendation) or True,
    )

    assert recommendations
    assert len(resolve_calls) == 1
    assert "UP000000589" in recommendations[0]["url"]
    assert bundle.materialized_fasta_path.name == "uniprot_mouse_UP000000589.fasta"


def test_prepare_pride_msdt_docker_input_uses_mzml_instrument_for_multi_instrument_project(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None, llm_reasoner=_DummyReasoner())
    task = normalize_input("PRF_Q_2024_D_KLIO_1166_65810.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD_MULTI_INST",
            matched_file=task.file_name,
            match_type="exact",
            match_score=100,
            evidence=["exact"],
            metadata_consistency=1.0,
        ),
        alternative_projects=[],
        resolution_reason="exact",
        resolution_confidence=1.0,
        needs_review=False,
    )
    context = ProjectContext(
        project_accession="PXD_MULTI_INST",
        file_name=task.file_name,
        metadata={
            "instruments": MetadataValue(
                value=["Orbitrap Fusion Lumos", "Q Exactive HF"],
                source="pride.instruments",
                source_level="project",
                completeness=1.0,
            )
        },
        project_files=[],
    )
    attrs = _attributes().model_copy(
        update={
            "instrument_name": AttributeValue(
                value="Orbitrap Fusion Lumos; Q Exactive HF",
                confidence=0.5,
                source="pride.instruments",
                evidence_excerpt="multiple instruments",
                conflict_flag=True,
            ),
            "instrument_family": AttributeValue(
                value="unknown",
                confidence=0.4,
                source="pride.instruments",
                evidence_excerpt="multiple instruments",
                conflict_flag=True,
            ),
        }
    )

    def fake_prepare_asset(asset):
        prepared = tmp_path / "task_out" / "assets" / "prepared" / "PRF_Q_2024_D_KLIO_1166_65810.mzML"
        _write_minimal_dda_mzml(prepared, instrument="Q Exactive HF")
        return prepared

    class FakePrideClient:
        def download_to_path(self, url, target_path, report=None):
            assert "UP000005640" in url
            Path(target_path).parent.mkdir(parents=True, exist_ok=True)
            Path(target_path).write_text(">sp|P1|HUMAN\nMPEPTIDEK\n", encoding="utf-8")
            return Path(target_path)

        def close(self):
            pass

    monkeypatch.setattr(service, "resolve_project", lambda _: resolution)
    monkeypatch.setattr(service, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(service, "infer_attributes", lambda *_: attrs)
    monkeypatch.setattr(
        service,
        "resolve_asset",
        lambda *args, **kwargs: FileAsset(
            original_file_name=task.file_name,
            resolved_asset_type="raw",
            matched_project_file=task.file_name,
            download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/PRF_Q_2024_D_KLIO_1166_65810.raw",
            local_path=tmp_path / "task_out" / "assets" / "downloads" / task.file_name,
            prepared_path=tmp_path / "task_out" / "assets" / "prepared" / "PRF_Q_2024_D_KLIO_1166_65810.mzML",
            requires_conversion=True,
            asset_confidence=1.0,
            match_type="exact",
        ),
    )
    monkeypatch.setattr(service, "prepare_asset", fake_prepare_asset)
    monkeypatch.setattr("agent.execution.bundle.PrideClient", FakePrideClient)

    bundle, result, prepared_path = service.prepare_pride_msdt_docker_input(task=task, output_dir=tmp_path / "task_out")

    assert prepared_path.name == "PRF_Q_2024_D_KLIO_1166_65810.mzML"
    assert result.attributes.instrument_name.value == "Q Exactive HF"
    assert result.attributes.instrument_name.source == "mzml"
    assert result.plan.needs_review is False
    assert bundle.plan.needs_review is False


def test_prepare_pride_msdt_docker_input_stops_before_download_when_plan_needs_review(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None, llm_reasoner=_DummyReasoner())
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


def test_prepare_pride_msdt_docker_input_stops_before_download_when_asset_is_unresolved(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None, llm_reasoner=_DummyReasoner())
    task = normalize_input("WT_5_Lys-c.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD_ASSET",
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
        project_accession="PXD_ASSET",
        file_name="WT_5_Lys-c.raw",
        metadata={},
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
            resolved_asset_type="unknown",
            matched_project_file=None,
            asset_confidence=0.2,
            match_type="unresolved",
        ),
    )
    monkeypatch.setattr(service, "prepare_asset", lambda *_: (_ for _ in ()).throw(AssertionError("should not download")))

    with pytest.raises(ReviewRequiredError):
        service.prepare_pride_msdt_docker_input(task=task, output_dir=tmp_path / "task_out")

    review_queue = json.loads((tmp_path / "task_out" / "review_queue.json").read_text(encoding="utf-8"))
    reasons = " ".join(review_queue[0]["reasons"])
    assert "文件资产" in reasons or "asset" in reasons.lower()
    assert not (tmp_path / "task_out" / "assets" / "downloads").exists()


def test_prepare_pride_msdt_docker_input_writes_recovery_audit_when_asset_preparation_fails(tmp_path: Path, monkeypatch):
    service = AgentService(pride_client=None, llm_reasoner=_DummyReasoner())
    task = normalize_input("WT_5_Lys-c.raw")
    resolution = ProjectResolution(
        primary_project=ProjectCandidate(
            project_accession="PXD_CONVERT",
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
    context = ProjectContext(project_accession="PXD_CONVERT", file_name="WT_5_Lys-c.raw", metadata={}, project_files=[])
    asset = FileAsset(
        original_file_name="WT_5_Lys-c.raw",
        resolved_asset_type="raw",
        matched_project_file="WT_5_Lys-c.raw",
        download_url="https://ftp.pride.ebi.ac.uk/pride/data/archive/WT_5_Lys-c.raw",
        local_path=tmp_path / "task_out" / "assets" / "downloads" / "WT_5_Lys-c.raw",
        prepared_path=tmp_path / "task_out" / "assets" / "prepared" / "WT_5_Lys-c.mzML",
        requires_conversion=True,
        asset_confidence=1.0,
        match_type="exact",
    )

    monkeypatch.setattr(service, "resolve_project", lambda _: resolution)
    monkeypatch.setattr(service, "build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(service, "infer_attributes", lambda *_: _attributes())
    monkeypatch.setattr(service, "resolve_asset", lambda *args, **kwargs: asset)
    monkeypatch.setattr(
        service,
        "prepare_asset",
        lambda *_: (_ for _ in ()).throw(
            AssetPreparationError("ProteoWizard conversion failed", local_path=asset.local_path)
        ),
    )

    with pytest.raises(AssetPreparationError):
        service.prepare_pride_msdt_docker_input(task=task, output_dir=tmp_path / "task_out")

    recovery = json.loads((tmp_path / "task_out" / "recovery_audit.json").read_text(encoding="utf-8"))
    assert recovery["schema_version"] == "recovery-audit/v1"
    assert recovery["failure"]["stage"] == "asset_preparation"
    assert recovery["failure"]["category"] == "conversion_failure"
    assert recovery["recovery"]["decision"] == "retry_scheduled"
    assert recovery["recovery"]["allowed_action"] == "retry_conversion_with_fallback"
