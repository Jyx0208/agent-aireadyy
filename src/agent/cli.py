from __future__ import annotations

from pathlib import Path

import typer

from agent.ai_ready.exporter import export_ai_ready_bundle
from agent.execution.bundle import materialize_dda_task_bundle
from agent.input.normalizer import normalize_input
from agent.orchestrator.pipeline import AgentService
from agent.progress import render_download_progress
from agent.runtime.bootstrap import bootstrap_msdt_converter
from agent.runtime.toolchain import detect_toolchain
from agent.utils import write_json

app = typer.Typer(help="PRIDE-first AI-ready data agent aligned with MSDT-Converter.")

class ConsoleReporter:
    def __init__(self, log_path: Path | None = None) -> None:
        self._progress_open = False
        self._last_progress_len = 0
        self._last_progress_line: str | None = None
        self._log_path = log_path
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(self, message: str) -> None:
        if self._log_path is not None:
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")

    def __call__(self, message) -> None:
        if isinstance(message, dict) and message.get("kind") == "download_progress":
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

        if self._progress_open:
            typer.echo("", err=True)
            self._progress_open = False
            self._last_progress_len = 0
            self._last_progress_line = None
        self._log(str(message))
        typer.echo(str(message), err=True)

def _build_reporter(output_dir: Path | None = None) -> ConsoleReporter:
    log_path = None
    if output_dir is not None:
        log_path = output_dir / "logs" / "runtime.log"
    return ConsoleReporter(log_path=log_path)


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
) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    manifest = service.run_pride_dda_msdt_docker(task=task, output_dir=output_dir, image=image)
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


@app.command("run-dda-msdt")
def run_dda_msdt(
    input_value: str,
    source_data_path: Path,
    output_dir: Path,
    fragpipe_root: Path = typer.Option(..., help="Pinned FragPipe root, expected version family 21.1."),
    converter_root: Path = typer.Option(..., help="Path to the MSDT-Converter repository."),
    java_home: Path | None = typer.Option(None, help="Optional JAVA_HOME override."),
) -> None:
    service = AgentService(reporter=_build_reporter(output_dir))
    task = normalize_input(input_value)
    manifest = service.run_dda_msdt(
        task=task,
        source_data_path=source_data_path,
        output_dir=output_dir,
        fragpipe_root=fragpipe_root,
        converter_root=converter_root,
        java_home=java_home,
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
