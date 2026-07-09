from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field

from agent.ai_ready.chimeric_exporter import ChimericExportResult, export_chimeric_ai_ready
from agent.ai_ready.denovo_exporter import DenovoExportResult, export_denovo_ai_ready
from agent.ai_ready.fragment_intensity_exporter import (
    FragmentIntensityExportResult,
    export_fragment_intensity_ai_ready,
)
from agent.ai_ready.agent_run_locator import locate_agent_run_inputs, select_agent_run_ai_ready_inputs
from agent.ai_ready.input_locator import locate_ai_ready_inputs, select_ai_ready_inputs
from agent.ai_ready.psm_scoring_exporter import PsmScoringExportResult, export_psm_scoring_ai_ready
from agent.ai_ready.ptm_denovo_exporter import PtmDenovoExportResult, export_ptm_denovo_ai_ready
from agent.ai_ready.rt_exporter import RtExportResult, export_rt_ai_ready
from agent.discovery.agentic import AgenticDiscoveryPlan, AgenticDiscoveryPlanner
from agent.discovery.agentic_runner import AgenticDiscoveryRound, run_agentic_discovery
from agent.discovery.batch_bridge import load_batch_manifest
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.memory import DiscoveryMemory, generate_discovery_run_id
from agent.discovery.models import DatasetManifest, DatasetRequest
from agent.discovery.outcomes import write_discovery_batch_outcome_report
from agent.discovery.pipeline_handoff import write_pipeline_handoff
from agent.discovery.pride_discovery import discover_pride_dataset
from agent.discovery.task_build_plan import write_task_build_plan
from agent.discovery.task_readiness import annotate_manifest_task_readiness, normalize_task_type
from agent.models import JsonModel
from agent.utils import write_json


AllowedBuildAction = Literal[
    "run_discovery",
    "build_task_plan",
    "make_pipeline_handoff",
    "link_outcomes",
    "export_rt_ai_ready",
    "export_fragment_intensity_ai_ready",
    "export_psm_scoring_ai_ready",
    "export_denovo_ai_ready",
    "export_ptm_denovo_ai_ready",
    "export_chimeric_ai_ready",
    "locate_agent_run_inputs",
    "locate_ai_ready_inputs",
    "summarize_result",
    "stop_with_blocker",
]
BuildStatus = Literal[
    "completed",
    "ready_for_handoff",
    "needs_search_results",
    "planned_task",
    "blocked",
]

ALLOWED_ACTIONS: list[AllowedBuildAction] = [
    "run_discovery",
    "build_task_plan",
    "make_pipeline_handoff",
    "link_outcomes",
    "export_rt_ai_ready",
    "export_fragment_intensity_ai_ready",
    "export_psm_scoring_ai_ready",
    "export_denovo_ai_ready",
    "export_ptm_denovo_ai_ready",
    "export_chimeric_ai_ready",
    "locate_agent_run_inputs",
    "locate_ai_ready_inputs",
    "summarize_result",
    "stop_with_blocker",
]


class DatasetBuildIntent(JsonModel):
    prompt: str
    request: DatasetRequest
    task_type: str
    task_spec: dict[str, Any] = Field(default_factory=dict)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    allowed_actions: list[str] = Field(default_factory=lambda: list(ALLOWED_ACTIONS))
    notes: list[str] = Field(default_factory=list)


class DatasetBuildTraceStep(JsonModel):
    step_index: int
    action: AllowedBuildAction
    status: str
    thought: str = ""
    observation: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class AgenticDatasetBuildSummary(JsonModel):
    status: BuildStatus
    run_id: str
    task_type: str
    output_dir: str
    next_step: str
    selected_files: int = 0
    selected_projects: int = 0
    task_candidate_files: int = 0
    handoff_ready_files: int = 0
    rt_rows_out: int = 0
    rt_peptide_rows_out: int = 0
    fragment_intensity_rows_out: int = 0
    psm_scoring_rows_out: int = 0
    denovo_rows_out: int = 0
    ptm_denovo_rows_out: int = 0
    chimeric_rows_out: int = 0
    planned_task: bool = False
    planned_task_target_schema: str | None = None
    planned_task_missing_labels: list[str] = Field(default_factory=list)
    planned_task_next_steps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)


class AgenticDatasetBuildResult(JsonModel):
    intent: DatasetBuildIntent
    discovery_plan: AgenticDiscoveryPlan
    discovery_rounds: list[AgenticDiscoveryRound] = Field(default_factory=list)
    trace: list[DatasetBuildTraceStep] = Field(default_factory=list)
    summary: AgenticDatasetBuildSummary
    output_files: dict[str, str] = Field(default_factory=dict)


DiscoveryFunction = Callable[..., DatasetManifest]
RtExportFunction = Callable[..., RtExportResult]
FragmentIntensityExportFunction = Callable[..., FragmentIntensityExportResult]
PsmScoringExportFunction = Callable[..., PsmScoringExportResult]
DenovoExportFunction = Callable[..., DenovoExportResult]
PtmDenovoExportFunction = Callable[..., PtmDenovoExportResult]
ChimericExportFunction = Callable[..., ChimericExportResult]


