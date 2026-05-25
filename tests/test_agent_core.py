from pathlib import Path
from types import SimpleNamespace

from agent.agent_core.audit import write_agent_audit_artifacts, write_agent_audit_for_result
from agent.agent_core.models import AgentDecisionRecord, AgentRisk, GateAction
from agent.agent_core.observation import build_agent_observation
from agent.agent_core.decision_trace import build_agent_decision_trace
from agent.agent_core.plan import build_agent_plan_summary
from agent.models import (
    AttributeSet,
    AttributeValue,
    DdaExecutionPlan,
    FileAsset,
    MetadataValue,
    PridePlanResult,
    ProjectCandidate,
    ProjectContext,
    ProjectResolution,
)


def test_agent_decision_record_serializes_as_json_ready_dict():
    record = AgentDecisionRecord(
        id="D001",
        decision_type="species_inference",
        selected_value="Saccharomyces cerevisiae",
        confidence=0.95,
        evidence=["project metadata: Saccharomyces cerevisiae"],
        alternatives=[],
        risk_level=AgentRisk.LOW,
        gate_action="auto_accept",
    )

    data = record.model_dump(mode="json")

    assert data["id"] == "D001"
    assert data["risk_level"] == "low"
    assert data["gate_action"] == "auto_accept"


def _sample_resolution() -> ProjectResolution:
    return ProjectResolution(
        primary_project=ProjectCandidate(
            repository="iprox",
            project_accession="IPX0000753001",
            matched_file="Yeast_R3.raw",
            match_type="exact",
            match_score=100,
            metadata_consistency=0.95,
            evidence=["exact file match"],
        ),
        alternative_projects=[
            ProjectCandidate(
                repository="pride",
                project_accession="PXD071918",
                matched_file="Yeast_R3.raw",
                match_type="exact",
                match_score=100,
                metadata_consistency=0.6,
            )
        ],
        resolution_reason="Selected iProX by metadata consistency",
        resolution_confidence=1.0,
    )


def _sample_context() -> ProjectContext:
    return ProjectContext(
        repository="iprox",
        project_accession="IPX0000753001",
        file_name="Yeast_R3.raw",
        metadata={
            "organisms": MetadataValue(
                value=["Saccharomyces cerevisiae"],
                source="iprox.project",
                source_level="project",
                completeness=1.0,
            ),
            "instruments": MetadataValue(
                value=["Q Exactive"],
                source="iprox.project",
                source_level="project",
                completeness=1.0,
            ),
            "experimentTypes": MetadataValue(
                value=["DDA"],
                source="iprox.project",
                source_level="project",
                completeness=0.9,
            ),
        },
        sdrf_rows=[],
    )


def _sample_asset() -> FileAsset:
    return FileAsset(
        repository="iprox",
        original_file_name="Yeast_R3.raw",
        resolved_asset_type="raw",
        matched_project_file="Yeast_R3.raw",
        requires_conversion=True,
        asset_confidence=0.87,
        match_type="exact",
    )


def _attr(value, confidence=0.9, source="llm_confirmed", evidence="evidence") -> AttributeValue:
    return AttributeValue(
        value=value,
        confidence=confidence,
        source=source,
        evidence_excerpt=evidence,
        conflict_flag=False,
    )


def _sample_attributes() -> AttributeSet:
    return AttributeSet(
        acquisition_mode=_attr("DDA", 1.0, evidence="DDA metadata"),
        species=_attr("Saccharomyces cerevisiae", 1.0, evidence="yeast metadata"),
        instrument_name=_attr("Q Exactive", 0.9, source="iprox.project_xml", evidence="instrument metadata"),
        instrument_family=_attr("orbitrap", 0.9),
        enzyme=_attr("Trypsin/Lys-C", 0.94, evidence="lysine-specific endoproteinase and trypsin"),
        labeling_strategy=_attr("label-free", 0.8),
        fixed_mods=_attr([], 0.8),
        variable_mods=_attr(["M[15.99]"], 0.8),
        fractionation_hint=_attr(None, 0.0, source="none"),
        search_parameter_hints=_attr({"recommended_workflow_name": "Default.workflow"}, 0.9),
    )


