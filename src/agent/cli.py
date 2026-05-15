from __future__ import annotations

import threading
import time
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

import typer

from agent.ai_ready.exporter import export_ai_ready_bundle
from agent.assets.preparer import AssetPreparationError
from agent.execution.bundle import materialize_dda_task_bundle
from agent.input.normalizer import normalize_input, safe_output_stem
from agent.oneclick.preflight import normalize_run_mode, run_preflight
from agent.orchestrator.pipeline import AgentService, ReviewRequiredError
from agent.progress import render_download_progress
from agent.repositories.registry import RepositoryRegistry
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


@app.command("check-runtime")
def check_runtime(
    fragpipe_root: Path | None = typer.Option(None, help="Optional local FragPipe root to report."),
    converter_root: Path | None = typer.Option(None, help="Optional local MSDT-Converter root to report."),
) -> None:
    report = detect_toolchain(fragpipe_root=fragpipe_root, msdt_converter_root=converter_root)
    typer.echo(report.model_dump_json(indent=2))


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
    adapter = registry.choose(repository, input_value)
    resolution = adapter.resolve_project(input_value)
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


@app.command("sync-repository-index")
def sync_repository_index(
    repository: str = typer.Option(..., "--repository", "-r", help="Repository to index. Currently supports iprox."),
    year: str | None = typer.Option(None, "--year", help="Sync iProX project IDs published in a year, for example 2025."),
    month: str | None = typer.Option(None, "--month", help="Sync iProX project IDs published in a month, for example 2025-05."),
    day: str | None = typer.Option(None, "--day", help="Sync iProX project IDs published on a day, for example 2025-05-09."),
    xml_dir: Path | None = typer.Option(None, "--xml-dir", help="Import local ProteomeXchange XML files into the repository index."),
    limit: int | None = typer.Option(None, "--limit", help="Limit the number of remote projects to index."),
) -> None:
    registry = RepositoryRegistry()
    adapter = registry.get(repository)
    if repository != "iprox":
        raise typer.BadParameter("sync-repository-index currently supports iProX only.")

    reporter = _build_reporter()
    if xml_dir is not None:
        xml_paths = sorted(path for path in xml_dir.glob("*.xml") if path.is_file())
        if not xml_paths:
            raise typer.BadParameter(f"No XML files found in {xml_dir}.")
        sync_from_xml = getattr(adapter, "sync_index_from_xml_files", None)
        if sync_from_xml is None:
            raise typer.BadParameter("Selected repository does not support XML index import.")
        summary = sync_from_xml(xml_paths, report=reporter)
        typer.echo(json_dumps(summary))
        return

    selected = [(name, value) for name, value in (("year", year), ("month", month), ("day", day)) if value]
    if len(selected) != 1:
        raise typer.BadParameter("Provide exactly one of --xml-dir, --year, --month, or --day.")
    sync_by_date = getattr(adapter, "sync_index_by_date", None)
    if sync_by_date is None:
        raise typer.BadParameter("Selected repository does not support date index sync.")
    granularity, value = selected[0]
    summary = sync_by_date(granularity, value, limit=limit, report=reporter)
    typer.echo(json_dumps(summary))


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
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip local Docker/disk/iProX preflight checks."),
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


if __name__ == "__main__":
    app()