def run_agentic_dataset_builder(
    *,
    prompt: str,
    request: DatasetRequest,
    output_dir: str | Path,
    planner: AgenticDiscoveryPlanner,
    task_type: str = "rt_prediction",
    memory: DiscoveryMemory | None = None,
    agentic_rounds: int = 1,
    search_results: list[str | Path] | None = None,
    peaklists: list[str | Path] | None = None,
    agent_run_dir: str | Path | None = None,
    search_dir: str | Path | None = None,
    batch_manifest: str | Path | None = None,
    project_accession: str | None = None,
    source_file: str | None = None,
    q_value_threshold: float = 0.01,
    probability_threshold: float = 0.9,
    require_confidence: bool = False,
    search_engine: str | None = None,
    max_input_file_mb: int = 2048,
    allow_large_input: bool = False,
    discovery_func: DiscoveryFunction = discover_pride_dataset,
    rt_export_func: RtExportFunction = export_rt_ai_ready,
    fragment_intensity_export_func: FragmentIntensityExportFunction = export_fragment_intensity_ai_ready,
    psm_scoring_export_func: PsmScoringExportFunction = export_psm_scoring_ai_ready,
    denovo_export_func: DenovoExportFunction = export_denovo_ai_ready,
    ptm_denovo_export_func: PtmDenovoExportFunction = export_ptm_denovo_ai_ready,
    chimeric_export_func: ChimericExportFunction = export_chimeric_ai_ready,
) -> AgenticDatasetBuildResult:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_task_type = normalize_task_type(task_type) or "rt_prediction"
    task_profile = _task_profile(normalized_task_type)
    trace: list[DatasetBuildTraceStep] = []
    warnings: list[str] = []
    blockers: list[str] = []
    output_files: dict[str, str] = {}

    discovery_result = run_agentic_discovery(
        request=request,
        planner=planner,
        prompt=prompt,
        memory=memory,
        max_rounds=agentic_rounds,
        task_profile=task_profile,
        discovery_func=discovery_func,
    )
    manifest = annotate_manifest_task_readiness(discovery_result.manifest, normalized_task_type)
    run_id = manifest.run_id or generate_discovery_run_id(request)
    manifest = _with_run_summary(
        manifest,
        run_id=run_id,
        discovery_plan=discovery_result.plan,
        discovery_rounds=discovery_result.rounds,
    )
    trace.append(
        DatasetBuildTraceStep(
            step_index=1,
            action="run_discovery",
            status="completed",
            thought="Use the LLM query plan, then let deterministic discovery select and validate PRIDE files.",
            observation=_compact_observation(manifest.summary),
            outputs={
                "selected_projects": manifest.summary.get("selected_projects", len(manifest.projects)),
                "selected_files": manifest.summary.get("selected_files", len(manifest.files)),
                "agentic_rounds": len(discovery_result.rounds),
            },
            warnings=discovery_result.plan.warnings,
        )
    )
    warnings.extend(discovery_result.plan.warnings)

    manifest_paths = write_dataset_manifest(manifest, output_dir)
    output_files.update(_string_paths(manifest_paths))
    discovery_plan_path = output_dir / "agentic_discovery_plan.json"
    discovery_rounds_path = output_dir / "agentic_discovery_rounds.json"
    write_json(discovery_plan_path, discovery_result.plan.model_dump(mode="json"))
    write_json(discovery_rounds_path, [item.model_dump(mode="json") for item in discovery_result.rounds])
    output_files["agentic_discovery_plan"] = str(discovery_plan_path)
    output_files["agentic_discovery_rounds"] = str(discovery_rounds_path)

    task_paths = write_task_build_plan(
        manifest,
        output_dir,
        normalized_task_type,
        selection="auto",
    )
    output_files.update(_string_paths(task_paths))
    task_summary = _read_json(task_paths["task_build_summary"])
    trace.append(
        DatasetBuildTraceStep(
            step_index=2,
            action="build_task_plan",
            status="completed",
            thought="Translate discovery candidates into task-specific label generation candidates.",
            observation=_compact_observation(task_summary),
            outputs={
                "candidate_files": task_summary.get("candidate_files", 0),
                "next_step": task_summary.get("next_step"),
            },
        )
    )

    handoff_paths = write_pipeline_handoff(manifest, output_dir, selection="auto")
    output_files.update(_string_paths(handoff_paths))
    handoff_summary = _read_json(handoff_paths["pipeline_handoff_summary"])
    handoff_ready = int(handoff_summary.get("ready_for_batch_parameters") or 0)
    handoff_blockers: list[str] = []
    if handoff_ready == 0:
        handoff_blockers.append("no_ready_for_batch_parameters_files")
        blockers.extend(handoff_blockers)
    trace.append(
        DatasetBuildTraceStep(
            step_index=3,
            action="make_pipeline_handoff",
            status="completed" if handoff_ready else "blocked",
            thought="Prepare a safe handoff to the existing parameters-only batch entrypoint.",
            observation=_compact_observation(handoff_summary),
            outputs={
                "ready_for_batch_parameters": handoff_ready,
                "handoff_status_counts": handoff_summary.get("handoff_status_counts", {}),
            },
            blockers=handoff_blockers,
        )
    )

    if batch_manifest is not None:
        batch_payload = load_batch_manifest(batch_manifest)
        outcome_dir = output_dir / "outcomes"
        outcome_paths = write_discovery_batch_outcome_report(manifest, batch_payload, outcome_dir)
        output_files.update({f"outcome_{key}": str(path) for key, path in outcome_paths.items()})
        outcome_report = _read_json(outcome_paths["discovery_batch_outcome_report"])
        trace.append(
            DatasetBuildTraceStep(
                step_index=4,
                action="link_outcomes",
                status="completed",
                thought="Link batch status back to discovery-selected files before label export.",
                observation=_compact_observation(outcome_report),
                inputs={"batch_manifest": str(batch_manifest)},
                outputs={
                    "completed_items": outcome_report.get("completed_items", 0),
                    "failed_items": outcome_report.get("failed_items", 0),
                    "submitted_success_rate": outcome_report.get("submitted_success_rate", 0.0),
                },
            )
        )
    else:
        trace.append(
            DatasetBuildTraceStep(
                step_index=4,
                action="link_outcomes",
                status="skipped",
                thought="No batch manifest was supplied, so outcome linking is deferred.",
                observation="Batch outcome linking can run after parameters/search jobs exist.",
            )
        )

    rt_result: RtExportResult | None = None
    fragment_result: FragmentIntensityExportResult | None = None
    psm_result: PsmScoringExportResult | None = None
    denovo_result: DenovoExportResult | None = None
    ptm_denovo_result: PtmDenovoExportResult | None = None
    chimeric_result: ChimericExportResult | None = None
    search_results = list(search_results or [])
    peaklists = list(peaklists or [])
    if agent_run_dir is not None and not search_results:
        locator_dir = output_dir / "agent_run_input_locator"
        locator = locate_agent_run_inputs(
            agent_run_dir=agent_run_dir,
            output_dir=locator_dir,
            max_input_file_mb=max_input_file_mb,
            allow_large_input=allow_large_input,
        )
        located_search_results, located_peaklists = select_agent_run_ai_ready_inputs(
            locator,
            task_type=normalized_task_type,
        )
        search_results = list(located_search_results)
        if not peaklists:
            peaklists = list(located_peaklists)
        output_files.update(
            {
                "agent_run_input_locations_json": locator.json_path,
                "agent_run_input_locations_csv": locator.csv_path,
            }
        )
        trace.append(
            DatasetBuildTraceStep(
                step_index=5,
                action="locate_agent_run_inputs",
                status=locator.status,
                thought="Read an existing original agent run directory and locate reusable TSV/PIN/MGF inputs.",
                observation=_compact_observation(locator.summary),
                inputs={"agent_run_dir": str(agent_run_dir)},
                outputs={
                    "search_results": [str(path) for path in search_results],
                    "peaklists": [str(path) for path in peaklists],
                    "located_artifacts": locator.summary.get("located_artifacts", 0),
                    "generic_ai_ready_available": locator.summary.get("generic_ai_ready_available", False),
                },
                warnings=list(locator.summary.get("warnings") or []),
            )
        )
        warnings.extend(locator.summary.get("warnings") or [])
        if not search_results and locator.summary.get("generic_ai_ready_available"):
            warnings.append("generic_ai_ready_available")
            warnings.append("task_specific_training_labels_not_found")
    if search_dir is not None and not search_results:
        locator_dir = output_dir / "ai_ready_input_locator"
        locator = locate_ai_ready_inputs(search_dir=search_dir, output_dir=locator_dir)
        located_search_results, located_peaklists = select_ai_ready_inputs(locator, task_type=normalized_task_type)
        search_results = list(located_search_results)
        if not peaklists:
            peaklists = list(located_peaklists)
        output_files.update(
            {
                "ai_ready_input_locations_json": locator.json_path,
                "ai_ready_input_locations_csv": locator.csv_path,
            }
        )
        trace.append(
            DatasetBuildTraceStep(
                step_index=5,
                action="locate_ai_ready_inputs",
                status=locator.status,
                thought="Use the local search result directory to find TSV/PIN/MGF inputs for the selected task.",
                observation=_compact_observation(locator.summary),
                inputs={"search_dir": str(search_dir)},
                outputs={
                    "search_results": [str(path) for path in search_results],
                    "peaklists": [str(path) for path in peaklists],
                    "located_files": locator.summary.get("located_files", 0),
                },
                warnings=list(locator.summary.get("warnings") or []),
            )
        )
        warnings.extend(locator.summary.get("warnings") or [])
    if task_profile.implementation_status == "planned":
        _planned_task_gap_report(
            task_profile=task_profile,
            task_summary=task_summary,
            handoff_summary=handoff_summary,
            output_dir=output_dir,
        )
        output_files.update(
            {
                "planned_task_gap_report_json": str(output_dir / "planned_task_gap_report.json"),
                "planned_task_gap_report_md": str(output_dir / "planned_task_gap_report.md"),
            }
        )
        trace.append(
            DatasetBuildTraceStep(
                step_index=5,
                action="stop_with_blocker",
                status="planned_task",
                thought="This task is connected at discovery and planning level, but its exporter is intentionally not implemented yet.",
                observation=json.dumps(
                    {
                        "target_schema": task_profile.ai_ready_target_schema,
                        "required_labels": task_profile.required_labels,
                        "next_pipeline_steps": task_profile.next_pipeline_steps,
                    },
                    ensure_ascii=False,
                ),
                outputs={
                    "planned_task_gap_report_json": str(output_dir / "planned_task_gap_report.json"),
                    "planned_task_gap_report_md": str(output_dir / "planned_task_gap_report.md"),
                },
                warnings=["planned_task_exporter_not_implemented"],
            )
        )
        warnings.append("planned_task_exporter_not_implemented")
    elif normalized_task_type == "rt_prediction" and search_results:
        rt_dir = output_dir / "rt_ai_ready"
        rt_result = rt_export_func(
            search_results=search_results,
            output_dir=rt_dir,
            project_accession=project_accession,
            source_file=source_file,
            task_build_plan=task_paths["task_build_plan"],
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
        )
        output_files.update(
            {
                "rt_train_parquet": rt_result.output_parquet,
                "rt_train_preview_csv": rt_result.preview_csv,
                "rt_train_peptide_parquet": rt_result.peptide_parquet,
                "rt_train_peptide_preview_csv": rt_result.peptide_preview_csv,
                "rt_export_report": rt_result.report_json,
                "rt_validation_report": rt_result.validation_report_json,
                "rt_schema": rt_result.schema_json_path,
            }
        )
        trace.append(
            DatasetBuildTraceStep(
                step_index=5,
                action="export_rt_ai_ready",
                status="completed",
                thought="Consume existing search result TSVs and export RT AI-ready training tables.",
                observation=json.dumps(
                    {
                        "rows_in": rt_result.rows_in,
                        "rows_out": rt_result.rows_out,
                        "peptide_rows_out": rt_result.peptide_rows_out,
                        "warnings": rt_result.warnings,
                    },
                    ensure_ascii=False,
                ),
                inputs={"search_results": [str(path) for path in search_results]},
                outputs={
                    "rows_out": rt_result.rows_out,
                    "peptide_rows_out": rt_result.peptide_rows_out,
                },
                warnings=rt_result.warnings,
            )
        )
        warnings.extend(rt_result.warnings)
    elif normalized_task_type == "rt_prediction":
        blockers.append("needs_search_results")
        trace.append(
            DatasetBuildTraceStep(
                step_index=5,
                action="stop_with_blocker",
                status="blocked",
                thought="RT AI-ready export requires existing search result TSVs.",
                observation="Provide --search-result after FragPipe/Sage/MSFragger has produced psm.tsv or peptide.tsv.",
                blockers=["needs_search_results"],
            )
        )
    elif normalized_task_type == "fragment_intensity_prediction":
        if not search_results:
            blockers.append("needs_search_results")
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="stop_with_blocker",
                    status="blocked",
                    thought="Fragment intensity export requires existing search result TSVs.",
                    observation="Provide --search-result after a search engine has produced PSM tables.",
                    blockers=["needs_search_results"],
                )
            )
        elif not peaklists:
            blockers.append("needs_peaklist")
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="stop_with_blocker",
                    status="blocked",
                    thought="Fragment intensity export requires an MGF peaklist for spectrum intensities.",
                    observation="Provide --peaklist spectra.mgf together with --search-result.",
                    blockers=["needs_peaklist"],
                )
            )
        else:
            fragment_dir = output_dir / "fragment_intensity_ai_ready"
            fragment_result = fragment_intensity_export_func(
                search_results=search_results,
                peaklists=peaklists,
                output_dir=fragment_dir,
                project_accession=project_accession,
                source_file=source_file,
                task_build_plan=task_paths["task_build_plan"],
                q_value_threshold=q_value_threshold,
                probability_threshold=probability_threshold,
                search_engine=search_engine,
            )
            output_files.update(
                {
                    "fragment_intensity_train_parquet": fragment_result.output_parquet,
                    "fragment_intensity_preview_csv": fragment_result.preview_csv,
                    "fragment_intensity_export_report": fragment_result.report_json,
                    "fragment_intensity_schema": fragment_result.schema_json_path,
                }
            )
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="export_fragment_intensity_ai_ready",
                    status="completed",
                    thought="Consume existing PSM TSVs plus MGF peaklists and export fragment intensity training rows.",
                    observation=json.dumps(
                        {
                            "rows_in": fragment_result.rows_in,
                            "rows_out": fragment_result.rows_out,
                            "warnings": fragment_result.warnings,
                        },
                        ensure_ascii=False,
                    ),
                    inputs={
                        "search_results": [str(path) for path in search_results],
                        "peaklists": [str(path) for path in peaklists],
                    },
                    outputs={"rows_out": fragment_result.rows_out},
                    warnings=fragment_result.warnings,
                )
            )
            warnings.extend(fragment_result.warnings)
    elif normalized_task_type == "psm_scoring":
        if not search_results:
            blockers.append("needs_search_results")
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="stop_with_blocker",
                    status="blocked",
                    thought="PSM scoring export requires existing search result TSV/PIN tables.",
                    observation="Provide --search-result with target/decoy labels.",
                    blockers=["needs_search_results"],
                )
            )
        else:
            psm_dir = output_dir / "psm_scoring_ai_ready"
            try:
                psm_result = psm_scoring_export_func(
                    search_results=search_results,
                    output_dir=psm_dir,
                    project_accession=project_accession,
                    source_file=source_file,
                    task_build_plan=task_paths["task_build_plan"],
                    search_engine=search_engine,
                )
            except ValueError as exc:
                if "target_decoy_label_missing" not in str(exc):
                    raise
                blockers.append("needs_target_decoy_labels")
                trace.append(
                    DatasetBuildTraceStep(
                        step_index=5,
                        action="stop_with_blocker",
                        status="blocked",
                        thought="PSM scoring export requires target/decoy labels.",
                        observation=str(exc),
                        blockers=["needs_target_decoy_labels"],
                    )
                )
            if psm_result is not None:
                output_files.update(
                    {
                        "psm_scoring_train_parquet": psm_result.output_parquet,
                        "psm_scoring_preview_csv": psm_result.preview_csv,
                        "psm_scoring_export_report": psm_result.report_json,
                        "psm_scoring_schema": psm_result.schema_json_path,
                    }
                )
                trace.append(
                    DatasetBuildTraceStep(
                        step_index=5,
                        action="export_psm_scoring_ai_ready",
                        status="completed",
                        thought="Consume existing target/decoy PSM tables and export PSM scoring training rows.",
                        observation=json.dumps(
                            {
                                "rows_in": psm_result.rows_in,
                                "rows_out": psm_result.rows_out,
                                "target_count": psm_result.target_count,
                                "decoy_count": psm_result.decoy_count,
                                "warnings": psm_result.warnings,
                            },
                            ensure_ascii=False,
                        ),
                        inputs={"search_results": [str(path) for path in search_results]},
                        outputs={"rows_out": psm_result.rows_out},
                        warnings=psm_result.warnings,
                    )
                )
                warnings.extend(psm_result.warnings)
    elif normalized_task_type == "denovo":
        if not search_results:
            blockers.append("needs_search_results")
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="stop_with_blocker",
                    status="blocked",
                    thought="De novo AI-ready export requires existing search result TSVs for high-confidence sequence labels.",
                    observation="Provide --search-result after a search engine has produced PSM or peptide tables.",
                    blockers=["needs_search_results"],
                )
            )
        elif not peaklists:
            blockers.append("needs_peaklist")
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="stop_with_blocker",
                    status="blocked",
                    thought="De novo AI-ready export requires an MGF peaklist for spectrum inputs.",
                    observation="Provide --peaklist spectra.mgf together with --search-result.",
                    blockers=["needs_peaklist"],
                )
            )
        else:
            denovo_dir = output_dir / "denovo_ai_ready"
            denovo_result = denovo_export_func(
                search_results=search_results,
                peaklists=peaklists,
                output_dir=denovo_dir,
                project_accession=project_accession,
                source_file=source_file,
                task_build_plan=task_paths["task_build_plan"],
                q_value_threshold=q_value_threshold,
                probability_threshold=probability_threshold,
                search_engine=search_engine,
            )
            output_files.update(
                {
                    "denovo_train_parquet": denovo_result.output_parquet,
                    "denovo_preview_csv": denovo_result.preview_csv,
                    "denovo_export_report": denovo_result.report_json,
                    "denovo_schema": denovo_result.schema_json_path,
                }
            )
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="export_denovo_ai_ready",
                    status="completed",
                    thought="Consume existing PSM TSVs plus MGF peaklists and export supervised spectrum-sequence pairs.",
                    observation=json.dumps(
                        {
                            "rows_in": denovo_result.rows_in,
                            "rows_out": denovo_result.rows_out,
                            "warnings": denovo_result.warnings,
                        },
                        ensure_ascii=False,
                    ),
                    inputs={
                        "search_results": [str(path) for path in search_results],
                        "peaklists": [str(path) for path in peaklists],
                    },
                    outputs={"rows_out": denovo_result.rows_out},
                    warnings=denovo_result.warnings,
                )
            )
            warnings.extend(denovo_result.warnings)
    elif normalized_task_type == "ptm_denovo":
        if not search_results:
            blockers.append("needs_search_results")
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="stop_with_blocker",
                    status="blocked",
                    thought="PTM-aware de novo export requires existing PTM search result TSVs.",
                    observation="Provide --search-result containing modified peptide labels.",
                    blockers=["needs_search_results"],
                )
            )
        elif not peaklists:
            blockers.append("needs_peaklist")
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="stop_with_blocker",
                    status="blocked",
                    thought="PTM-aware de novo export requires an MGF peaklist for spectrum inputs.",
                    observation="Provide --peaklist spectra.mgf together with PTM search results.",
                    blockers=["needs_peaklist"],
                )
            )
        else:
            ptm_denovo_dir = output_dir / "ptm_denovo_ai_ready"
            ptm_denovo_result = ptm_denovo_export_func(
                search_results=search_results,
                peaklists=peaklists,
                output_dir=ptm_denovo_dir,
                project_accession=project_accession,
                source_file=source_file,
                task_build_plan=task_paths["task_build_plan"],
                q_value_threshold=q_value_threshold,
                probability_threshold=probability_threshold,
                search_engine=search_engine,
            )
            if ptm_denovo_result.rows_out == 0 and (
                ptm_denovo_result.filter_counts.get("missing_modified_sequence")
                or ptm_denovo_result.filter_counts.get("missing_required_columns")
            ):
                blockers.append("needs_modified_sequence_labels")
            output_files.update(
                {
                    "ptm_denovo_train_parquet": ptm_denovo_result.output_parquet,
                    "ptm_denovo_preview_csv": ptm_denovo_result.preview_csv,
                    "ptm_denovo_export_report": ptm_denovo_result.report_json,
                    "ptm_denovo_schema": ptm_denovo_result.schema_json_path,
                }
            )
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="export_ptm_denovo_ai_ready",
                    status="completed" if ptm_denovo_result.rows_out else "blocked",
                    thought="Consume existing modified PSM TSVs plus MGF peaklists and export PTM-aware de novo rows.",
                    observation=json.dumps(
                        {
                            "rows_in": ptm_denovo_result.rows_in,
                            "rows_out": ptm_denovo_result.rows_out,
                            "warnings": ptm_denovo_result.warnings,
                            "filter_counts": ptm_denovo_result.filter_counts,
                        },
                        ensure_ascii=False,
                    ),
                    inputs={
                        "search_results": [str(path) for path in search_results],
                        "peaklists": [str(path) for path in peaklists],
                    },
                    outputs={"rows_out": ptm_denovo_result.rows_out},
                    warnings=ptm_denovo_result.warnings,
                    blockers=["needs_modified_sequence_labels"] if "needs_modified_sequence_labels" in blockers else [],
                )
            )
            warnings.extend(ptm_denovo_result.warnings)
    elif normalized_task_type == "chimeric_interpretation":
        if not search_results:
            blockers.append("needs_search_results")
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="stop_with_blocker",
                    status="blocked",
                    thought="Chimeric export requires existing PSM TSVs with candidate multi-peptide assignments.",
                    observation="Provide --search-result after a search engine has produced PSM tables.",
                    blockers=["needs_search_results"],
                )
            )
        elif not peaklists:
            blockers.append("needs_peaklist")
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="stop_with_blocker",
                    status="blocked",
                    thought="Chimeric export requires an MGF peaklist for spectrum inputs.",
                    observation="Provide --peaklist spectra.mgf together with --search-result.",
                    blockers=["needs_peaklist"],
                )
            )
        else:
            chimeric_dir = output_dir / "chimeric_ai_ready"
            chimeric_result = chimeric_export_func(
                search_results=search_results,
                peaklists=peaklists,
                output_dir=chimeric_dir,
                project_accession=project_accession,
                source_file=source_file,
                task_build_plan=task_paths["task_build_plan"],
                q_value_threshold=q_value_threshold,
                probability_threshold=probability_threshold,
                search_engine=search_engine,
            )
            if chimeric_result.rows_out == 0 and chimeric_result.filter_counts.get("no_multi_peptide_assignment"):
                blockers.append("no_multi_peptide_assignment")
            output_files.update(
                {
                    "chimeric_train_parquet": chimeric_result.output_parquet,
                    "chimeric_preview_csv": chimeric_result.preview_csv,
                    "chimeric_export_report": chimeric_result.report_json,
                    "chimeric_schema": chimeric_result.schema_json_path,
                }
            )
            trace.append(
                DatasetBuildTraceStep(
                    step_index=5,
                    action="export_chimeric_ai_ready",
                    status="completed" if chimeric_result.rows_out else "blocked",
                    thought="Consume existing PSM TSVs plus MGF peaklists and export conservative multi-peptide spectrum labels.",
                    observation=json.dumps(
                        {
                            "rows_in": chimeric_result.rows_in,
                            "rows_out": chimeric_result.rows_out,
                            "warnings": chimeric_result.warnings,
                            "filter_counts": chimeric_result.filter_counts,
                        },
                        ensure_ascii=False,
                    ),
                    inputs={
                        "search_results": [str(path) for path in search_results],
                        "peaklists": [str(path) for path in peaklists],
                    },
                    outputs={"rows_out": chimeric_result.rows_out},
                    warnings=chimeric_result.warnings,
                    blockers=["no_multi_peptide_assignment"] if "no_multi_peptide_assignment" in blockers else [],
                )
            )
            warnings.extend(chimeric_result.warnings)

    status, next_step = _resolve_build_status(
        task_type=normalized_task_type,
        task_implementation_status=task_profile.implementation_status,
        handoff_ready=handoff_ready,
        search_results=search_results,
        rt_result=rt_result,
        fragment_result=fragment_result,
        psm_result=psm_result,
        denovo_result=denovo_result,
        ptm_denovo_result=ptm_denovo_result,
        chimeric_result=chimeric_result,
        blockers=blockers,
    )
    summary = AgenticDatasetBuildSummary(
        status=status,
        run_id=run_id,
        task_type=normalized_task_type,
        output_dir=str(output_dir),
        next_step=next_step,
        selected_files=int(manifest.summary.get("selected_files") or len(manifest.files)),
        selected_projects=int(manifest.summary.get("selected_projects") or len(manifest.projects)),
        task_candidate_files=int(task_summary.get("candidate_files") or 0),
        handoff_ready_files=handoff_ready,
        rt_rows_out=rt_result.rows_out if rt_result is not None else 0,
        rt_peptide_rows_out=rt_result.peptide_rows_out if rt_result is not None else 0,
        fragment_intensity_rows_out=fragment_result.rows_out if fragment_result is not None else 0,
        psm_scoring_rows_out=psm_result.rows_out if psm_result is not None else 0,
        denovo_rows_out=denovo_result.rows_out if denovo_result is not None else 0,
        ptm_denovo_rows_out=ptm_denovo_result.rows_out if ptm_denovo_result is not None else 0,
        chimeric_rows_out=chimeric_result.rows_out if chimeric_result is not None else 0,
        planned_task=task_profile.implementation_status == "planned",
        planned_task_target_schema=task_profile.ai_ready_target_schema if task_profile.implementation_status == "planned" else None,
        planned_task_missing_labels=task_profile.required_labels if task_profile.implementation_status == "planned" else [],
        planned_task_next_steps=task_profile.next_pipeline_steps if task_profile.implementation_status == "planned" else [],
        warnings=_dedupe(warnings),
        blockers=_dedupe(blockers),
        files=output_files,
    )
    trace.append(
        DatasetBuildTraceStep(
            step_index=6,
            action="summarize_result",
            status=status,
            thought="Summarize what is complete and what the next controlled action should be.",
            observation=summary.model_dump_json(),
            outputs={"next_step": next_step, "status": status},
            warnings=summary.warnings,
            blockers=summary.blockers,
        )
    )

    intent = DatasetBuildIntent(
        prompt=prompt,
        request=request,
        task_type=normalized_task_type,
        task_spec=discovery_result.plan.task_spec.model_dump(mode="json"),
        hard_constraints=request.model_dump(mode="json"),
        notes=[
            "LLM plans discovery strategy and explanation only.",
            "Deterministic validators keep authority over validity, handoff, and RT export.",
        ],
    )
    result = AgenticDatasetBuildResult(
        intent=intent,
        discovery_plan=discovery_result.plan,
        discovery_rounds=discovery_result.rounds,
        trace=trace,
        summary=summary,
        output_files=output_files,
    )
    _write_builder_outputs(result, output_dir)
    return result