def _sample_plan(tmp_path: Path) -> DdaExecutionPlan:
    return DdaExecutionPlan(
        task_id="task-1",
        source_file_name="Yeast_R3.raw",
        source_data_path=tmp_path / "Yeast_R3.mzML",
        raw_data_type="mzml",
        fasta_path=tmp_path / "uniprot_yeast.fasta",
        fasta_selection_mode="inferred",
        fasta_download_url="https://example.test/yeast.fasta",
        fragpipe_workflow_path=tmp_path / "Default.workflow",
        manifest_path=tmp_path / "fragpipe.fp-manifest",
        converter_config_path=tmp_path / "converter_config.json",
        rawspectrum_output_path=tmp_path / "rawspectrum.parquet",
        fragpipe_workdir=tmp_path / "fragpipe",
        expected_pin_path=tmp_path / "exp" / "Yeast_R3_edited.pin",
        expected_pin_glob=str(tmp_path / "exp" / "*_edited.pin"),
        output_paths={"fp_msdt": tmp_path / "msdt.parquet"},
        thread_num=4,
    )


def test_build_agent_observation_summarizes_project_metadata_and_asset():
    observation = build_agent_observation(
        "Yeast_R3.raw",
        _sample_resolution(),
        _sample_context(),
        asset=_sample_asset(),
    )

    assert observation.input_file == "Yeast_R3.raw"
    assert observation.selected_project["project_accession"] == "IPX0000753001"
    assert observation.selected_project["resolution_confidence"] == 1.0
    assert observation.selected_project["match_score"] == 100
    assert observation.selected_project["metadata_consistency"] == 0.95
    assert observation.repository_candidates[1]["project_accession"] == "PXD071918"
    assert observation.metadata_evidence["species"]["value"] == ["Saccharomyces cerevisiae"]
    assert observation.asset_evidence["resolved_asset_type"] == "raw"


def test_build_agent_observation_accepts_mapping_metadata_entries():
    context = SimpleNamespace(
        metadata={
            "organisms": {
                "value": ["Homo sapiens"],
                "source": "test.metadata",
                "source_level": "project",
                "completeness": 1.0,
            }
        }
    )

    observation = build_agent_observation("sample.raw", _sample_resolution(), context)

    assert observation.metadata_evidence["species"]["value"] == ["Homo sapiens"]
    assert observation.metadata_evidence["species"]["source"] == "test.metadata"


def test_build_agent_decision_trace_records_resolution_and_attributes():
    trace = build_agent_decision_trace(_sample_resolution(), _sample_attributes())

    assert trace[0].decision_type == "project_selection"
    assert trace[0].gate_action == "auto_accept"
    enzyme = next(item for item in trace if item.decision_type == "enzyme_inference")
    assert enzyme.selected_value == "Trypsin/Lys-C"
    assert enzyme.gate_action == "evidence_gated_accept"
    assert enzyme.risk_level == "medium"


def test_build_agent_decision_trace_records_file_database_workflow_and_resource_policy(tmp_path: Path):
    trace = build_agent_decision_trace(
        _sample_resolution(),
        _sample_attributes(),
        asset=_sample_asset(),
        plan=_sample_plan(tmp_path),
    )

    decision_types = {item.decision_type for item in trace}

    assert "file_matching" in decision_types
    assert "database_selection" in decision_types
    assert "workflow_selection" in decision_types
    assert "resource_policy_selection" in decision_types
    database = next(item for item in trace if item.decision_type == "database_selection")
    workflow = next(item for item in trace if item.decision_type == "workflow_selection")
    resource = next(item for item in trace if item.decision_type == "resource_policy_selection")
    assert database.selected_value["fasta_name"] == "uniprot_yeast.fasta"
    assert workflow.selected_value == "Default.workflow"
    assert resource.selected_value["thread_num"] == 4


def test_build_agent_plan_summary_records_workflow_database_and_parameters(tmp_path: Path):
    attributes = _sample_attributes().model_copy(
        update={
            "search_parameter_hints": _attr(
                {
                    "recommended_workflow_name": "Default.workflow",
                    "recommended_fasta_name": "uniprot_yeast.fasta",
                    "recommended_fasta_source": "UniProt",
                    "precursor_tol": "20ppm",
                    "fragment_tol": "20ppm",
                    "missed_cleavages": 3,
                },
                0.9,
            )
        }
    )

    summary = build_agent_plan_summary(_sample_plan(tmp_path), attributes)

    assert summary.selected_workflow["name"] == "Default.workflow"
    assert summary.selected_database["fasta_selection_mode"] == "inferred"
    assert summary.search_parameters["enzyme"] == "Trypsin/Lys-C"
    assert summary.search_parameters["missed_cleavages"] == 3
    assert summary.execution_gate == "allowed"


