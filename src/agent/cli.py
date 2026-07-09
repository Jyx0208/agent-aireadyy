from __future__ import annotations

import threading
import time
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import typer

from agent.ai_ready.chimeric_exporter import export_chimeric_ai_ready
from agent.ai_ready.agentic_recovery import default_agentic_recovery_llm_client, run_agentic_recovery
from agent.ai_ready.agentic_recovery_batch import run_agentic_recovery_batch
from agent.ai_ready.denovo_exporter import export_denovo_ai_ready
from agent.ai_ready.data_scientist_report import make_data_scientist_agent_report
from agent.ai_ready.data_scientist_loop import run_data_scientist_agent_loop
from agent.ai_ready.curation_memory import apply_curation_decisions_to_memory
from agent.ai_ready.dataset_recipe import make_dataset_recipe
from agent.ai_ready.exporter import export_ai_ready_bundle
from agent.ai_ready.fragment_intensity_exporter import export_fragment_intensity_ai_ready
from agent.ai_ready.guidance_alignment import make_guidance_alignment_report
from agent.ai_ready.agent_run_bridge import build_ai_ready_from_agent_run
from agent.ai_ready.agent_run_locator import locate_agent_run_inputs
from agent.ai_ready.agent_run_peaklist import generate_agent_run_peaklist
from agent.ai_ready.input_locator import locate_ai_ready_inputs
from agent.ai_ready.input_profile import profile_ai_ready_inputs
from agent.ai_ready.mini_e2e import mini_e2e_parameters_only_placeholder, validate_agent_run_ai_ready_mini
from agent.ai_ready.mini_e2e_batch import validate_agent_runs_ai_ready_batch
from agent.ai_ready.model_loop import run_dataset_model_loop
from agent.ai_ready.model_strategy_comparison import compare_dataset_model_strategies
from agent.ai_ready.psm_scoring_exporter import export_psm_scoring_ai_ready
from agent.ai_ready.ptm_denovo_exporter import export_ptm_denovo_ai_ready
from agent.ai_ready.real_smoke import run_ai_ready_real_smoke
from agent.ai_ready.rt_exporter import export_rt_ai_ready
from agent.ai_ready.validation import validate_ai_ready_build as write_ai_ready_validation_report
from agent.agent_core.recovery_report import analyze_agent_recovery
from agent.agent_core.harness import run_agent_harness
from agent.assets.preparer import AssetPreparationError
from agent.discovery.agentic import (
    AgenticDiscoveryPlanner,
    default_agentic_discovery_planner,
)
from agent.discovery.agentic_runner import run_agentic_discovery
from agent.discovery.batch_bridge import (
    load_batch_manifest,
    load_batch_parameters_request,
    write_batch_result_report,
    write_batch_submission_report,
)
from agent.discovery.dataset_builder import run_agentic_dataset_builder
from agent.discovery.download_preflight import preflight_pride_download_candidates
from agent.discovery.evaluation import (
    build_validation_report,
    evaluate_data_value_selection,
    load_validation_reviews,
    validation_reviews_to_memory_decisions,
    write_review_sheet,
    write_validation_report,
)
from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.memory import (
    DiscoveryMemory,
    build_run_record,
    decisions_from_review_csv,
    generate_discovery_run_id,
    load_dataset_manifest,
)
from agent.discovery.models import DatasetRequest
from agent.discovery.ontology import general_query_terms_from_text, is_immunopeptidomics_goal, normalize_labeling_strategy, normalize_ptm_type, normalize_species_values
from agent.discovery.outcomes import write_discovery_batch_outcome_report
from agent.discovery.pipeline_handoff import load_pipeline_handoff, write_handoff_batch_preflight, write_pipeline_handoff
from agent.discovery.pride_discovery import discover_pride_dataset
from agent.discovery.repository_discovery import discover_repository_dataset
from agent.discovery.task_build_plan import write_task_build_plan
from agent.discovery.task_readiness import annotate_manifest_task_readiness, normalize_task_type
from agent.discovery.task_profiles import get_task_profile
from agent.execution.bundle import materialize_dda_task_bundle
from agent.input.normalizer import normalize_input, safe_output_stem
from agent.oneclick.preflight import normalize_run_mode, run_preflight
from agent.orchestrator.pipeline import AgentService, ReviewRequiredError
from agent.progress import render_download_progress
from agent.repositories.registry import RepositoryRegistry
from agent.repositories.iprox_adapter import refresh_public_iprox_index
from agent.repositories.smoke import run_repository_smoke
from agent.runtime.bootstrap import bootstrap_msdt_converter
from agent.runtime.toolchain import detect_toolchain
from agent.utils import write_json

app = typer.Typer(help="PRIDE-first AI-ready data agent aligned with MSDT-Converter.")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)

class ConsoleReporter:
    def __init__(self, log_path: Path | None = None) -> None:
        self._progress_open = False
        self._last_progress_len = 0
        self._last_progress_line: str | None = None
        self._activity_open = False
        self._activity_stop: threading.Event | None = None
        self._activity_thread: threading.Thread | None = None
        self._activity_len = 0
        self._log_path = log_path
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(self, message: str) -> None:
        if self._log_path is not None:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")

    def _clear_progress_line(self) -> None:
        if self._progress_open:
            typer.echo("", err=True)
            self._progress_open = False
            self._last_progress_len = 0
            self._last_progress_line = None

    def _start_activity(self, label: str) -> None:
        self._clear_progress_line()
        self._stop_activity(final_message=None)
        self._log(label)
        stop_event = threading.Event()
        self._activity_stop = stop_event
        self._activity_open = True

        def spin() -> None:
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            index = 0
            while not stop_event.is_set():
                line = f"{frames[index % len(frames)]} {label}"
                padding = " " * max(0, self._activity_len - len(line))
                typer.echo(f"\r{line}{padding}", err=True, nl=False)
                self._activity_len = len(line)
                index += 1
                stop_event.wait(0.12)

        self._activity_thread = threading.Thread(target=spin, daemon=True)
        self._activity_thread.start()

    def _stop_activity(self, final_message: str | None = None) -> None:
        if self._activity_stop is not None:
            self._activity_stop.set()
        if self._activity_thread is not None:
            self._activity_thread.join(timeout=0.5)
        if self._activity_open:
            clear_line = "\r" + (" " * self._activity_len) + "\r"
            typer.echo(clear_line, err=True, nl=False)
            if final_message:
                self._log(final_message)
                typer.echo(final_message, err=True)
            self._activity_open = False
            self._activity_len = 0
        self._activity_stop = None
        self._activity_thread = None

    def __call__(self, message) -> None:
        if isinstance(message, dict) and message.get("kind") == "download_progress":
            self._stop_activity(final_message=None)
            line = render_download_progress(message)
            is_duplicate = line == self._last_progress_line
            if is_duplicate and not message.get("complete"):
                return
            self._log(line)
            if len(line) < self._last_progress_len:
                line = line + (" " * (self._last_progress_len - len(line)))
            typer.echo(f"\r{line}", err=True, nl=False)
            self._progress_open = True
            self._last_progress_len = len(line)
            self._last_progress_line = line.rstrip()
            if message.get("complete"):
                typer.echo("", err=True)
                self._progress_open = False
                self._last_progress_len = 0
                self._last_progress_line = None
            return

        if isinstance(message, dict) and message.get("kind") == "activity_start":
            self._start_activity(str(message.get("label") or "处理中，请稍候…"))
            return

        if isinstance(message, dict) and message.get("kind") == "activity_stop":
            self._stop_activity(final_message=message.get("message"))
            return

        self._stop_activity(final_message=None)
        self._clear_progress_line()
        self._log(str(message))
        typer.echo(str(message), err=True)

def _build_reporter(output_dir: Path | None = None) -> ConsoleReporter:
    log_path = None
    if output_dir is not None:
        log_path = output_dir / "logs" / "runtime.log"
    return ConsoleReporter(log_path=log_path)


def _msdt_docker_command(output_dir: Path, image: str = "guomics2017/msdt-converter:v1.3") -> str:
    return (
        f'docker run --rm -v "{Path(output_dir).resolve()}:/workspace" '
        f"{image} -config /workspace/converter_config.json"
    )


def _dataset_request_from_cli(
    *,
    repository: str,
    goal: str,
    ptm: str,
    species: list[str],
    species_policy: str = "open",
    acquisition: str,
    max_projects: int,
    max_files: int,
    max_candidate_projects: int,
    max_files_per_project: int,
    labeling: str = "label_free",
    query_terms: list[str] | None = None,
) -> DatasetRequest:
    canonical_species, taxon_ids = normalize_species_values(species)
    ptm_type = normalize_ptm_type(ptm)
    normalized_goal = str(goal or "general").lower()
    if normalized_goal == "general":
        ptm_type = "unknown_ptm"
    elif is_immunopeptidomics_goal(normalized_goal) and ptm_type == "phospho":
        ptm_type = "unknown_ptm"
    return DatasetRequest(
        repository=repository.lower(),
        goal=normalized_goal,
        ptm_type=ptm_type,
        query_terms=list(query_terms or []),
        species=species,
        species_policy=species_policy,
        canonical_species=canonical_species,
        organism_taxon_id=taxon_ids,
        modification_scope=None if normalized_goal == "general" else ptm_type,
        labeling_strategy=normalize_labeling_strategy(labeling),
        acquisition_mode=acquisition.lower(),
        max_projects=max_projects,
        max_files=max_files,
        max_candidate_projects=max_candidate_projects,
        max_files_per_project=max_files_per_project,
    )


def _require_agentic_planner() -> AgenticDiscoveryPlanner:
    planner = default_agentic_discovery_planner()
    if planner is None:
        raise typer.BadParameter(
            "No discovery LLM API key found. Set DEEPSEEK_API_KEY or run without --agentic."
        )
    return planner


def _discover_dataset_for_cli(
    request: DatasetRequest,
    *,
    memory: DiscoveryMemory | None = None,
    queries: list[str] | None = None,
) -> Any:
    if request.repository == "pride":
        if queries is None:
            return discover_pride_dataset(request, memory=memory)
        return discover_pride_dataset(request, memory=memory, queries=queries)
    return discover_repository_dataset(request, memory=memory, queries=queries)


@app.command("check-runtime")
def check_runtime(
    fragpipe_root: Path | None = typer.Option(None, help="Optional local FragPipe root to report."),
    converter_root: Path | None = typer.Option(None, help="Optional local MSDT-Converter root to report."),
) -> None:
    report = detect_toolchain(fragpipe_root=fragpipe_root, msdt_converter_root=converter_root)
    typer.echo(report.model_dump_json(indent=2))


@app.command("analyze-agent-recovery")
def analyze_agent_recovery_command(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run/build directory containing recovery/task/log artifacts."),
    output_dir: Path | None = typer.Option(None, "--output-dir", help="Optional output directory. Defaults to --run-dir."),
) -> None:
    paths = analyze_agent_recovery(run_dir=run_dir, output_dir=output_dir)
    report = json.loads(paths["agent_recovery_report_json"].read_text(encoding="utf-8"))
    payload = {
        "status": "completed",
        "run_dir": str(run_dir),
        "output_dir": str(output_dir or run_dir),
        "recovery_status": report.get("status"),
        "primary_issue": report.get("primary_issue"),
        "recommended_next_step": report.get("recommended_next_step"),
        "files": {name: str(path) for name, path in paths.items()},
    }
    typer.echo(json_dumps(payload))


@app.command("agentic-recover-build")
def agentic_recover_build_command(
    mini_e2e_dir: Path = typer.Option(..., "--mini-e2e-dir", help="Mini E2E output directory containing mini_e2e_summary.json."),
    output_dir: Path | None = typer.Option(None, "--output-dir", help="Optional directory for agentic recovery outputs. Defaults to --mini-e2e-dir."),
    allow_safe_actions: bool = typer.Option(False, "--allow-safe-actions/--plan-only", help="Execute allowlisted low-risk recovery actions."),
    use_llm: bool = typer.Option(True, "--use-llm/--no-use-llm", help="Use an LLM planner when DEEPSEEK_API_KEY/AGENT_LLM_API_KEY is available."),
) -> None:
    llm_client = default_agentic_recovery_llm_client() if use_llm else None
    result = run_agentic_recovery(
        mini_e2e_dir=mini_e2e_dir,
        output_dir=output_dir,
        allow_safe_actions=allow_safe_actions,
        llm_client=llm_client,
    )
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "mode": result.mode,
                "mini_e2e_dir": result.mini_e2e_dir,
                "planned_actions": [item.model_dump(mode="json") for item in result.planned_actions],
                "executed_actions": result.executed_actions,
                "final_recommendation": result.final_recommendation,
                "files": result.files,
            }
        )
    )