def _task_profile(task_type: str):
    from agent.discovery.task_profiles import get_task_profile

    return get_task_profile(task_type)


def _with_run_summary(
    manifest: DatasetManifest,
    *,
    run_id: str,
    discovery_plan: AgenticDiscoveryPlan,
    discovery_rounds: list[AgenticDiscoveryRound],
) -> DatasetManifest:
    summary = dict(manifest.summary)
    summary["run_id"] = run_id
    summary["agentic_builder"] = {
        "enabled": True,
        "allowed_actions": list(ALLOWED_ACTIONS),
        "agentic_discovery_rounds": len(discovery_rounds),
        "warnings": discovery_plan.warnings,
        "suggested_next_queries": discovery_plan.suggested_next_queries,
    }
    return manifest.model_copy(update={"run_id": run_id, "summary": summary})


def _resolve_build_status(
    *,
    task_type: str,
    task_implementation_status: str,
    handoff_ready: int,
    search_results: list[str | Path],
    rt_result: RtExportResult | None,
    fragment_result: FragmentIntensityExportResult | None,
    psm_result: PsmScoringExportResult | None,
    denovo_result: DenovoExportResult | None,
    ptm_denovo_result: PtmDenovoExportResult | None,
    chimeric_result: ChimericExportResult | None,
    blockers: list[str],
) -> tuple[BuildStatus, str]:
    if rt_result is not None and rt_result.rows_out > 0:
        return "completed", "review_rt_export_report"
    if rt_result is not None:
        return "blocked", "review_rt_export_report_and_filter_counts"
    if fragment_result is not None and fragment_result.rows_out > 0:
        return "completed", "review_fragment_intensity_export_report"
    if fragment_result is not None:
        return "blocked", "review_fragment_intensity_export_report_and_spectrum_matching"
    if psm_result is not None and psm_result.rows_out > 0:
        return "completed", "review_psm_scoring_export_report"
    if psm_result is not None:
        return "blocked", "review_psm_scoring_export_report_and_target_decoy_labels"
    if denovo_result is not None and denovo_result.rows_out > 0:
        return "completed", "review_denovo_export_report"
    if denovo_result is not None:
        return "blocked", "review_denovo_export_report_and_spectrum_matching"
    if ptm_denovo_result is not None and ptm_denovo_result.rows_out > 0:
        return "completed", "review_ptm_denovo_export_report"
    if ptm_denovo_result is not None:
        return "blocked", "review_ptm_denovo_export_report_and_label_quality"
    if chimeric_result is not None and chimeric_result.rows_out > 0:
        return "completed", "review_chimeric_export_report"
    if chimeric_result is not None:
        return "blocked", "review_chimeric_export_report_and_multi_peptide_labels"
    if task_implementation_status == "planned":
        if handoff_ready:
            return "planned_task", "run_batch_parameters_then_implement_task_exporter"
        return "planned_task", "refine_discovery_or_review_candidates"
    if handoff_ready == 0:
        return "blocked", "refine_discovery_or_review_candidates"
    if task_type in {"rt_prediction", "fragment_intensity_prediction", "psm_scoring", "denovo", "ptm_denovo", "chimeric_interpretation"} and not search_results:
        return "needs_search_results", "run_batch_parameters_or_provide_existing_search_results"
    if blockers:
        return "blocked", "resolve_blockers"
    return "ready_for_handoff", "submit_or_run_batch_parameters"