def test_build_agent_plan_summary_blocks_when_plan_requires_review(tmp_path: Path):
    plan = _sample_plan(tmp_path).model_copy(
        update={
            "needs_review": True,
            "blocking_issues": ["项目解析需要人工复核：Exact file name tie across repositories."],
        }
    )

    summary = build_agent_plan_summary(plan, _sample_attributes())

    assert summary.execution_gate == "review_required"
    assert "项目解析需要人工复核" in summary.blocking_issues[0]


def test_build_agent_plan_summary_blocks_when_any_key_decision_requires_review(tmp_path: Path):
    plan = _sample_plan(tmp_path)
    decisions = build_agent_decision_trace(
        _sample_resolution(),
        _sample_attributes(),
        asset=_sample_asset().model_copy(update={"asset_confidence": 0.2, "resolved_asset_type": "unknown"}),
        plan=plan,
    )

    summary = build_agent_plan_summary(plan, _sample_attributes(), decisions=decisions)

    assert any(item.gate_action == GateAction.REVIEW_REQUIRED for item in decisions)
    assert summary.execution_gate == "review_required"
    assert any("file_matching" in issue for issue in summary.blocking_issues)


def test_build_agent_decision_trace_records_rejected_conflicting_fasta_hint(tmp_path: Path):
    attributes = _sample_attributes().model_copy(
        update={
            "species": _attr("Oryza sativa", 0.95, evidence="rice metadata"),
            "search_parameter_hints": _attr(
                {
                    "recommended_fasta_name": "arabidopsis.fasta",
                    "recommended_fasta_url": "https://www.uniprot.org/proteomes/UP000006548",
                    "recommended_fasta_source": "UniProt",
                    "recommended_workflow_name": "Default.workflow",
                },
                0.9,
            ),
        }
    )
    plan = _sample_plan(tmp_path).model_copy(
        update={
            "fasta_path": tmp_path / "uniprot_oryza_sativa_UP000059680.fasta",
            "fasta_download_url": "https://rest.uniprot.org/uniprotkb/stream?compressed=false&format=fasta&query=%28proteome%3AUP000059680%29",
        }
    )

    trace = build_agent_decision_trace(_sample_resolution(), attributes, asset=_sample_asset(), plan=plan)

    database = next(item for item in trace if item.decision_type == "database_selection")
    assert database.selected_value["fasta_name"] == "uniprot_oryza_sativa_UP000059680.fasta"
    assert any(alt["value"] == "UP000006548" and alt["reason_rejected"] for alt in database.alternatives)


def test_write_agent_audit_artifacts_writes_three_json_files(tmp_path: Path):
    observation = build_agent_observation(
        "Yeast_R3.raw",
        _sample_resolution(),
        _sample_context(),
        asset=_sample_asset(),
    )
    decisions = build_agent_decision_trace(_sample_resolution(), _sample_attributes())
    plan_summary = build_agent_plan_summary(_sample_plan(tmp_path), _sample_attributes())

    paths = write_agent_audit_artifacts(tmp_path, observation, plan_summary, decisions)

    assert paths.observation.exists()
    assert paths.plan.exists()
    assert paths.decision_trace.exists()
    assert paths.observation.name == "agent_observation.json"
    assert "Trypsin/Lys-C" in paths.decision_trace.read_text(encoding="utf-8")


def test_write_agent_audit_for_result_uses_existing_pipeline_objects(tmp_path: Path):
    result = PridePlanResult(
        resolution=_sample_resolution(),
        context=_sample_context(),
        asset=_sample_asset(),
        attributes=_sample_attributes(),
        plan=_sample_plan(tmp_path),
    )

    paths = write_agent_audit_for_result(tmp_path, result)

    assert paths.observation.exists()
    assert paths.plan.exists()
    assert paths.decision_trace.exists()