@app.command("agentic-recover-batch")
def agentic_recover_batch_command(
    batch_dir: Path = typer.Option(..., "--batch-dir", help="Mini E2E batch output directory containing mini_e2e_batch_summary.json."),
    output_dir: Path | None = typer.Option(None, "--output-dir", help="Optional directory for batch recovery outputs. Defaults to --batch-dir/agentic_recovery."),
    allow_safe_actions: bool = typer.Option(False, "--allow-safe-actions/--plan-only", help="Execute allowlisted low-risk recovery actions for each run."),
    use_llm: bool = typer.Option(True, "--use-llm/--no-use-llm", help="Use an LLM planner when DEEPSEEK_API_KEY/AGENT_LLM_API_KEY is available."),
) -> None:
    llm_client = default_agentic_recovery_llm_client() if use_llm else None
    result = run_agentic_recovery_batch(
        batch_dir=batch_dir,
        output_dir=output_dir,
        allow_safe_actions=allow_safe_actions,
        llm_client=llm_client,
    )
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "mode": result.mode,
                "batch_dir": result.batch_dir,
                "run_count": len(result.run_results),
                "status_counts": result.status_counts,
                "primary_issue_counts": result.primary_issue_counts,
                "planned_action_counts": result.planned_action_counts,
                "executed_action_count": result.executed_action_count,
                "files": result.files,
            }
        )
    )


@app.command("run-agent-harness")
def run_agent_harness_command(
    case_file: Path = typer.Option(..., "--case-file", help="JSON case file for agent decision harness."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for harness outputs."),
    use_llm: bool = typer.Option(True, "--use-llm/--no-use-llm", help="Allow LLM planning when available; v1 has deterministic fallback."),
) -> None:
    try:
        result = run_agent_harness(case_file=case_file, output_dir=output_dir, use_llm=use_llm)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "total_cases": result.total_cases,
                "passed": result.passed,
                "failed": result.failed,
                "blocked": result.blocked,
                "files": result.files,
            }
        )
    )


@app.command("bootstrap-msdt-converter")
def bootstrap_msdt_converter_command(
    destination: Path = typer.Option(Path("external"), help="Directory where MSDT-Converter should be downloaded."),
) -> None:
    repo_root = bootstrap_msdt_converter(destination=destination)
    typer.echo(str(repo_root))


@app.command("resolve-project")
def resolve_project(input_value: str) -> None:
    service = AgentService(reporter=_build_reporter())
    resolution = service.resolve_project(input_value)
    typer.echo(resolution.model_dump_json(indent=2))


@app.command("resolve-dataset")
def resolve_dataset(
    input_value: str,
    repository: str = typer.Option("auto", "--repository", "-r", help="Repository: auto, pride, massive, or iprox."),
) -> None:
    registry = RepositoryRegistry()
    resolution = registry.resolve_project(repository, input_value)
    if resolution.primary_project:
        adapter = registry.get(resolution.primary_project.repository)
    else:
        adapter = registry.choose(repository, input_value)
    payload: dict[str, Any] = {
        "repository": adapter.name,
        "resolution": resolution.model_dump(mode="json"),
    }
    if resolution.primary_project:
        project = adapter.get_project(resolution.primary_project.project_accession)
        files = adapter.list_project_files(project)
        payload["project"] = project.model_dump(mode="json")
        payload["file_count"] = len(files)
        payload["files_preview"] = [file.model_dump(mode="json") for file in files[:20]]
    typer.echo(json_dumps(payload))


@app.command("repository-smoke")
def repository_smoke(
    repository: str = typer.Option(..., "--repository", "-r", help="Repository: pride, massive, iprox, or auto."),
    input_value: str = typer.Option(..., "--input", help="Known accession, file name, URL, or repository path."),
    mode: str = typer.Option("parameters", "--mode", help="Smoke target: parameters, prepare, or full. V1 does not execute downloads/full."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for smoke summary and adapter artifacts."),
) -> None:
    try:
        result = run_repository_smoke(
            repository=repository,
            input_value=input_value,
            mode=mode,  # type: ignore[arg-type]
            output_dir=output_dir,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "repository": result.repository,
                "requested_repository": result.requested_repository,
                "project_accession": result.project_accession,
                "native_accession": result.native_accession,
                "px_accession": result.px_accession,
                "matched_file": result.matched_file,
                "asset_type": result.asset_type,
                "download_url": result.download_url,
                "transfer_method": result.transfer_method,
                "expected_size_bytes": result.expected_size_bytes,
                "blockers": result.blockers,
                "warnings": result.warnings,
                "next_step": result.next_step,
                "files": result.files,
            }
        )
    )


@app.command("refresh-iprox-index")
def refresh_iprox_index(
    years: list[int] = typer.Option([], "--years", help="Public iProX release year; repeat for multiple years."),
    projects: list[str] = typer.Option([], "--project", help="Known public iProX project/subdataset accession; repeat for multiple projects."),
    output_dir: Path = typer.Option(Path("data") / "iprox_index", "--output-dir", help="Directory for JSONL iProX index cache."),
    max_projects: int | None = typer.Option(None, "--max-projects", min=1, help="Optional cap for smoke refresh."),
) -> None:
    summary = refresh_public_iprox_index(years=years, project_ids=projects, output_dir=output_dir, max_projects=max_projects)
    typer.echo(json_dumps(summary))


@app.command("discovery-plan")
def discovery_plan(
    prompt: str = typer.Option(..., "--prompt", help="Natural-language dataset discovery request."),
    goal: str = typer.Option("general", "--goal", help="Discovery target: general, ptm, or immunopeptidomics."),
    ptm: str = typer.Option("phospho", "--ptm", help="PTM type: phospho, acetyl, ubiquitin, glyco, methyl, or unknown_ptm."),
    species: list[str] = typer.Option([], "--species", help="Requested species; repeat for multiple species."),
    species_policy: str = typer.Option("open", "--species-policy", help="Species policy: open, include_only, or exclude."),
    acquisition: str = typer.Option("dda", "--acquisition", help="Acquisition mode. First release supports dda."),
    labeling: str = typer.Option("label_free", "--labeling", help="Labeling strategy: label_free, TMT, iTRAQ, or unknown."),
    max_projects: int = typer.Option(100, "--max-projects", min=1),
    max_files: int = typer.Option(2000, "--max-files", min=1),
    repository: str = typer.Option("pride", "--repository", help="Repository: pride, massive, iprox, or auto."),
    max_candidate_projects: int = typer.Option(300, "--max-candidate-projects", min=1),
    max_files_per_project: int = typer.Option(50, "--max-files-per-project", min=1),
    task_type: str | None = typer.Option(None, "--task-type", help="Optional modeling task profile."),
    query_term: list[str] = typer.Option([], "--query-term", help="Extra repository search term for general discovery; repeatable."),
    output_json: Path | None = typer.Option(None, "--output-json", help="Optional path for agentic plan JSON."),
) -> None:
    query_terms = list(query_term or [])
    if goal.lower() == "general":
        query_terms = [*query_terms, *general_query_terms_from_text(prompt)]
    request = _dataset_request_from_cli(
        repository=repository,
        goal=goal,
        ptm=ptm,
        species=species,
        species_policy=species_policy,
        acquisition=acquisition,
        labeling=labeling,
        max_projects=max_projects,
        max_files=max_files,
        max_candidate_projects=max_candidate_projects,
        max_files_per_project=max_files_per_project,
        query_terms=query_terms,
    )
    task_profile = None
    if task_type:
        try:
            task_profile = get_task_profile(task_type)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    planner = _require_agentic_planner()
    plan = planner.plan(prompt=prompt, request=request, task_profile=task_profile)
    payload = {"status": "completed", "plan": plan.model_dump(mode="json")}
    if output_json is not None:
        write_json(output_json, plan.model_dump(mode="json"))
        payload["output_json"] = str(output_json)
    typer.echo(json_dumps(payload))