def _write_builder_outputs(result: AgenticDatasetBuildResult, output_dir: Path) -> None:
    plan_path = output_dir / "agentic_dataset_build_plan.json"
    trace_path = output_dir / "agentic_dataset_build_trace.json"
    summary_path = output_dir / "agentic_dataset_build_summary.json"
    recommendations_path = output_dir / "agentic_dataset_build_recommendations.json"
    report_path = output_dir / "agentic_dataset_build_report.md"
    write_json(
        plan_path,
        {
            "intent": result.intent.model_dump(mode="json"),
            "discovery_plan": result.discovery_plan.model_dump(mode="json"),
            "planned_actions": ALLOWED_ACTIONS,
        },
    )
    write_json(trace_path, [step.model_dump(mode="json") for step in result.trace])
    write_json(summary_path, result.summary.model_dump(mode="json"))
    write_json(recommendations_path, _builder_recommendations(result))
    report_path.write_text(_markdown_report(result), encoding="utf-8")
    result.output_files.update(
        {
            "agentic_dataset_build_plan": str(plan_path),
            "agentic_dataset_build_trace": str(trace_path),
            "agentic_dataset_build_summary": str(summary_path),
            "agentic_dataset_build_recommendations": str(recommendations_path),
            "agentic_dataset_build_report": str(report_path),
        }
    )
    result.summary.files = dict(result.output_files)
    write_json(summary_path, result.summary.model_dump(mode="json"))


