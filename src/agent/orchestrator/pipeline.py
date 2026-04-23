from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agent.ai_ready.exporter import export_ai_ready_bundle
from agent.decision.dda import plan_dda_execution
from agent.execution.fragpipe import FragPipeRunner
from agent.inference.rules import infer_attributes
from agent.metadata.context import build_project_context
from agent.models import DdaExecutionPlan, InputTask, ProjectContext, ProjectResolution, RunManifest
from agent.msdt_converter.config import build_converter_config
from agent.msdt_converter.runner import MSDTConverterRunner
from agent.pride.client import PrideClient
from agent.pride.resolver import resolve_input_to_project
from agent.utils import write_json


class AgentService:
    def __init__(self, pride_client: PrideClient | None = None):
        self.pride_client = pride_client or PrideClient()

    def resolve_project(self, raw_input: str) -> ProjectResolution:
        return resolve_input_to_project(self.pride_client, raw_input)

    def build_context(self, resolution: ProjectResolution, file_name: str) -> ProjectContext:
        if not resolution.primary_project:
            raise ValueError("Cannot build project context without a primary project.")
        return build_project_context(self.pride_client, resolution.primary_project.project_accession, file_name)

    def infer_attributes(self, context: ProjectContext):
        return infer_attributes(context)

    def plan_dda_run(
        self,
        task: InputTask,
        source_data_path: str | Path,
        output_dir: str | Path,
    ) -> tuple[ProjectResolution, ProjectContext, DdaExecutionPlan]:
        resolution = self.resolve_project(task.original_input)
        context = self.build_context(resolution, task.file_name) if resolution.primary_project else ProjectContext(project_accession="unknown", file_name=task.file_name)
        attributes = self.infer_attributes(context)
        plan = plan_dda_execution(
            task_id=task.task_id,
            source_file_name=task.file_name,
            source_data_path=source_data_path,
            project_resolution=resolution,
            attributes=attributes,
            output_dir=output_dir,
        )
        return resolution, context, plan

    def write_task_bundle(
        self,
        output_dir: str | Path,
        resolution: ProjectResolution,
        context: ProjectContext,
        attributes,
        plan: DdaExecutionPlan,
    ) -> None:
        output_dir = Path(output_dir)
        write_json(output_dir / "project_resolution.json", resolution)
        write_json(output_dir / "metadata.json", context)
        write_json(output_dir / "attributes.json", attributes)
        write_json(output_dir / "decision_trace.json", plan)
        write_json(output_dir / "converter_config.json", build_converter_config(plan))

    def run_dda_msdt(
        self,
        task: InputTask,
        source_data_path: str | Path,
        output_dir: str | Path,
        fragpipe_root: str | Path,
        converter_root: str | Path,
        java_home: str | Path | None = None,
    ) -> RunManifest:
        resolution, context, plan = self.plan_dda_run(task, source_data_path, output_dir)
        attributes = self.infer_attributes(context)
        self.write_task_bundle(output_dir, resolution, context, attributes, plan)
        if plan.needs_review:
            manifest = RunManifest(
                task_id=task.task_id,
                created_at=datetime.utcnow(),
                status="needs_review",
                project_accession=resolution.primary_project.project_accession if resolution.primary_project else None,
                source_file=task.file_name,
                source_data_path=str(source_data_path),
                notes=plan.blocking_issues,
            )
            write_json(Path(output_dir) / "run_manifest.json", manifest)
            return manifest

        fragpipe = FragPipeRunner(fragpipe_root=fragpipe_root, java_home=java_home)
        fragpipe.materialize_manifest(plan)
        fragpipe.materialize_workflow_copy(plan)
        fragpipe_result = fragpipe.run(plan)

        converter = MSDTConverterRunner(converter_root=converter_root)
        converter_result = converter.run(plan)

        manifest = RunManifest(
            task_id=task.task_id,
            created_at=datetime.utcnow(),
            status="completed",
            project_accession=resolution.primary_project.project_accession if resolution.primary_project else None,
            source_file=task.file_name,
            source_data_path=str(source_data_path),
            outputs={key: str(value) for key, value in plan.output_paths.items()},
            notes=[fragpipe_result.stdout, converter_result.stdout],
        )
        write_json(Path(output_dir) / "run_manifest.json", manifest)
        return manifest

    def export_ai_ready(
        self,
        msdt_path: str | Path,
        output_dir: str | Path,
        project_accession: str,
        source_file: str,
        attribute_evidence: dict,
        decision_trace: dict,
        run_manifest: dict,
    ) -> Path:
        return export_ai_ready_bundle(
            msdt_path=msdt_path,
            output_dir=output_dir,
            project_accession=project_accession,
            source_file=source_file,
            attribute_evidence=attribute_evidence,
            decision_trace=decision_trace,
            run_manifest=run_manifest,
        )