@app.command("discover-dataset")
def discover_dataset(
    goal: str = typer.Option("general", "--goal", help="Discovery target: general, ptm, or immunopeptidomics."),
    ptm: str = typer.Option("phospho", "--ptm", help="PTM type: phospho, acetyl, ubiquitin, glyco, methyl, or unknown_ptm."),
    species: list[str] = typer.Option([], "--species", help="Requested species; repeat for multiple species."),
    species_policy: str = typer.Option("open", "--species-policy", help="Species policy: open, include_only, or exclude."),
    acquisition: str = typer.Option("dda", "--acquisition", help="Acquisition mode. First release supports dda."),
    labeling: str = typer.Option("label_free", "--labeling", help="Labeling strategy: label_free, TMT, iTRAQ, or unknown."),
    max_projects: int = typer.Option(100, "--max-projects", min=1, help="Maximum selected projects."),
    max_files: int = typer.Option(2000, "--max-files", min=1, help="Maximum selected files."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for discovery manifest files."),
    repository: str = typer.Option("pride", "--repository", help="Repository: pride, massive, iprox, or auto."),
    max_candidate_projects: int = typer.Option(300, "--max-candidate-projects", min=1),
    max_files_per_project: int = typer.Option(50, "--max-files-per-project", min=1),
    use_memory: bool = typer.Option(True, "--use-memory/--no-use-memory", help="Use review memory as a neutral prior when available."),
    save_memory: bool = typer.Option(False, "--save-memory", help="Append this discovery run to local memory JSONL."),
    memory_dir: Path = typer.Option(Path("runs") / "discovery_memory", "--memory-dir", help="Discovery memory directory."),
    agentic: bool = typer.Option(False, "--agentic/--no-agentic", help="Use LLM agentic query planning before deterministic discovery."),
    agentic_rounds: int = typer.Option(1, "--agentic-rounds", min=1, max=2, help="Agentic discovery rounds. Use 2 for one ReAct follow-up round."),
    prompt: str | None = typer.Option(None, "--prompt", help="Natural-language discovery request for --agentic."),
    query_term: list[str] = typer.Option([], "--query-term", help="Extra repository search term for general discovery; repeatable."),
    task_type: str | None = typer.Option(None, "--task-type", help="Optional modeling task: rt_prediction, fragment_intensity_prediction, psm_scoring, or denovo."),
) -> None:
    query_terms = list(query_term or [])
    if goal.lower() == "general":
        query_terms = [*query_terms, *general_query_terms_from_text(prompt or "")]
    request = _dataset_request_from_cli(
        repository=repository,
        goal=goal,
        ptm=ptm,
        species=species,
        species_policy=species_policy,
        acquisition=acquisition,
        labeling=labeling,
        max_projects=max_projects,
        max_files=max_files,
        max_candidate_projects=max_candidate_projects,
        max_files_per_project=max_files_per_project,
        query_terms=query_terms,
    )
    normalized_task_type = None
    task_profile = None
    if task_type:
        try:
            normalized_task_type = normalize_task_type(task_type)
            task_profile = get_task_profile(normalized_task_type)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    agentic_plan = None
    agentic_round_records = []
    if agentic:
        planner = _require_agentic_planner()
        plan_prompt = prompt or (
            f"Find {', '.join(species)} {ptm} {acquisition} {repository.upper()} projects/files for model-building datasets. "
            "Prefer trustworthy file-level evidence and useful diversity."
        )
    prior_memory = DiscoveryMemory(memory_dir) if use_memory else None
    if agentic:
        result = run_agentic_discovery(
            request=request,
            planner=planner,
            prompt=plan_prompt,
            memory=prior_memory,
            max_rounds=agentic_rounds,
            task_profile=task_profile,
            discovery_func=_discover_dataset_for_cli,
        )
        manifest = result.manifest
        agentic_plan = result.plan
        agentic_round_records = result.rounds
    else:
        manifest = _discover_dataset_for_cli(request, memory=prior_memory)
    if normalized_task_type is not None:
        manifest = annotate_manifest_task_readiness(manifest, normalized_task_type)
    run_id = generate_discovery_run_id(request)
    summary = {**manifest.summary, "run_id": run_id}
    if agentic_plan is not None:
        summary["agentic"] = {
            "enabled": True,
            "rounds": len(agentic_round_records),
            "queries": agentic_plan.queries,
            "warnings": agentic_plan.warnings,
            "suggested_next_queries": agentic_plan.suggested_next_queries,
            "trace_steps": len(agentic_plan.trace),
        }
    manifest = manifest.model_copy(update={"run_id": run_id, "summary": summary})
    paths = write_dataset_manifest(manifest, output_dir)
    agentic_plan_path = None
    if agentic_plan is not None:
        agentic_plan_path = output_dir / "agentic_plan.json"
        write_json(agentic_plan_path, agentic_plan.model_dump(mode="json"))
    agentic_rounds_path = None
    if agentic_round_records:
        agentic_rounds_path = output_dir / "agentic_rounds.json"
        write_json(agentic_rounds_path, [item.model_dump(mode="json") for item in agentic_round_records])
    memory_payload: dict[str, Any] | None = None
    if save_memory:
        memory = DiscoveryMemory(memory_dir)
        record = build_run_record(
            run_id=run_id,
            manifest=manifest,
            output_dir=output_dir,
            manifest_path=paths["dataset_manifest_json"],
        )
        memory.append_run(record)
        memory_payload = {
            "memory_dir": str(memory_dir),
            "discovery_runs": str(memory.discovery_runs_path),
        }
    typer.echo(
        json_dumps(
            {
                "status": "completed",
                "run_id": run_id,
                "request": request.model_dump(mode="json"),
                "summary": manifest.summary,
                "output_dir": str(output_dir),
                "memory_used": use_memory,
                "files": {name: str(path) for name, path in paths.items()},
                "agentic_plan": str(agentic_plan_path) if agentic_plan_path is not None else None,
                "agentic_rounds": str(agentic_rounds_path) if agentic_rounds_path is not None else None,
                "task_type": normalized_task_type,
                "memory": memory_payload,
            }
        )
    )


@app.command("preflight-pride-download-candidates")
def preflight_pride_download_candidates_cli(
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for preflight candidate files."),
    query: list[str] = typer.Option([], "--query", help="PRIDE project search keyword; repeat to add queries."),
    max_projects: int = typer.Option(12, "--max-projects", min=1, help="Maximum PRIDE projects to inspect."),
    max_files_per_project: int = typer.Option(80, "--max-files-per-project", min=1, help="Maximum files to inspect per project."),
    max_file_mb: int = typer.Option(500, "--max-file-mb", min=1, help="Maximum single acquisition file size for safe-download candidates."),
    exclude_project: list[str] = typer.Option(["PXD000900"], "--exclude-project", help="Project accession to exclude; repeat as needed."),
) -> None:
    result = preflight_pride_download_candidates(
        output_dir=output_dir,
        queries=query or None,
        max_projects=max_projects,
        max_files_per_project=max_files_per_project,
        max_file_mb=max_file_mb,
        exclude_projects=exclude_project,
    )
    typer.echo(json_dumps(result))


@app.command("agentic-build-dataset")
def agentic_build_dataset(
    prompt: str = typer.Option(..., "--prompt", help="Natural-language modeling/data-building goal."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for agentic build outputs."),
    task_type: str = typer.Option("rt_prediction", "--task-type", help="Modeling task type, e.g. rt_prediction, fragment_intensity_prediction, psm_scoring, denovo, ptm_denovo, chimeric_interpretation."),
    goal: str = typer.Option("ptm", "--goal", help="Modeling goal: ptm or immunopeptidomics."),
    ptm: str = typer.Option("phospho", "--ptm", help="PTM type: phospho, acetyl, ubiquitin, glyco, methyl, or unknown_ptm."),
    species: list[str] = typer.Option([], "--species", help="Requested species; repeat for multiple species."),
    species_policy: str = typer.Option("open", "--species-policy", help="Species policy: open, include_only, or exclude."),
    acquisition: str = typer.Option("dda", "--acquisition", help="Acquisition mode. First release supports dda."),
    labeling: str = typer.Option("label_free", "--labeling", help="Labeling strategy: label_free, TMT, iTRAQ, or unknown."),
    max_projects: int = typer.Option(100, "--max-projects", min=1, help="Maximum selected projects."),
    max_files: int = typer.Option(2000, "--max-files", min=1, help="Maximum selected files."),
    repository: str = typer.Option("pride", "--repository", help="Repository: pride, massive, iprox, or auto."),
    max_candidate_projects: int = typer.Option(300, "--max-candidate-projects", min=1),
    max_files_per_project: int = typer.Option(50, "--max-files-per-project", min=1),
    agentic_rounds: int = typer.Option(1, "--agentic-rounds", min=1, max=2, help="Agentic discovery rounds."),
    use_memory: bool = typer.Option(True, "--use-memory/--no-use-memory", help="Use discovery memory as neutral prior when available."),
    memory_dir: Path = typer.Option(Path("runs") / "discovery_memory", "--memory-dir", help="Discovery memory directory."),
    batch_manifest: Path | None = typer.Option(None, "--batch-manifest", help="Optional runs/_batches/<id>/batch_manifest.json to link outcomes."),
    search_result: list[Path] = typer.Option([], "--search-result", help="Optional search result TSV path; repeat for RT export."),
    peaklist: list[Path] = typer.Option([], "--peaklist", help="Optional MGF peaklist path; repeat for fragment intensity export."),
    agent_run_dir: Path | None = typer.Option(None, "--agent-run-dir", help="Optional original agent run directory to auto-locate FragPipe/MSDT/AI-ready outputs."),
    search_dir: Path | None = typer.Option(None, "--search-dir", help="Optional local FragPipe/Sage/MSFragger output directory to auto-locate TSV/PIN/MGF inputs."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession override for RT export rows."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source raw/mzML file override for RT export rows."),
    q_value_threshold: float = typer.Option(0.01, "--q-value-threshold", min=0.0),
    probability_threshold: float = typer.Option(0.9, "--probability-threshold", min=0.0, max=1.0),
    require_confidence: bool = typer.Option(False, "--require-confidence", help="Block RT export when confidence columns are absent."),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label for RT export rows."),
    max_input_file_mb: int = typer.Option(2048, "--max-input-file-mb", min=1, help="Maximum table/MGF input size to inspect from --agent-run-dir."),
    allow_large_input: bool = typer.Option(False, "--allow-large-input", help="Allow agent-run table/MGF inputs larger than --max-input-file-mb."),
) -> None:
    request = _dataset_request_from_cli(
        repository=repository,
        goal=goal,
        ptm=ptm,
        species=species,
        species_policy=species_policy,
        acquisition=acquisition,
        labeling=labeling,
        max_projects=max_projects,
        max_files=max_files,
        max_candidate_projects=max_candidate_projects,
        max_files_per_project=max_files_per_project,
    )
    planner = _require_agentic_planner()
    memory = DiscoveryMemory(memory_dir) if use_memory else None
    try:
        result = run_agentic_dataset_builder(
            prompt=prompt,
            request=request,
            output_dir=output_dir,
            planner=planner,
            task_type=task_type,
            memory=memory,
            agentic_rounds=agentic_rounds,
            search_results=search_result,
            peaklists=peaklist,
            agent_run_dir=agent_run_dir,
            search_dir=search_dir,
            batch_manifest=batch_manifest,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
            max_input_file_mb=max_input_file_mb,
            allow_large_input=allow_large_input,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.summary.status,
                "run_id": result.summary.run_id,
                "task_type": result.summary.task_type,
                "next_step": result.summary.next_step,
                "selected_files": result.summary.selected_files,
                "task_candidate_files": result.summary.task_candidate_files,
                "handoff_ready_files": result.summary.handoff_ready_files,
                "rt_rows_out": result.summary.rt_rows_out,
                "rt_peptide_rows_out": result.summary.rt_peptide_rows_out,
                "fragment_intensity_rows_out": result.summary.fragment_intensity_rows_out,
                "psm_scoring_rows_out": result.summary.psm_scoring_rows_out,
                "denovo_rows_out": result.summary.denovo_rows_out,
                "ptm_denovo_rows_out": result.summary.ptm_denovo_rows_out,
                "chimeric_rows_out": result.summary.chimeric_rows_out,
                "planned_task": result.summary.planned_task,
                "planned_task_target_schema": result.summary.planned_task_target_schema,
                "planned_task_missing_labels": result.summary.planned_task_missing_labels,
                "planned_task_next_steps": result.summary.planned_task_next_steps,
                "warnings": result.summary.warnings,
                "blockers": result.summary.blockers,
                "files": result.output_files,
            }
        )
    )


@app.command("build-ai-ready-from-agent-run")
def build_ai_ready_from_agent_run_command(
    agent_run_dir: Path = typer.Option(..., "--agent-run-dir", help="Existing original agent run directory."),
    task_type: list[str] = typer.Option(["rt_prediction"], "--task-type", help="Task type to build; repeat for multiple tasks."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for bridge reports and task exports."),
    peaklist: list[Path] = typer.Option([], "--peaklist", help="Optional generated/existing MGF peaklist path; repeat for multiple files."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession label for exported rows."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source file label for exported rows."),
    q_value_threshold: float = typer.Option(0.01, "--q-value-threshold", min=0.0),
    probability_threshold: float = typer.Option(0.9, "--probability-threshold", min=0.0, max=1.0),
    require_confidence: bool = typer.Option(False, "--require-confidence", help="Block RT export when confidence columns are absent."),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label for exported rows."),
    max_input_file_mb: int = typer.Option(2048, "--max-input-file-mb", min=1, help="Maximum table/MGF input size to inspect."),
    allow_large_input: bool = typer.Option(False, "--allow-large-input", help="Allow table/MGF inputs larger than --max-input-file-mb."),
) -> None:
    try:
        result = build_ai_ready_from_agent_run(
            agent_run_dir=agent_run_dir,
            task_types=task_type,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
            max_input_file_mb=max_input_file_mb,
            allow_large_input=allow_large_input,
            peaklists=peaklist,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "agent_run_dir": result.agent_run_dir,
                "locator_summary": result.locator_summary,
                "task_results": [item.model_dump(mode="json") for item in result.task_results],
                "files": {
                    "agent_run_build_summary_json": result.summary_path,
                    "agent_run_build_report_md": result.report_path,
                },
            }
        )
    )


@app.command("generate-agent-run-peaklist")
def generate_agent_run_peaklist_command(
    agent_run_dir: Path = typer.Option(..., "--agent-run-dir", help="Existing original agent run directory."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for peaklists/ and peaklist reports."),
    source: str = typer.Option("auto", "--source", help="Peaklist source: auto, existing, msdt, or rawspectrum."),
    max_output_mb: int = typer.Option(2048, "--max-output-mb", min=1, help="Maximum generated MGF size."),
) -> None:
    try:
        result = generate_agent_run_peaklist(
            agent_run_dir=agent_run_dir,
            output_dir=output_dir,
            source=source,  # type: ignore[arg-type]
            max_output_mb=max_output_mb,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "source": result.source,
                "peaklist_path": result.peaklist_path,
                "spectra_written": result.spectra_written,
                "blockers": result.blockers,
                "warnings": result.warnings,
                "files": {
                    "agent_run_peaklist_report_json": result.json_path,
                    "agent_run_peaklist_report_md": result.report_path,
                },
            }
        )
    )


@app.command("locate-agent-run-ai-ready-inputs")
def locate_agent_run_ai_ready_inputs_command(
    agent_run_dir: Path = typer.Option(..., "--agent-run-dir", help="Existing original agent run directory."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for agent_run_input_locations.json/csv."),
    max_input_file_mb: int = typer.Option(2048, "--max-input-file-mb", min=1, help="Maximum table/MGF input size to inspect."),
    allow_large_input: bool = typer.Option(False, "--allow-large-input", help="Allow table/MGF inputs larger than --max-input-file-mb."),
) -> None:
    try:
        result = locate_agent_run_inputs(
            agent_run_dir=agent_run_dir,
            output_dir=output_dir,
            max_input_file_mb=max_input_file_mb,
            allow_large_input=allow_large_input,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "summary": result.summary,
                "files": {
                    "agent_run_input_locations_json": result.json_path,
                    "agent_run_input_locations_csv": result.csv_path,
                },
            }
        )
    )