def _markdown_report(result: AgenticDatasetBuildResult) -> str:
    summary = result.summary
    lines = [
        "# Agentic Dataset Build Report",
        "",
        f"- Status: `{summary.status}`",
        f"- Run ID: `{summary.run_id}`",
        f"- Task type: `{summary.task_type}`",
        f"- Next step: `{summary.next_step}`",
        f"- Selected files: {summary.selected_files}",
        f"- Task candidate files: {summary.task_candidate_files}",
        f"- Handoff-ready files: {summary.handoff_ready_files}",
        f"- RT PSM rows: {summary.rt_rows_out}",
        f"- RT peptide rows: {summary.rt_peptide_rows_out}",
        f"- Fragment intensity rows: {summary.fragment_intensity_rows_out}",
        f"- PSM scoring rows: {summary.psm_scoring_rows_out}",
        f"- De novo rows: {summary.denovo_rows_out}",
        f"- PTM de novo rows: {summary.ptm_denovo_rows_out}",
        f"- Chimeric rows: {summary.chimeric_rows_out}",
        f"- Planned task: {summary.planned_task}",
        f"- Planned target schema: `{summary.planned_task_target_schema or ''}`",
        "",
        "## Recommendations",
        "",
    ]
    recommendations = _builder_recommendations(result)
    for item in recommendations["recommendations"]:
        lines.append(f"- `{item}`")
    lines.extend(
        [
            "",
        "## Warnings And Blockers",
        "",
        ]
    )
    if not summary.warnings and not summary.blockers:
        lines.append("- None")
    for warning in summary.warnings:
        lines.append(f"- Warning: `{warning}`")
    for blocker in summary.blockers:
        lines.append(f"- Blocker: `{blocker}`")
    lines.extend(["", "## Agent Trace", ""])
    for step in result.trace:
        lines.append(f"{step.step_index}. `{step.action}` -> `{step.status}`")
        if step.observation:
            lines.append(f"   Observation: {step.observation}")
    lines.extend(
        [
            "",
            "## Safety Boundary",
            "",
            "- The LLM generated discovery strategy and summaries only.",
            "- Deterministic modules handled validity, handoff, and AI-ready export decisions.",
            "- This builder did not download RAW files, run full workflow, or submit cluster jobs.",
        ]
    )
    return "\n".join(lines) + "\n"


