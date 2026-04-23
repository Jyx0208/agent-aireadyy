from __future__ import annotations

from pathlib import Path

import typer

from agent.ai_ready.exporter import export_ai_ready_bundle
from agent.execution.bundle import materialize_dda_task_bundle
from agent.input.normalizer import normalize_input
from agent.orchestrator.pipeline import AgentService
from agent.runtime.bootstrap import bootstrap_msdt_converter
from agent.runtime.toolchain import detect_toolchain
from agent.utils import write_json

app = typer.Typer(help="PRIDE-first AI-ready data agent aligned with MSDT-Converter.")


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
    service = AgentService()
    resolution = service.resolve_project(input_value)
    typer.echo(resolution.model_dump_json(indent=2))


@app.command("infer-attributes")
def infer_attributes(input_value: str) -> None:
    service = AgentService()
    task = normalize_input(input_value)
    resolution = service.resolve_project(input_value)
    if not resolution.primary_project:
        raise typer.BadParameter("No PRIDE project could be resolved for the input.")
    context = service.build_context(resolution, task.file_name)
    attributes = service.infer_attributes(context)
    typer.echo(attributes.model_dump_json(indent=2))


@app.command("plan-dda-run")
def plan_dda_run(input_value: str, source_data_path: Path, output_dir: Path) -> None:
    service = AgentService()
    task = normalize_input(input_value)
    resolution, context, plan = service.plan_dda_run(task, source_data_path, output_dir)
    attributes = service.infer_attributes(context)
    service.write_task_bundle(output_dir, resolution, context, attributes, plan)
    typer.echo(plan.model_dump_json(indent=2))


@app.command("prepare-dda-bundle")
def prepare_dda_bundle(input_value: str, source_data_path: Path, output_dir: Path) -> None:
    service = AgentService()
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
    service = AgentService()
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