@app.command("validate-agent-run-ai-ready-mini")
def validate_agent_run_ai_ready_mini_command(
    agent_run_dir: Path | None = typer.Option(None, "--agent-run-dir", help="Existing original agent run directory."),
    input_value: str | None = typer.Option(None, "--input-value", help="Optional future mini run input. V1 records a safe placeholder and does not execute workflow."),
    mode: str = typer.Option("parameters", "--mode", help="Future mini run mode. V1 only records parameters/prepare placeholders unless --allow-full is used later."),
    task_type: list[str] = typer.Option(["rt_prediction", "denovo"], "--task-type", help="Task type to validate; repeat for multiple tasks."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for mini E2E summary/report."),
    peaklist: list[Path] = typer.Option([], "--peaklist", help="Optional generated/existing MGF peaklist path; repeat for multiple files."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession label for exported rows."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source file label for exported rows."),
    q_value_threshold: float = typer.Option(0.01, "--q-value-threshold", min=0.0),
    probability_threshold: float = typer.Option(0.9, "--probability-threshold", min=0.0, max=1.0),
    require_confidence: bool = typer.Option(False, "--require-confidence", help="Block RT export when confidence columns are absent."),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label for exported rows."),
    max_input_file_mb: int = typer.Option(2048, "--max-input-file-mb", min=1, help="Maximum table/MGF input size to inspect."),
    allow_large_input: bool = typer.Option(False, "--allow-large-input", help="Allow table/MGF inputs larger than --max-input-file-mb."),
    auto_recover: bool = typer.Option(True, "--auto-recover/--no-auto-recover", help="Run safe low-risk recovery actions such as generating MGF from existing parquet."),
    allow_full: bool = typer.Option(False, "--allow-full", help="Reserved safety switch. V1 still does not execute full workflow."),
) -> None:
    if agent_run_dir is None and not input_value:
        raise typer.BadParameter("Provide --agent-run-dir for v1, or --input-value to write a safe placeholder report.")
    if str(mode).lower() == "full" and not allow_full:
        raise typer.BadParameter("--mode full is blocked unless --allow-full is set. V1 does not execute full workflow.")
    try:
        if agent_run_dir is not None:
            result = validate_agent_run_ai_ready_mini(
                agent_run_dir=agent_run_dir,
                task_types=task_type,
                output_dir=output_dir,
                project_accession=project_accession,
                source_file=source_file,
                q_value_threshold=q_value_threshold,
                probability_threshold=probability_threshold,
                require_confidence=require_confidence,
                search_engine=search_engine,
                max_input_file_mb=max_input_file_mb,
                allow_large_input=allow_large_input,
                peaklists=peaklist,
                auto_recover=auto_recover,
            )
        else:
            result = mini_e2e_parameters_only_placeholder(
                input_value=str(input_value),
                output_dir=output_dir,
                mode=mode,
            )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "ai_ready_outcome": result.ai_ready_outcome,
                "usable_partial_outputs": result.usable_partial_outputs,
                "mode": result.mode,
                "agent_run_dir": result.agent_run_dir,
                "input_value": result.input_value,
                "generic_ai_ready_available": result.generic_ai_ready_available,
                "located_artifacts": result.located_artifacts,
                "task_results": [item.model_dump(mode="json") for item in result.task_results],
                "blockers": result.blockers,
                "warnings": result.warnings,
                "recovery_actions": [action.model_dump(mode="json") for action in result.recovery_actions],
                "recovery_status": result.recovery_status,
                "primary_issue": result.primary_issue,
                "recommended_next_step": result.recommended_next_step,
                "upstream_recovery_status": result.upstream_recovery_status,
                "upstream_workflow_outcome": result.upstream_workflow_outcome,
                "upstream_usable_partial_outputs": result.upstream_usable_partial_outputs,
                "upstream_primary_issue": result.upstream_primary_issue,
                "upstream_recommended_next_step": result.upstream_recommended_next_step,
                "files": {
                    "mini_e2e_summary_json": result.summary_path,
                    "mini_e2e_report_md": result.report_path,
                    "agent_recovery_report_json": result.recovery_report_json,
                    "agent_recovery_report_md": result.recovery_report_md,
                    "upstream_recovery_report_json": result.upstream_recovery_report_json,
                    "upstream_recovery_report_md": result.upstream_recovery_report_md,
                },
            }
        )
    )


@app.command("validate-agent-runs-ai-ready-batch")
def validate_agent_runs_ai_ready_batch_command(
    agent_run_dir: list[Path] = typer.Option(..., "--agent-run-dir", help="Existing original agent run directory; repeat for multiple runs."),
    task_type: list[str] = typer.Option(["rt_prediction", "denovo"], "--task-type", help="Task type to validate; repeat for multiple tasks."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for batch mini E2E summary/report."),
    peaklist: list[Path] = typer.Option([], "--peaklist", help="Optional generated/existing MGF peaklist path; repeat for multiple files."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession label for exported rows."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source file label for exported rows."),
    q_value_threshold: float = typer.Option(0.01, "--q-value-threshold", min=0.0),
    probability_threshold: float = typer.Option(0.9, "--probability-threshold", min=0.0, max=1.0),
    require_confidence: bool = typer.Option(False, "--require-confidence", help="Block RT export when confidence columns are absent."),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label for exported rows."),
    max_input_file_mb: int = typer.Option(2048, "--max-input-file-mb", min=1, help="Maximum table/MGF input size to inspect."),
    allow_large_input: bool = typer.Option(False, "--allow-large-input", help="Allow table/MGF inputs larger than --max-input-file-mb."),
    auto_recover: bool = typer.Option(True, "--auto-recover/--no-auto-recover", help="Run safe low-risk recovery actions such as generating MGF from existing parquet."),
) -> None:
    try:
        result = validate_agent_runs_ai_ready_batch(
            agent_run_dirs=agent_run_dir,
            task_types=task_type,
            output_dir=output_dir,
            peaklists=peaklist,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
            max_input_file_mb=max_input_file_mb,
            allow_large_input=allow_large_input,
            auto_recover=auto_recover,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "runs": len(result.run_results),
                "run_count": len(result.run_results),
                "status_counts": result.status_counts,
                "ai_ready_outcome_counts": result.ai_ready_outcome_counts,
                "task_status_counts": result.task_status_counts,
                "recovery_issue_counts": result.recovery_issue_counts,
                "upstream_recovery_issue_counts": result.upstream_recovery_issue_counts,
                "total_output_size_mb": result.total_output_size_mb,
                "files": {
                    "mini_e2e_batch_summary_json": result.summary_path,
                    "mini_e2e_batch_summary_csv": result.csv_path,
                    "mini_e2e_batch_report_md": result.report_path,
                    "benchmark_sample_manifest_json": result.benchmark_sample_manifest_json_path,
                    "benchmark_sample_manifest_csv": result.benchmark_sample_manifest_csv_path,
                    "benchmark_summary_json": result.benchmark_summary_json_path,
                    "benchmark_summary_csv": result.benchmark_summary_csv_path,
                    "benchmark_report_md": result.benchmark_report_path,
                    "benchmark_failure_taxonomy_json": result.benchmark_failure_taxonomy_path,
                },
            }
        )
    )


@app.command("make-dataset-recipe")
def make_dataset_recipe_command(
    batch_dir: Path = typer.Option(..., "--batch-dir", help="Mini E2E batch output directory."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for dataset recipe and split manifests."),
    discovery_manifest: Path | None = typer.Option(None, "--discovery-manifest", help="Optional discovery manifest CSV/JSON for evidence fields."),
    repository_audit: Path | None = typer.Option(None, "--repository-audit", help="Optional repository_audit.json from discovery or repository-smoke aggregation."),
    split_strategy: str = typer.Option(
        "auto",
        "--split-strategy",
        help=(
            "Split strategy: auto, project_disjoint, file_disjoint, sample_disjoint, lab_disjoint, "
            "instrument_disjoint, organism_disjoint, peptide_disjoint, protein_disjoint, "
            "modification_disjoint, acquisition_disjoint."
        ),
    ),
) -> None:
    try:
        result = make_dataset_recipe(
            batch_dir=batch_dir,
            output_dir=output_dir,
            discovery_manifest=discovery_manifest,
            repository_audit=repository_audit,
            split_strategy=split_strategy,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "selected_count": result.selected_count,
                "excluded_count": result.excluded_count,
                "split_level": result.split_level,
                "split_policy": result.split_policy,
                "split_strategy": result.split_strategy,
                "split_counts": result.split_counts,
                "leakage_status": result.leakage_status,
                "hard_benchmark_count": result.hard_benchmark_count,
                "curation_queue_count": result.curation_queue_count,
                "warnings": result.warnings,
                "files": result.files,
            }
        )
    )


@app.command("apply-curation-decisions")
def apply_curation_decisions_command(
    curation_queue: Path = typer.Option(..., "--curation-queue", help="curation_queue.json generated by make-dataset-recipe."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for curation memory update reports."),
    memory_dir: Path = typer.Option(Path("runs") / "discovery_memory", "--memory-dir", help="Discovery memory directory to update."),
    decisions_csv: Path | None = typer.Option(None, "--decisions-csv", help="Optional CSV with curation_id/project/file decision columns."),
    default_decision: str | None = typer.Option(None, "--default-decision", help="Optional keep/reject/needs_review decision for queue rows without explicit decisions."),
    run_id: str = typer.Option("active_curation", "--run-id", help="Run id to store in imported discovery memory decisions."),
) -> None:
    try:
        result = apply_curation_decisions_to_memory(
            curation_queue=curation_queue,
            output_dir=output_dir,
            memory_dir=memory_dir,
            decisions_csv=decisions_csv,
            default_decision=default_decision,
            run_id=run_id,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "curation_queue": result.curation_queue,
                "memory_dir": result.memory_dir,
                "imported_decision_count": result.imported_decision_count,
                "skipped_count": result.skipped_count,
                "memory_summary": result.memory_summary,
                "files": result.files,
            }
        )
    )


@app.command("run-dataset-model-loop")
def run_dataset_model_loop_command(
    recipe_dir: Path = typer.Option(..., "--recipe-dir", help="Dataset recipe output directory."),
    task_type: str = typer.Option(..., "--task-type", help="Task type to evaluate in the model-loop smoke."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for model-loop reports."),
    mode: str = typer.Option("smoke", "--mode", help="Only 'smoke' is supported in v1."),
    adapter: str = typer.Option("dry_run", "--adapter", help="Model adapter name/template, e.g. dry_run, xuanjinovo_template, massnet_eval, casanovo_eval. Default dry_run does not train."),
    adapter_command: str | None = typer.Option(None, "--adapter-command", help="Optional external adapter command. Must write external_model_metrics.json in output_dir."),
    metrics_file: Path | None = typer.Option(None, "--metrics-file", help="Optional precomputed model metrics JSON/CSV/TSV/log. Common XuanjiNovo, MassNet, and Casanovo-style metrics are normalized."),
) -> None:
    try:
        result = run_dataset_model_loop(
            recipe_dir=recipe_dir,
            task_type=task_type,
            output_dir=output_dir,
            mode=mode,  # type: ignore[arg-type]
            adapter=adapter,
            adapter_command=adapter_command,
            metrics_file=metrics_file,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "task_type": result.task_type,
                "adapter": result.adapter,
                "metric_status": result.metric_status,
                "failure_mode_count": result.failure_mode_count,
                "expansion_action_count": result.expansion_action_count,
                "blockers": result.blockers,
                "warnings": result.warnings,
                "files": result.files,
            }
        )
    )


@app.command("compare-dataset-model-strategies")
def compare_dataset_model_strategies_command(
    case_file: Path = typer.Option(..., "--case-file", help="JSON file listing strategy metrics/model-loop outputs."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for strategy comparison reports."),
    primary_metric: str | None = typer.Option(None, "--primary-metric", help="Metric to compare. Defaults to case file primary_metric."),
    higher_is_better: bool = typer.Option(True, "--higher-is-better/--lower-is-better", help="Whether higher metric values are better."),
    agent_strategy: str = typer.Option("agent_data_value", "--agent-strategy", help="Strategy name treated as the agent-selected dataset."),
) -> None:
    result = compare_dataset_model_strategies(
        case_file=case_file,
        output_dir=output_dir,
        primary_metric=primary_metric,
        higher_is_better=higher_is_better,
        agent_strategy=agent_strategy,
    )
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "primary_metric": result.primary_metric,
                "agent_strategy": result.agent_strategy,
                "best_baseline_strategy": result.best_baseline_strategy,
                "agent_minus_best_baseline": result.agent_minus_best_baseline,
                "interpretation": result.interpretation,
                "warnings": result.warnings,
                "files": result.files,
            }
        )
    )