def _builder_recommendations(result: AgenticDatasetBuildResult) -> dict[str, Any]:
    summary = result.summary
    recommendations: list[str] = []
    if summary.status == "completed":
        recommendations.append("ready_for_training_preview")
    if "needs_search_results" in summary.blockers or summary.status == "needs_search_results":
        recommendations.append("provide_search_results")
    if "needs_peaklist" in summary.blockers:
        recommendations.append("provide_peaklist")
    if "needs_target_decoy_labels" in summary.blockers:
        recommendations.append("run_psm_with_target_decoy")
    if "needs_modified_sequence_labels" in summary.blockers:
        recommendations.append("run_ptm_localization_export")
    if "no_multi_peptide_assignment" in summary.blockers:
        recommendations.append("provide_chimeric_search_or_multi_peptide_labels")
    if any("spectrum_not_matched" in warning for warning in summary.warnings):
        recommendations.append("review_spectrum_id_or_peaklist")
    if summary.planned_task:
        recommendations.append("run_batch_parameters_then_implement_task_exporter")
    if not recommendations:
        recommendations.append(summary.next_step)
    return {
        "status": summary.status,
        "task_type": summary.task_type,
        "next_step": summary.next_step,
        "recommendations": _dedupe(recommendations),
        "blockers": summary.blockers,
        "warnings": summary.warnings,
    }


def _planned_task_gap_report(
    *,
    task_profile,
    task_summary: dict[str, Any],
    handoff_summary: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    report = {
        "status": "planned_not_exported",
        "task_type": task_profile.task_type,
        "task_profile": task_profile.display_name,
        "target_schema": task_profile.ai_ready_target_schema,
        "required_labels": task_profile.required_labels,
        "required_metadata": task_profile.required_metadata,
        "missing_requirement_counts": task_summary.get("missing_requirement_counts", {}),
        "candidate_tier_counts": task_summary.get("candidate_tier_counts", {}),
        "handoff_ready_files": handoff_summary.get("ready_for_batch_parameters", 0),
        "next_pipeline_steps": task_profile.next_pipeline_steps,
        "quality_gate": task_profile.quality_gate,
        "gap_notes": _planned_task_gap_notes(task_profile.task_type),
        "safety_boundary": [
            "Planned tasks do not yet have task-specific parquet exporters in this milestone.",
            "This report only identifies candidate inputs and downstream label-generation gaps.",
            "The builder did not download RAW files, run full workflow, or submit cluster jobs.",
        ],
    }
    json_path = output_dir / "planned_task_gap_report.json"
    md_path = output_dir / "planned_task_gap_report.md"
    write_json(json_path, report)
    md_path.write_text(_planned_task_gap_markdown(report), encoding="utf-8")
    return report


def _planned_task_gap_notes(task_type: str) -> list[str]:
    if task_type == "denovo":
        return [
            "Needs high-confidence PSM or spectrum-sequence labels before denovo_train.parquet can be built.",
            "Future exporter should emit spectrum, precursor, charge, peptide sequence, and fragmentation context.",
        ]
    if task_type == "ptm_denovo":
        return [
            "Needs modified sequence labels plus PTM localization confidence before ptm_denovo_train.parquet can be built.",
            "Future exporter should preserve PTM type, modification site, localization score, and fragmentation context.",
        ]
    if task_type == "chimeric_interpretation":
        return [
            "Needs multi-peptide spectrum labels or component assignment before chimeric_train.parquet can be built.",
            "Future exporter should preserve isolation window, component peptides, and component intensity evidence.",
        ]
    return ["Planned task exporter is not implemented yet."]


def _planned_task_gap_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Planned Task Gap Report",
        "",
        f"- Status: `{report['status']}`",
        f"- Task type: `{report['task_type']}`",
        f"- Target schema: `{report['target_schema']}`",
        f"- Handoff-ready files: {report['handoff_ready_files']}",
        "",
        "## Required Labels",
        "",
    ]
    for label in report["required_labels"]:
        lines.append(f"- `{label}`")
    lines.extend(["", "## Missing Requirement Counts", ""])
    missing = report.get("missing_requirement_counts") or {}
    if not missing:
        lines.append("- None recorded")
    for key, value in sorted(missing.items()):
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Next Pipeline Steps", ""])
    for step in report["next_pipeline_steps"]:
        lines.append(f"- `{step}`")
    lines.extend(["", "## Gap Notes", ""])
    for note in report["gap_notes"]:
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def _compact_observation(payload: dict[str, Any]) -> str:
    keys = [
        "selected_projects",
        "selected_files",
        "validity_status_counts",
        "task_readiness",
        "candidate_files",
        "ready_for_batch_parameters",
        "next_step",
        "warnings",
    ]
    compact = {key: payload[key] for key in keys if key in payload}
    if not compact:
        compact = dict(list(payload.items())[:8])
    return json.dumps(compact, ensure_ascii=False)


def _read_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _string_paths(paths: dict[str, Path]) -> dict[str, str]:
    return {key: str(path) for key, path in paths.items()}


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result