@app.command("make-data-scientist-agent-report")
def make_data_scientist_agent_report_command(
    recipe_dir: Path = typer.Option(..., "--recipe-dir", help="Dataset recipe output directory."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for final data scientist agent report."),
    model_loop_dir: Path | None = typer.Option(None, "--model-loop-dir", help="Optional model-loop output directory."),
    benchmark_dir: Path | None = typer.Option(None, "--benchmark-dir", help="Optional benchmark output directory."),
    discovery_manifest: Path | None = typer.Option(None, "--discovery-manifest", help="Optional discovery manifest used for provenance."),
    guidance_alignment_dir: Path | None = typer.Option(None, "--guidance-alignment-dir", help="Optional guidance alignment output directory."),
) -> None:
    result = make_data_scientist_agent_report(
        recipe_dir=recipe_dir,
        output_dir=output_dir,
        model_loop_dir=model_loop_dir,
        benchmark_dir=benchmark_dir,
        discovery_manifest=discovery_manifest,
        guidance_alignment_dir=guidance_alignment_dir,
    )
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "selected_count": result.selected_count,
                "excluded_count": result.excluded_count,
                "leakage_status": result.leakage_status,
                "hard_benchmark_count": result.hard_benchmark_count,
                "curation_queue_count": result.curation_queue_count,
                "model_loop_status": result.model_loop_status,
                "guidance_alignment_status": result.guidance_alignment_status,
                "gap_action_count": result.gap_action_count,
                "warnings": result.warnings,
                "files": result.files,
            }
        )
    )


@app.command("make-guidance-alignment-report")
def make_guidance_alignment_report_command(
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for guidance alignment audit outputs."),
    recipe_dir: Path | None = typer.Option(None, "--recipe-dir", help="Optional dataset recipe output directory."),
    discovery_dir: Path | None = typer.Option(None, "--discovery-dir", help="Optional discovery output directory with readiness/value reports."),
    discovery_manifest: Path | None = typer.Option(None, "--discovery-manifest", help="Optional discovery manifest; parent dir is used if --discovery-dir is omitted."),
    model_loop_dir: Path | None = typer.Option(None, "--model-loop-dir", help="Optional model-loop output directory."),
    benchmark_dir: Path | None = typer.Option(None, "--benchmark-dir", help="Optional benchmark output directory."),
) -> None:
    result = make_guidance_alignment_report(
        output_dir=output_dir,
        recipe_dir=recipe_dir,
        discovery_dir=discovery_dir,
        discovery_manifest=discovery_manifest,
        model_loop_dir=model_loop_dir,
        benchmark_dir=benchmark_dir,
    )
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "achieved_count": result.achieved_count,
                "partial_count": result.partial_count,
                "missing_count": result.missing_count,
                "files": result.files,
            }
        )
    )


@app.command("run-data-scientist-agent-loop")
def run_data_scientist_agent_loop_command(
    batch_dir: Path = typer.Option(..., "--batch-dir", help="Mini E2E benchmark/batch output directory."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for composed data scientist agent loop outputs."),
    task_type: str = typer.Option("auto", "--task-type", help="Task type for model-loop smoke, or auto to use the first selected task."),
    discovery_manifest: Path | None = typer.Option(None, "--discovery-manifest", help="Optional discovery manifest for provenance/readiness/value context."),
    split_strategy: str = typer.Option("auto", "--split-strategy", help="Recipe split strategy."),
    mode: str = typer.Option("smoke", "--mode", help="Only 'smoke' is supported in v1."),
    adapter: str = typer.Option("dry_run", "--adapter", help="Model adapter name/template, e.g. dry_run, xuanjinovo_template, massnet_eval, casanovo_eval."),
    adapter_command: str | None = typer.Option(None, "--adapter-command", help="Optional external adapter command."),
    metrics_file: Path | None = typer.Option(None, "--metrics-file", help="Optional precomputed model metrics JSON/CSV/TSV/log."),
    strategy_comparison_case_file: Path | None = typer.Option(None, "--strategy-comparison-case-file", help="Optional model strategy comparison case JSON."),
    curation_decisions_csv: Path | None = typer.Option(None, "--curation-decisions-csv", help="Optional reviewed curation decisions CSV to write back to discovery memory."),
    curation_default_decision: str | None = typer.Option(None, "--curation-default-decision", help="Optional explicit default review decision for all curation items, e.g. needs_review."),
    curation_memory_dir: Path | None = typer.Option(None, "--curation-memory-dir", help="Optional discovery memory directory for curation write-back."),
    repository_smoke_dir: list[Path] = typer.Option([], "--repository-smoke-dir", help="Optional repository-smoke output directory; repeat to include MassIVE/iProX audit evidence."),
) -> None:
    result = run_data_scientist_agent_loop(
        batch_dir=batch_dir,
        output_dir=output_dir,
        task_type=task_type,
        discovery_manifest=discovery_manifest,
        split_strategy=split_strategy,
        mode=mode,  # type: ignore[arg-type]
        adapter=adapter,
        adapter_command=adapter_command,
        metrics_file=metrics_file,
        strategy_comparison_case_file=strategy_comparison_case_file,
        curation_decisions_csv=curation_decisions_csv,
        curation_default_decision=curation_default_decision,
        curation_memory_dir=curation_memory_dir,
        repository_smoke_dirs=repository_smoke_dir,
    )
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "task_type": result.task_type,
                "recipe_status": result.recipe_status,
                "model_loop_status": result.model_loop_status,
                "final_report_status": result.final_report_status,
                "guidance_alignment_status": result.guidance_alignment_status,
                "blockers": result.blockers,
                "warnings": result.warnings,
                "files": result.files,
            }
        )
    )


@app.command("locate-ai-ready-inputs")
def locate_ai_ready_inputs_command(
    search_dir: Path = typer.Option(..., "--search-dir", help="Local FragPipe/Sage/MSFragger output directory."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for ai_ready_input_locations.json/csv."),
) -> None:
    try:
        result = locate_ai_ready_inputs(search_dir=search_dir, output_dir=output_dir)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "summary": result.summary,
                "files": {
                    "ai_ready_input_locations_json": result.json_path,
                    "ai_ready_input_locations_csv": result.csv_path,
                },
            }
        )
    )


@app.command("run-ai-ready-real-smoke")
def run_ai_ready_real_smoke_command(
    search_dir: Path = typer.Option(..., "--search-dir", help="Local FragPipe/Sage/MSFragger output directory."),
    task_type: list[str] = typer.Option(["rt_prediction", "denovo", "ptm_denovo"], "--task-type", help="Task type to validate; repeat for multiple tasks."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for real smoke reports and task runs."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession label for exported rows."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source file label for exported rows."),
    q_value_threshold: float = typer.Option(0.01, "--q-value-threshold", min=0.0),
    probability_threshold: float = typer.Option(0.9, "--probability-threshold", min=0.0, max=1.0),
    require_confidence: bool = typer.Option(False, "--require-confidence", help="Block RT export when confidence columns are absent."),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label for exported rows."),
) -> None:
    try:
        result = run_ai_ready_real_smoke(
            search_dir=search_dir,
            task_types=task_type,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "locator_summary": result.locator_summary,
                "task_results": [item.model_dump(mode="json") for item in result.task_results],
                "files": {
                    "real_smoke_summary_json": result.summary_path,
                    "real_smoke_report_md": result.report_path,
                    "discovery_feedback_preview_json": result.discovery_feedback_preview_path,
                },
            }
        )
    )


@app.command("validate-ai-ready-build")
def validate_ai_ready_build_command(
    build_dir: Path = typer.Option(..., "--build-dir", help="Agentic build output directory."),
    task_type: str = typer.Option(..., "--task-type", help="Task type to validate."),
) -> None:
    try:
        paths = write_ai_ready_validation_report(build_dir, task_type)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report = json.loads(paths["ai_ready_validation_report_json"].read_text(encoding="utf-8"))
    typer.echo(
        json_dumps(
            {
                "status": report.get("status"),
                "task_type": report.get("task_type"),
                "implementation_status": report.get("implementation_status"),
                "summary": report.get("summary", {}),
                "files": {name: str(path) for name, path in paths.items()},
            }
        )
    )


@app.command("profile-ai-ready-inputs")
def profile_ai_ready_inputs_command(
    search_result: list[Path] = typer.Option(..., "--search-result", help="Search result TSV path; repeat for multiple files."),
    peaklist: list[Path] = typer.Option([], "--peaklist", help="Optional MGF peaklist path; repeat for multiple files."),
    task_type: list[str] = typer.Option(["rt_prediction"], "--task-type", help="Task type to profile; repeat for multiple tasks."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for ai_ready_input_profile.json/csv."),
) -> None:
    try:
        result = profile_ai_ready_inputs(
            search_results=search_result,
            peaklists=peaklist,
            task_types=task_type,
            output_dir=output_dir,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "rows_in": result.rows_in,
                "task_profiles": [item.model_dump(mode="json") for item in result.task_profiles],
                "warnings": result.warnings,
                "files": {
                    "ai_ready_input_profile_json": result.json_path,
                    "ai_ready_input_profile_csv": result.csv_path,
                },
            }
        )
    )


@app.command("review-discovery")
def review_discovery(
    manifest: Path = typer.Option(..., "--manifest", help="Path to dataset_manifest.json."),
    review_csv: Path = typer.Option(..., "--review-csv", help="CSV with project_accession,file_name,decision,reason,note."),
    memory_dir: Path = typer.Option(Path("runs") / "discovery_memory", "--memory-dir", help="Discovery memory directory."),
) -> None:
    try:
        dataset_manifest = load_dataset_manifest(manifest)
        decisions = decisions_from_review_csv(review_csv=review_csv, manifest=dataset_manifest)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    memory = DiscoveryMemory(memory_dir)
    memory.append_review_decisions(decisions)
    typer.echo(
        json_dumps(
            {
                "status": "completed",
                "run_id": dataset_manifest.run_id,
                "review_decisions": len(decisions),
                "memory_dir": str(memory_dir),
                "review_decisions_path": str(memory.review_decisions_path),
            }
        )
    )


@app.command("discovery-memory-summary")
def discovery_memory_summary(
    memory_dir: Path = typer.Option(Path("runs") / "discovery_memory", "--memory-dir", help="Discovery memory directory."),
) -> None:
    typer.echo(json_dumps(DiscoveryMemory(memory_dir).summary()))


@app.command("make-discovery-review-sheet")
def make_discovery_review_sheet(
    manifest: Path = typer.Option(..., "--manifest", help="Path to dataset_manifest.json."),
    output_csv: Path = typer.Option(..., "--output-csv", help="Review sheet CSV path."),
    max_files: int = typer.Option(50, "--max-files", min=1, help="Maximum files to include."),
    selection: str = typer.Option("usable", "--selection", help="Selection: usable, valid, all, or review."),
) -> None:
    try:
        dataset_manifest = load_dataset_manifest(manifest)
        output_path = write_review_sheet(
            dataset_manifest,
            output_csv,
            max_files=max_files,
            selection=selection.lower(),  # type: ignore[arg-type]
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": "completed",
                "run_id": dataset_manifest.run_id,
                "selection": selection.lower(),
                "max_files": max_files,
                "review_sheet": str(output_path),
            }
        )
    )


@app.command("eval-discovery")
def eval_discovery(
    manifest: Path = typer.Option(..., "--manifest", help="Path to dataset_manifest.json."),
    review_csv: Path = typer.Option(..., "--review-csv", help="Reviewed CSV from make-discovery-review-sheet."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for validation report files."),
    save_memory: bool = typer.Option(False, "--save-memory", help="Append validation reviews to local memory JSONL."),
    memory_dir: Path = typer.Option(Path("runs") / "discovery_memory", "--memory-dir", help="Discovery memory directory."),
) -> None:
    try:
        dataset_manifest = load_dataset_manifest(manifest)
        reviews = load_validation_reviews(review_csv=review_csv, manifest=dataset_manifest)
        paths = write_validation_report(manifest=dataset_manifest, reviews=reviews, output_dir=output_dir)
        report = build_validation_report(dataset_manifest, reviews)
        memory_payload: dict[str, Any] | None = None
        if save_memory:
            memory = DiscoveryMemory(memory_dir)
            decisions = validation_reviews_to_memory_decisions(manifest=dataset_manifest, reviews=reviews)
            memory.append_review_decisions(decisions)
            memory_payload = {
                "memory_dir": str(memory_dir),
                "review_decisions": str(memory.review_decisions_path),
                "saved_decisions": len(decisions),
            }
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": "completed",
                "run_id": dataset_manifest.run_id,
                "reviewed_files": report["reviewed_files"],
                "summary": report,
                "files": {name: str(path) for name, path in paths.items()},
                "memory": memory_payload,
            }
        )
    )


@app.command("eval-data-value-selection")
def eval_data_value_selection(
    manifest: Path = typer.Option(..., "--manifest", help="Path to dataset_manifest.json."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for data value selection evaluation files."),
    max_files: int | None = typer.Option(None, "--max-files", min=1, help="Maximum files selected by each strategy."),
    random_seed: int = typer.Option(17, "--random-seed", help="Deterministic random baseline seed."),
) -> None:
    dataset_manifest = load_dataset_manifest(manifest)
    paths = evaluate_data_value_selection(
        manifest=dataset_manifest,
        output_dir=output_dir,
        max_files=max_files,
        random_seed=random_seed,
    )
    summary = json.loads(paths["data_value_strategy_eval_json"].read_text(encoding="utf-8"))
    typer.echo(
        json_dumps(
            {
                "status": "completed",
                "run_id": dataset_manifest.run_id,
                "task_type": summary.get("task_type"),
                "interpretation": summary.get("interpretation"),
                "agent_minus_best_baseline": summary.get("agent_minus_best_baseline"),
                "files": {name: str(path) for name, path in paths.items()},
            }
        )
    )


@app.command("make-discovery-pipeline-handoff")
def make_discovery_pipeline_handoff(
    manifest: Path = typer.Option(..., "--manifest", help="Path to dataset_manifest.json."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for pipeline handoff files."),
    selection: str = typer.Option("auto", "--selection", help="Selection: auto, task_ready, usable, valid, review, or all."),
    max_files: int | None = typer.Option(None, "--max-files", min=1, help="Optional maximum files to include."),
) -> None:
    try:
        dataset_manifest = load_dataset_manifest(manifest)
        paths = write_pipeline_handoff(
            dataset_manifest,
            output_dir,
            selection=selection.lower(),  # type: ignore[arg-type]
            max_files=max_files,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    summary = json.loads(paths["pipeline_handoff_summary"].read_text(encoding="utf-8"))
    typer.echo(
        json_dumps(
            {
                "status": "completed",
                "run_id": dataset_manifest.run_id,
                "selection": selection.lower(),
                "summary": summary,
                "files": {name: str(path) for name, path in paths.items()},
            }
        )
    )


@app.command("make-discovery-task-build-plan")
def make_discovery_task_build_plan(
    manifest: Path = typer.Option(..., "--manifest", help="Path to dataset_manifest.json."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for task build plan files."),
    task_type: str = typer.Option(..., "--task-type", help="Modeling task type, e.g. rt_prediction, fragment_intensity_prediction, psm_scoring, denovo, ptm_denovo."),
    selection: str = typer.Option("auto", "--selection", help="Selection: auto, task_ready, usable, valid, review, or all."),
    max_files: int | None = typer.Option(None, "--max-files", min=1, help="Optional maximum files to include."),
) -> None:
    try:
        dataset_manifest = load_dataset_manifest(manifest)
        paths = write_task_build_plan(
            dataset_manifest,
            output_dir,
            task_type,
            selection=selection.lower(),  # type: ignore[arg-type]
            max_files=max_files,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    summary = json.loads(paths["task_build_summary"].read_text(encoding="utf-8"))
    typer.echo(
        json_dumps(
            {
                "status": "completed",
                "run_id": dataset_manifest.run_id,
                "task_type": summary["task_type"],
                "summary": summary,
                "files": {name: str(path) for name, path in paths.items()},
            }
        )
    )


@app.command("validate-discovery-pipeline-handoff")
def validate_discovery_pipeline_handoff(
    handoff: Path = typer.Option(..., "--handoff", help="Path to discovery_pipeline_handoff.json."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for batch preflight files."),
    submitter: str = typer.Option("discovery_handoff", "--submitter", help="Submitter label for generated batch request."),
    jobs: int = typer.Option(1, "--jobs", min=1, help="Requested batch parameter jobs."),
    repository: str = typer.Option("pride", "--repository", help="Repository for generated batch request."),
    resource_policy: str = typer.Option("balanced", "--resource-policy", help="Resource policy for preflight."),
    prefer_project_fasta: bool = typer.Option(False, "--prefer-project-fasta", help="Prefer project FASTA in generated batch request."),
) -> None:
    try:
        pipeline_handoff = load_pipeline_handoff(handoff)
        paths = write_handoff_batch_preflight(
            pipeline_handoff,
            output_dir,
            submitter=submitter,
            jobs=jobs,
            repository=repository,
            resource_policy=resource_policy,
            prefer_project_fasta=prefer_project_fasta,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report = json.loads(paths["batch_preflight_report"].read_text(encoding="utf-8"))
    typer.echo(
        json_dumps(
            {
                "status": report["status"],
                "run_id": pipeline_handoff.run_id,
                "summary": report["summary"],
                "files": {name: str(path) for name, path in paths.items()},
            }
        )
    )


@app.command("submit-discovery-batch-request")
def submit_discovery_batch_request(
    request_json: Path = typer.Option(..., "--request", help="Path to batch_parameters_request.json."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for submission report files."),
    web_url: str = typer.Option("http://127.0.0.1:8000", "--web-url", help="Running Web app base URL."),
    execute: bool = typer.Option(False, "--execute", help="Actually submit to the Web batch parameters API. Default is dry-run."),
) -> None:
    try:
        request_payload = load_batch_parameters_request(request_json)
        paths = write_batch_submission_report(
            request_payload,
            output_dir,
            request_path=request_json,
            execute=execute,
            web_url=web_url,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report = json.loads(paths["batch_submission_report"].read_text(encoding="utf-8"))
    typer.echo(
        json_dumps(
            {
                "status": report["status"],
                "execute": execute,
                "input_count": report["input_count"],
                "next_step": report["next_step"],
                "blocking_issues": report["blocking_issues"],
                "warnings": report["warnings"],
                "response": report["response"],
                "files": {name: str(path) for name, path in paths.items()},
            }
        )
    )


@app.command("summarize-discovery-batch")
def summarize_discovery_batch(
    batch_manifest: Path = typer.Option(..., "--batch-manifest", help="Path to runs/_batches/<id>/batch_manifest.json."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for batch result summary files."),
) -> None:
    try:
        manifest_payload = load_batch_manifest(batch_manifest)
        paths = write_batch_result_report(manifest_payload, output_dir)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report = json.loads(paths["batch_result_report"].read_text(encoding="utf-8"))
    typer.echo(
        json_dumps(
            {
                "status": report["status"],
                "batch_id": report["batch_id"],
                "item_count": report["item_count"],
                "completed_items": report["completed_items"],
                "failed_items": report["failed_items"],
                "needs_review_items": report["needs_review_items"],
                "success_rate": report["success_rate"],
                "files": {name: str(path) for name, path in paths.items()},
            }
        )
    )


@app.command("link-discovery-batch-results")
def link_discovery_batch_results(
    manifest: Path = typer.Option(..., "--manifest", help="Path to dataset_manifest.json."),
    batch_manifest: Path = typer.Option(..., "--batch-manifest", help="Path to runs/_batches/<id>/batch_manifest.json."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for linked discovery/batch outcome report."),
) -> None:
    try:
        dataset_manifest = load_dataset_manifest(manifest)
        batch_payload = load_batch_manifest(batch_manifest)
        paths = write_discovery_batch_outcome_report(dataset_manifest, batch_payload, output_dir)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    report = json.loads(paths["discovery_batch_outcome_report"].read_text(encoding="utf-8"))
    typer.echo(
        json_dumps(
            {
                "status": "completed",
                "run_id": report["run_id"],
                "batch_id": report["batch_id"],
                "manifest_file_count": report["manifest_file_count"],
                "submitted_files": report["submitted_files"],
                "completed_items": report["completed_items"],
                "failed_items": report["failed_items"],
                "needs_review_items": report["needs_review_items"],
                "submitted_success_rate": report["submitted_success_rate"],
                "unmatched_batch_items": report["unmatched_batch_items"],
                "files": {name: str(path) for name, path in paths.items()},
            }
        )
    )


@app.command("infer-attributes")
def infer_attributes(input_value: str) -> None:
    service = AgentService(reporter=_build_reporter())
    task = normalize_input(input_value)
    resolution = service.resolve_project(input_value)
    if not resolution.primary_project:
        raise typer.BadParameter("No PRIDE project could be resolved for the input.")
    context = service.build_context(resolution, task.file_name)
    attributes = service.infer_attributes(context)
    typer.echo(attributes.model_dump_json(indent=2))


@app.command("plan-dda-run")
def plan_dda_run(input_value: str, source_data_path: Path, output_dir: Path) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    resolution, context, plan = service.plan_dda_run(task, source_data_path, output_dir)
    attributes = service.infer_attributes(context)
    service.write_task_bundle(output_dir, resolution, context, attributes, plan)
    typer.echo(plan.model_dump_json(indent=2))


@app.command("plan-pride-dda-run")
def plan_pride_dda_run(input_value: str, output_dir: Path) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    result = service.plan_dda_run_from_pride(task=task, output_dir=output_dir)
    service.write_task_bundle(
        output_dir,
        result.resolution,
        result.context,
        result.attributes,
        result.plan,
        asset=result.asset,
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("download-pride-asset")
def download_pride_asset(input_value: str, output_dir: Path) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    result = service.plan_dda_run_from_pride(task=task, output_dir=output_dir)
    local_path = service.download_asset(result.asset)
    typer.echo(str(local_path))


@app.command("prepare-pride-asset")
def prepare_pride_asset(input_value: str, output_dir: Path) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    result = service.plan_dda_run_from_pride(task=task, output_dir=output_dir)
    prepared_path = service.prepare_asset(result.asset)
    typer.echo(str(prepared_path))


@app.command("run-pride-dda-msdt-docker")
def run_pride_dda_msdt_docker(
    input_value: str,
    output_dir: Path,
    image: str = typer.Option("guomics2017/msdt-converter:v1.3", help="Docker image for MSDT-Converter with bundled FragPipe."),
    reviewed_fasta_path: Path | None = typer.Option(None, help="Human-reviewed local FASTA path to use instead of inferred/default FASTA."),
    reviewed_fasta_url: str | None = typer.Option(None, help="Human-reviewed FASTA URL to download and use."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Automatically accept interactive review prompts."),
) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    manifest = service.run_pride_dda_msdt_docker(
        task=task,
        output_dir=output_dir,
        image=image,
        reviewed_fasta_path=reviewed_fasta_path,
        reviewed_fasta_url=reviewed_fasta_url,
        confirm_search_parameters=_build_search_parameter_confirmation(yes),
    )
    typer.echo(manifest.model_dump_json(indent=2))


@app.command("prepare-dda-bundle")
def prepare_dda_bundle(input_value: str, source_data_path: Path, output_dir: Path) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    resolution, context, _ = service.plan_dda_run(task, source_data_path, output_dir)
    attributes = service.infer_attributes(context)
    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=attributes,
        source_data_path=source_data_path,
        output_dir=output_dir,
    )
    service.write_task_bundle(output_dir, resolution, context, attributes, bundle.plan)
    typer.echo(bundle.model_dump_json(indent=2))


@app.command("prepare-msdt-docker-input")
def prepare_msdt_docker_input(input_value: str, source_data_path: Path, output_dir: Path) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    resolution, context, _ = service.plan_dda_run(task, source_data_path, output_dir)
    attributes = service.infer_attributes(context)
    bundle = materialize_dda_task_bundle(
        task=task,
        project_resolution=resolution,
        project_context=context,
        attributes=attributes,
        source_data_path=source_data_path,
        output_dir=output_dir,
    )
    service.write_task_bundle(output_dir, resolution, context, attributes, bundle.plan)
    typer.echo(f"请使用下面的命令运行 MSDT-Converter Docker：{_msdt_docker_command(output_dir)}")


@app.command("prepare-known-project-msdt-docker-input")
def prepare_known_project_msdt_docker_input(
    input_value: str,
    source_data_path: Path,
    output_dir: Path,
    project_accession: str = typer.Option(..., "--project-accession", help="Known PRIDE project accession for the local source file."),
    repository: str = typer.Option("pride", "--repository", "-r", help="Repository. V1 supports pride."),
    matched_file: str | None = typer.Option(None, "--matched-file", help="Repository/discovery file name to associate with the local source."),
    context_dir: Path | None = typer.Option(None, "--context-dir", help="Optional existing run dir containing metadata.json/attributes.json to reuse."),
    reviewed_fasta_path: Path | None = typer.Option(None, help="Human-reviewed local FASTA path to use instead of inferred/default FASTA."),
    reviewed_fasta_url: str | None = typer.Option(None, help="Human-reviewed FASTA URL to download and use."),
    image: str = typer.Option("guomics2017/msdt-converter:v1.3", help="Docker image for MSDT-Converter."),
    no_run: bool = typer.Option(True, "--no-run/--run", help="Only prepare input by default; pass --run to execute Docker."),
) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    try:
        bundle, _, _ = service.prepare_known_project_local_msdt_docker_input(
            task=task,
            source_data_path=source_data_path,
            project_accession=project_accession,
            output_dir=output_dir,
            repository=repository,
            matched_file=matched_file,
            reviewed_fasta_path=reviewed_fasta_path,
            reviewed_fasta_url=reviewed_fasta_url,
            context_dir=context_dir,
        )
    except (AssetPreparationError, ReviewRequiredError):
        typer.echo(_review_message(output_dir), err=True)
        raise typer.Exit(1)
    if no_run:
        typer.echo(f"Input package is ready. Run Docker manually: {_msdt_docker_command(output_dir, image)}")
        return
    from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner

    runner = DockerMSDTConverterRunner(image=image, report=service.reporter)
    docker_result = runner.run(bundle)
    if docker_result.returncode == 0:
        typer.echo("MSDT-Converter Docker completed.")
    else:
        typer.echo(f"MSDT-Converter Docker failed with return code: {docker_result.returncode}", err=True)
        typer.echo(docker_result.stderr, err=True)
        raise typer.Exit(1)


@app.command("run-known-project-dda-msdt-docker")
def run_known_project_dda_msdt_docker(
    input_value: str,
    source_data_path: Path,
    output_dir: Path,
    project_accession: str = typer.Option(..., "--project-accession", help="Known PRIDE project accession for the local source file."),
    repository: str = typer.Option("pride", "--repository", "-r", help="Repository. V1 supports pride."),
    matched_file: str | None = typer.Option(None, "--matched-file", help="Repository/discovery file name to associate with the local source."),
    context_dir: Path | None = typer.Option(None, "--context-dir", help="Optional existing run dir containing metadata.json/attributes.json to reuse."),
    reviewed_fasta_path: Path | None = typer.Option(None, help="Human-reviewed local FASTA path to use instead of inferred/default FASTA."),
    reviewed_fasta_url: str | None = typer.Option(None, help="Human-reviewed FASTA URL to download and use."),
    workflow_name: str | None = typer.Option(None, "--workflow-name", help="Optional reviewed FragPipe workflow override, e.g. TMT10.workflow."),
    image: str = typer.Option("guomics2017/msdt-converter:v1.3", help="Docker image for MSDT-Converter."),
) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    try:
        manifest = service.run_known_project_local_dda_msdt_docker(
            task=task,
            source_data_path=source_data_path,
            project_accession=project_accession,
            output_dir=output_dir,
            repository=repository,
            matched_file=matched_file,
            image=image,
            reviewed_fasta_path=reviewed_fasta_path,
            reviewed_fasta_url=reviewed_fasta_url,
            context_dir=context_dir,
            workflow_name=workflow_name,
        )
    except (AssetPreparationError, ReviewRequiredError):
        typer.echo(_review_message(output_dir), err=True)
        raise typer.Exit(1)
    typer.echo(manifest.model_dump_json(indent=2))


@app.command("prepare-repository-msdt-docker-input")
def prepare_repository_msdt_docker_input(
    input_value: str,
    output_dir: Path | None = typer.Argument(None, help="Output directory. Auto-generated from input file name if not specified."),
    repository: str = typer.Option("auto", "--repository", "-r", help="Repository: auto, pride, massive, or iprox."),
    reviewed_fasta_path: Path | None = typer.Option(None, help="Human-reviewed local FASTA path to use instead of inferred/default FASTA."),
    reviewed_fasta_url: str | None = typer.Option(None, help="Human-reviewed FASTA URL to download and use."),
    no_run: bool = typer.Option(False, "--no-run", help="Only prepare input, do not run Docker."),
    image: str = typer.Option("guomics2017/msdt-converter:v1.3", help="Docker image for MSDT-Converter."),
) -> None:
    if output_dir is None:
        output_dir = Path("runs") / safe_output_stem(input_value)
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    try:
        bundle, _, _ = service.prepare_repository_msdt_docker_input(
            task=task,
            output_dir=output_dir,
            repository=repository,
            reviewed_fasta_path=reviewed_fasta_path,
            reviewed_fasta_url=reviewed_fasta_url,
        )
    except (AssetPreparationError, ReviewRequiredError):
        typer.echo(_review_message(output_dir), err=True)
        raise typer.Exit(1)

    if no_run:
        typer.echo(f"Input package is ready. Run Docker manually: {_msdt_docker_command(output_dir, image)}")
        return

    typer.echo("Input package is ready; starting MSDT-Converter Docker...")
    from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner

    runner = DockerMSDTConverterRunner(image=image, report=service.reporter)
    docker_result = runner.run(bundle)
    if docker_result.returncode == 0:
        typer.echo("MSDT-Converter Docker completed.")
    else:
        typer.echo(f"MSDT-Converter Docker failed with return code: {docker_result.returncode}", err=True)
        typer.echo(docker_result.stderr, err=True)
        raise typer.Exit(1)


@app.command("prepare-pride-msdt-docker-input")
def prepare_pride_msdt_docker_input(
    input_value: str,
    output_dir: Path | None = typer.Argument(None, help="Output directory. Auto-generated from input file name if not specified."),
    reviewed_fasta_path: Path | None = typer.Option(None, help="Human-reviewed local FASTA path to use instead of inferred/default FASTA."),
    reviewed_fasta_url: str | None = typer.Option(None, help="Human-reviewed FASTA URL to download and use."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Automatically accept interactive review prompts."),
    no_run: bool = typer.Option(False, "--no-run", help="Only prepare input, do not run Docker."),
    image: str = typer.Option("guomics2017/msdt-converter:v1.3", help="Docker image for MSDT-Converter."),
) -> None:
    if output_dir is None:
        output_dir = Path("runs") / safe_output_stem(input_value)
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)

    def confirm_llm_fasta(recommendation: dict) -> bool:
        typer.echo("大模型推荐了可下载 FASTA，请确认是否使用：")
        typer.echo(f"  FASTA: {recommendation.get('name') or '未知'}")
        typer.echo(f"  URL: {recommendation.get('url')}")
        typer.echo(f"  来源: {recommendation.get('source') or '未知'}")
        if recommendation.get("database"):
            typer.echo(f"  数据库线索: {recommendation.get('database')}")
        if recommendation.get("workflow"):
            typer.echo(f"  workflow 建议: {recommendation.get('workflow')}")
        if yes:
            return True
        return typer.confirm("是否下载并使用这个 FASTA？", default=False)

    try:
        bundle, _, _ = service.prepare_pride_msdt_docker_input(
            task=task,
            output_dir=output_dir,
            reviewed_fasta_path=reviewed_fasta_path,
            reviewed_fasta_url=reviewed_fasta_url,
            confirm_llm_recommended_fasta=confirm_llm_fasta if reviewed_fasta_path is None and reviewed_fasta_url is None else None,
            confirm_search_parameters=_build_search_parameter_confirmation(yes),
        )
    except (AssetPreparationError, ReviewRequiredError):
        typer.echo(_review_message(output_dir), err=True)
        raise typer.Exit(1)

    if no_run:
        typer.echo(f"输入包已准备完成。手动运行 Docker：{_msdt_docker_command(output_dir, image)}")
        return

    typer.echo("输入包已准备完成，开始运行 MSDT-Converter Docker...")
    from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner
    runner = DockerMSDTConverterRunner(image=image, report=service.reporter)
    docker_result = runner.run(bundle)
    if docker_result.returncode == 0:
        typer.echo("MSDT-Converter Docker 运行完成。")
    else:
        typer.echo(f"MSDT-Converter Docker 运行失败，返回码：{docker_result.returncode}", err=True)
        typer.echo(docker_result.stderr, err=True)
        raise typer.Exit(1)


def _format_review_value(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(f"    - {key}: {val if val not in (None, '') else '未提供'}" for key, val in value.items())
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "无"
    return str(value if value not in (None, "") else "未提供")


def _build_search_parameter_confirmation(yes: bool):
    def confirm(result) -> bool:
        attrs = result.attributes
        hints = attrs.search_parameter_hints
        typer.echo("")
        typer.echo("需要人工复核搜库参数。当前推断如下：")
        typer.echo(f"  采集模式: {_format_review_value(attrs.acquisition_mode.value)}")
        typer.echo(f"  物种: {_format_review_value(attrs.species.value)}")
        typer.echo(f"  仪器: {_format_review_value(attrs.instrument_name.value)}")
        typer.echo(f"  酶切酶: {_format_review_value(attrs.enzyme.value)}")
        typer.echo(f"  固定修饰: {_format_review_value(attrs.fixed_mods.value)}")
        typer.echo(f"  可变修饰: {_format_review_value(attrs.variable_mods.value)}")
        typer.echo("  搜库参数:")
        typer.echo(_format_review_value(hints.value))
        typer.echo("  复核原因:")
        for issue in result.plan.blocking_issues:
            if "搜库参数需要人工复核" in issue:
                typer.echo(f"    - {issue}")
        if yes:
            typer.echo("已通过 --yes 自动确认搜库参数。")
            return True
        return typer.confirm("是否确认上述搜库参数并继续？", default=False)

    return confirm


def _review_message(output_dir: Path) -> str:
    return (
        "当前输入包需要人工复核，暂不能运行 MSDT-Converter Docker。"
        f"请查看 {output_dir / 'review_queue.json'} 和 {output_dir / 'task_state.json'}。"
    )


@app.command("one-click-run")
def one_click_run(
    input_value: str,
    output_dir: Path | None = typer.Argument(None, help="Output directory. Auto-generated from input file name if not specified."),
    repository: str = typer.Option("auto", "--repository", "-r", help="Repository: auto, pride, massive, or iprox."),
    mode: str = typer.Option("full", "--mode", "-m", help="Run mode: parameters, prepare, or full."),
    resource_policy: str = typer.Option("balanced", "--resource-policy", help="Preflight disk policy: fast, balanced, or conservative."),
    reviewed_fasta_path: Path | None = typer.Option(None, help="Human-reviewed local FASTA path to use instead of inferred/default FASTA."),
    reviewed_fasta_url: str | None = typer.Option(None, help="Human-reviewed FASTA URL to download and use."),
    image: str = typer.Option("guomics2017/msdt-converter:v1.3", help="Docker image for MSDT-Converter."),
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip local Docker and disk preflight checks."),
) -> None:
    run_mode = normalize_run_mode(mode)
    if output_dir is None:
        output_dir = Path("runs") / safe_output_stem(input_value)

    if not skip_preflight:
        preflight = run_preflight(
            inputs=[input_value],
            run_mode=run_mode,
            repository=repository,
            output_root=output_dir.parent,
            resource_policy=resource_policy,
        )
        typer.echo(json_dumps({"preflight": preflight}))
        if preflight.get("status") == "blocked":
            raise typer.Exit(1)

    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    try:
        if run_mode == "parameters":
            result = service.plan_dda_run_from_repository(
                task=task,
                output_dir=output_dir,
                repository=repository,
            )
            service.write_task_bundle(output_dir, result.resolution, result.context, result.attributes, result.plan, asset=result.asset)
            if result.plan.needs_review:
                typer.echo(_review_message(output_dir), err=True)
                raise typer.Exit(1)
            typer.echo(
                json_dumps(
                    {
                        "status": "completed",
                        "mode": run_mode,
                        "output_dir": str(output_dir),
                        "workflow": str(result.plan.fragpipe_workflow_path),
                        "fasta": str(result.plan.fasta_path),
                    }
                )
            )
            return

        bundle, _, _ = service.prepare_repository_msdt_docker_input(
            task=task,
            output_dir=output_dir,
            repository=repository,
            reviewed_fasta_path=reviewed_fasta_path,
            reviewed_fasta_url=reviewed_fasta_url,
        )
    except (AssetPreparationError, ReviewRequiredError):
        typer.echo(_review_message(output_dir), err=True)
        raise typer.Exit(1)

    if run_mode == "prepare":
        typer.echo(
            json_dumps(
                {
                    "status": "completed",
                    "mode": run_mode,
                    "output_dir": str(output_dir),
                    "converter_config": str(bundle.converter_config_path),
                    "workflow": str(bundle.materialized_workflow_path),
                    "fasta": str(bundle.materialized_fasta_path),
                    "docker_command": _msdt_docker_command(output_dir, image),
                }
            )
        )
        return

    from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner

    runner = DockerMSDTConverterRunner(image=image, report=service.reporter)
    docker_result = runner.run(bundle)
    if docker_result.returncode != 0:
        typer.echo(f"MSDT-Converter Docker failed with return code: {docker_result.returncode}", err=True)
        typer.echo(docker_result.stderr, err=True)
        raise typer.Exit(1)
    typer.echo(json_dumps({"status": "completed", "mode": run_mode, "output_dir": str(output_dir)}))


@app.command("run-dda-msdt")
def run_dda_msdt(
    input_value: str,
    source_data_path: Path,
    output_dir: Path,
    converter_root: Path = typer.Option(..., help="Path to the MSDT-Converter repository. It must contain convert.py and bundled FragPipe assets."),
) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    manifest = service.run_dda_msdt(
        task=task,
        source_data_path=source_data_path,
        output_dir=output_dir,
        converter_root=converter_root,
    )
    typer.echo(manifest.model_dump_json(indent=2))


@app.command("export-ai-ready")
def export_ai_ready(
    msdt_path: Path,
    output_dir: Path,
    project_accession: str,
    source_file: str,
    attribute_evidence_path: Path,
    decision_trace_path: Path,
    run_manifest_path: Path,
) -> None:
    import json

    result = export_ai_ready_bundle(
        msdt_path=msdt_path,
        output_dir=output_dir,
        project_accession=project_accession,
        source_file=source_file,
        attribute_evidence=json.loads(attribute_evidence_path.read_text(encoding="utf-8")),
        decision_trace=json.loads(decision_trace_path.read_text(encoding="utf-8")),
        run_manifest=json.loads(run_manifest_path.read_text(encoding="utf-8")),
    )
    write_json(output_dir / "ai_ready_export.json", {"output_path": str(result)})
    typer.echo(str(result))


@app.command("export-denovo-ai-ready")
def export_denovo_ai_ready_command(
    search_result: list[Path] = typer.Option(..., "--search-result", help="Search result TSV path; repeat for multiple files."),
    peaklist: list[Path] = typer.Option(..., "--peaklist", help="MGF peaklist path; repeat for multiple files."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for denovo_train.parquet and reports."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession to assign to all exported rows."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source raw/mzML file name to assign to all rows."),
    task_build_plan: Path | None = typer.Option(None, "--task-build-plan", help="Optional discovery_task_build_plan.json for metadata enrichment."),
    q_value_threshold: float = typer.Option(0.01, "--q-value-threshold", min=0.0, help="Keep rows with q-value <= threshold when q-value column exists."),
    probability_threshold: float = typer.Option(0.9, "--probability-threshold", min=0.0, max=1.0, help="Keep rows with probability >= threshold when probability column exists."),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label to assign to all rows."),
) -> None:
    try:
        result = export_denovo_ai_ready(
            search_results=search_result,
            peaklists=peaklist,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            task_build_plan=task_build_plan,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            search_engine=search_engine,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "schema_version": result.schema_version,
                "rows_in": result.rows_in,
                "rows_out": result.rows_out,
                "filter_counts": result.filter_counts,
                "warnings": result.warnings,
                "files": {
                    "denovo_train_parquet": result.output_parquet,
                    "preview_csv": result.preview_csv,
                    "report_json": result.report_json,
                    "schema_json": result.schema_json_path,
                },
            }
        )
    )


@app.command("export-ptm-denovo-ai-ready")
def export_ptm_denovo_ai_ready_command(
    search_result: list[Path] = typer.Option(..., "--search-result", help="PTM search result TSV path; repeat for multiple files."),
    peaklist: list[Path] = typer.Option(..., "--peaklist", help="MGF peaklist path; repeat for multiple files."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for ptm_denovo_train.parquet and reports."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession to assign to all exported rows."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source raw/mzML file name to assign to all rows."),
    task_build_plan: Path | None = typer.Option(None, "--task-build-plan", help="Optional discovery_task_build_plan.json for metadata enrichment."),
    q_value_threshold: float = typer.Option(0.01, "--q-value-threshold", min=0.0, help="Keep rows with q-value <= threshold when q-value column exists."),
    probability_threshold: float = typer.Option(0.9, "--probability-threshold", min=0.0, max=1.0, help="Keep rows with probability >= threshold when probability column exists."),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label to assign to all rows."),
) -> None:
    try:
        result = export_ptm_denovo_ai_ready(
            search_results=search_result,
            peaklists=peaklist,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            task_build_plan=task_build_plan,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            search_engine=search_engine,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "schema_version": result.schema_version,
                "rows_in": result.rows_in,
                "rows_out": result.rows_out,
                "filter_counts": result.filter_counts,
                "warnings": result.warnings,
                "files": {
                    "ptm_denovo_train_parquet": result.output_parquet,
                    "preview_csv": result.preview_csv,
                    "report_json": result.report_json,
                    "schema_json": result.schema_json_path,
                },
            }
        )
    )


@app.command("export-chimeric-ai-ready")
def export_chimeric_ai_ready_command(
    search_result: list[Path] = typer.Option(..., "--search-result", help="PSM/search result TSV path; repeat for multiple files."),
    peaklist: list[Path] = typer.Option(..., "--peaklist", help="MGF peaklist path; repeat for multiple files."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for chimeric_train.parquet and reports."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession label."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source file label."),
    task_build_plan: Path | None = typer.Option(None, "--task-build-plan", help="Optional discovery_task_build_plan.json for metadata."),
    q_value_threshold: float = typer.Option(0.01, "--q-value-threshold", min=0.0),
    probability_threshold: float = typer.Option(0.9, "--probability-threshold", min=0.0, max=1.0),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label."),
) -> None:
    try:
        result = export_chimeric_ai_ready(
            search_results=search_result,
            peaklists=peaklist,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            task_build_plan=task_build_plan,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            search_engine=search_engine,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "rows_in": result.rows_in,
                "rows_out": result.rows_out,
                "filter_counts": result.filter_counts,
                "warnings": result.warnings,
                "files": {
                    "chimeric_train_parquet": result.output_parquet,
                    "preview_csv": result.preview_csv,
                    "chimeric_export_report": result.report_json,
                    "schema_json": result.schema_json_path,
                },
            }
        )
    )


@app.command("export-fragment-intensity-ai-ready")
def export_fragment_intensity_ai_ready_command(
    search_result: list[Path] = typer.Option(..., "--search-result", help="Search result TSV path; repeat for multiple files."),
    peaklist: list[Path] = typer.Option(..., "--peaklist", help="MGF peaklist path; repeat for multiple files."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for fragment_intensity_train.parquet and reports."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession to assign to all exported rows."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source raw/mzML file name to assign to all rows."),
    task_build_plan: Path | None = typer.Option(None, "--task-build-plan", help="Optional discovery_task_build_plan.json for metadata enrichment."),
    q_value_threshold: float = typer.Option(0.01, "--q-value-threshold", min=0.0, help="Keep rows with q-value <= threshold when q-value column exists."),
    probability_threshold: float = typer.Option(0.9, "--probability-threshold", min=0.0, max=1.0, help="Keep rows with probability >= threshold when probability column exists."),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label to assign to all rows."),
) -> None:
    try:
        result = export_fragment_intensity_ai_ready(
            search_results=search_result,
            peaklists=peaklist,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            task_build_plan=task_build_plan,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            search_engine=search_engine,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "schema_version": result.schema_version,
                "rows_in": result.rows_in,
                "rows_out": result.rows_out,
                "filter_counts": result.filter_counts,
                "warnings": result.warnings,
                "files": {
                    "fragment_intensity_train_parquet": result.output_parquet,
                    "preview_csv": result.preview_csv,
                    "report_json": result.report_json,
                    "schema_json": result.schema_json_path,
                },
            }
        )
    )


@app.command("export-psm-scoring-ai-ready")
def export_psm_scoring_ai_ready_command(
    search_result: list[Path] = typer.Option(..., "--search-result", help="Search result TSV/PIN path; repeat for multiple files."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for psm_scoring_train.parquet and reports."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession to assign to all exported rows."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source raw/mzML file name to assign to all rows."),
    task_build_plan: Path | None = typer.Option(None, "--task-build-plan", help="Optional discovery_task_build_plan.json for metadata enrichment."),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label to assign to all rows."),
) -> None:
    try:
        result = export_psm_scoring_ai_ready(
            search_results=search_result,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            task_build_plan=task_build_plan,
            search_engine=search_engine,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "schema_version": result.schema_version,
                "rows_in": result.rows_in,
                "rows_out": result.rows_out,
                "target_count": result.target_count,
                "decoy_count": result.decoy_count,
                "filter_counts": result.filter_counts,
                "warnings": result.warnings,
                "files": {
                    "psm_scoring_train_parquet": result.output_parquet,
                    "preview_csv": result.preview_csv,
                    "report_json": result.report_json,
                    "schema_json": result.schema_json_path,
                },
            }
        )
    )


@app.command("export-rt-ai-ready")
def export_rt_ai_ready_command(
    search_result: list[Path] = typer.Option(..., "--search-result", help="Search result TSV path; repeat for multiple files."),
    output_dir: Path = typer.Option(..., "--output-dir", help="Directory for rt_train.parquet and reports."),
    project_accession: str | None = typer.Option(None, "--project-accession", help="Optional project accession to assign to all exported rows."),
    source_file: str | None = typer.Option(None, "--source-file", help="Optional source raw/mzML file name to assign to all rows."),
    task_build_plan: Path | None = typer.Option(None, "--task-build-plan", help="Optional discovery_task_build_plan.json for metadata enrichment."),
    q_value_threshold: float = typer.Option(0.01, "--q-value-threshold", min=0.0, help="Keep rows with q-value <= threshold when q-value column exists."),
    probability_threshold: float = typer.Option(0.9, "--probability-threshold", min=0.0, max=1.0, help="Keep rows with probability >= threshold when probability column exists."),
    require_confidence: bool = typer.Option(False, "--require-confidence", help="Block export when no q-value/probability column is present."),
    search_engine: str | None = typer.Option(None, "--search-engine", help="Optional search engine label to assign to all rows."),
) -> None:
    try:
        result = export_rt_ai_ready(
            search_results=search_result,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            task_build_plan=task_build_plan,
            q_value_threshold=q_value_threshold,
            probability_threshold=probability_threshold,
            require_confidence=require_confidence,
            search_engine=search_engine,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json_dumps(
            {
                "status": result.status,
                "schema_version": result.schema_version,
                "rows_in": result.rows_in,
                "rows_out": result.rows_out,
                "peptide_rows_out": result.peptide_rows_out,
                "filter_counts": result.filter_counts,
                "warnings": result.warnings,
                "files": {
                    "rt_train_parquet": result.output_parquet,
                    "preview_csv": result.preview_csv,
                    "rt_train_peptide_parquet": result.peptide_parquet,
                    "peptide_preview_csv": result.peptide_preview_csv,
                    "peptide_report_json": result.peptide_report_json,
                    "report_json": result.report_json,
                    "validation_report_json": result.validation_report_json,
                    "schema_json": result.schema_json_path,
                },
            }
        )
    )


if __name__ == "__main__":
    app()
