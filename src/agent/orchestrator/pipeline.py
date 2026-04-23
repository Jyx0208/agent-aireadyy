from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from agent.ai_ready.exporter import export_ai_ready_bundle
from agent.audit.review import append_review_item, build_review_item, build_task_state_snapshot, write_task_state
from agent.assets.downloader import download_file_asset
from agent.assets.preparer import DockerPwizConverter, RawToMzMLConverter, prepare_file_asset
from agent.assets.resolver import resolve_file_asset
from agent.decision.dda import plan_dda_execution
from agent.execution.bundle import materialize_dda_task_bundle
from agent.execution.fragpipe import FragPipeRunner
from agent.inference.rules import infer_attributes
from agent.llm.reasoner import LLMReasoner, confirm_no_sdrf_parameters, confirm_sdrf_parameters
from agent.metadata.context import build_project_context
from agent.models import DdaExecutionPlan, FileAsset, InputTask, PridePlanResult, ProjectContext, ProjectResolution, RunManifest
from agent.msdt_converter.config import build_converter_config
from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner
from agent.msdt_converter.runner import MSDTConverterRunner
from agent.pride.client import PrideClient
from agent.pride.resolver import resolve_input_to_project
from agent.utils import write_json


class AgentService:
    def __init__(
        self,
        pride_client: PrideClient | None = None,
        reporter: Callable[[str], None] | None = None,
        llm_reasoner: LLMReasoner | None = None,
    ):
        self.pride_client = pride_client or PrideClient()
        self.reporter = reporter
        self.llm_reasoner = llm_reasoner

    def _report(self, message: str) -> None:
        if self.reporter is not None:
            self.reporter(message)

    @staticmethod
    def _format_value(value) -> str:
        if isinstance(value, dict):
            return ", ".join(f"{key}={val}" for key, val in value.items())
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def _report_resolution_summary(self, resolution: ProjectResolution) -> None:
        primary = resolution.primary_project
        if primary is None:
            self._report("Decision summary: no primary PRIDE project resolved.")
            return
        self._report(
            "Decision summary: "
            f"project={primary.project_accession}; matched_file={primary.matched_file}; "
            f"match_type={primary.match_type}; match_score={primary.match_score}; "
            f"resolution_confidence={resolution.resolution_confidence:.2f}"
        )
        if resolution.resolution_reason:
            self._report(f"Decision reason: {resolution.resolution_reason}")

    def _report_metadata_summary(self, context: ProjectContext) -> None:
        organisms = self._format_value(context.metadata.get("organisms").value) if context.metadata.get("organisms") else ""
        instruments = self._format_value(context.metadata.get("instruments").value) if context.metadata.get("instruments") else ""
        experiment_types = self._format_value(context.metadata.get("experimentTypes").value) if context.metadata.get("experimentTypes") else ""
        self._report(
            "Metadata summary: "
            f"sdrf_rows={len(context.sdrf_rows)}; organisms={organisms or 'unknown'}; "
            f"instruments={instruments or 'unknown'}; experiment_types={experiment_types or 'unknown'}"
        )

    def _report_attribute_summary(self, attributes) -> None:
        self._report(
            "Attribute decision: "
            f"acquisition_mode={attributes.acquisition_mode.value} [{attributes.acquisition_mode.source}, {attributes.acquisition_mode.confidence:.2f}]; "
            f"species={attributes.species.value} [{attributes.species.source}, {attributes.species.confidence:.2f}]; "
            f"instrument={attributes.instrument_name.value} [{attributes.instrument_name.source}, {attributes.instrument_name.confidence:.2f}]; "
            f"enzyme={attributes.enzyme.value} [{attributes.enzyme.source}, {attributes.enzyme.confidence:.2f}]"
        )
        self._report(
            "Search decision: "
            f"params={self._format_value(attributes.search_parameter_hints.value) or 'none'}; "
            f"fixed_mods={self._format_value(attributes.fixed_mods.value) or 'none'}; "
            f"variable_mods={self._format_value(attributes.variable_mods.value) or 'none'}"
        )

    def _report_plan_summary(self, plan: DdaExecutionPlan) -> None:
        self._report(
            "Execution decision: "
            f"raw_type={plan.raw_data_type}; fasta={plan.fasta_path.name} ({plan.fasta_selection_mode}); "
            f"workflow={plan.fragpipe_workflow_path.name}; threads={plan.thread_num}"
        )
        self._report(
            "Execution outputs: "
            f"rawspectrum={plan.rawspectrum_output_path}; fp_pin={plan.expected_pin_path}; "
            f"fp_msdt={plan.output_paths['fp_msdt']}"
        )

    def _report_asset_summary(self, asset: FileAsset) -> None:
        self._report(
            "Asset decision: "
            f"resolved_type={asset.resolved_asset_type}; matched_file={asset.matched_project_file or 'unknown'}; "
            f"requires_conversion={asset.requires_conversion}; asset_confidence={asset.asset_confidence:.2f}"
        )

    def resolve_project(self, raw_input: str) -> ProjectResolution:
        self._report(f"[1/5] Resolving project for file: {raw_input}")
        return resolve_input_to_project(self.pride_client, raw_input)

    def build_context(self, resolution: ProjectResolution, file_name: str) -> ProjectContext:
        if not resolution.primary_project:
            raise ValueError("Cannot build project context without a primary project.")
        return build_project_context(self.pride_client, resolution.primary_project.project_accession, file_name)

    def infer_attributes(self, context: ProjectContext):
        attributes = infer_attributes(context)
        attributes = confirm_sdrf_parameters(
            context,
            attributes,
            llm_reasoner=self.llm_reasoner,
            report=self._report,
        )
        return confirm_no_sdrf_parameters(
            context,
            attributes,
            llm_reasoner=self.llm_reasoner,
            report=self._report,
        )

    def resolve_asset(self, task: InputTask, context: ProjectContext, output_dir: str | Path) -> FileAsset:
        return resolve_file_asset(task=task, context=context, work_dir=output_dir)

    def download_asset(self, asset: FileAsset) -> Path:
        return download_file_asset(self.pride_client, asset, report=self.reporter)

    def prepare_asset(self, asset: FileAsset, converter: RawToMzMLConverter | None = None) -> Path:
        primary = converter or RawToMzMLConverter(report=self.reporter)
        fallback = DockerPwizConverter(report=self.reporter)
        return prepare_file_asset(self.pride_client, asset, primary, fallback_converter=fallback, report=self.reporter)

    def plan_dda_run(
        self,
        task: InputTask,
        source_data_path: str | Path,
        output_dir: str | Path,
    ) -> tuple[ProjectResolution, ProjectContext, DdaExecutionPlan]:
        resolution = self.resolve_project(task.original_input)
        context = self.build_context(resolution, task.file_name) if resolution.primary_project else ProjectContext(project_accession="unknown", file_name=task.file_name)
        attributes = self.infer_attributes(context)
        self._report_resolution_summary(resolution)
        self._report_metadata_summary(context)
        self._report_attribute_summary(attributes)
        plan = plan_dda_execution(
            task_id=task.task_id,
            source_file_name=task.file_name,
            source_data_path=source_data_path,
            project_resolution=resolution,
            attributes=attributes,
            output_dir=output_dir,
        )
        self._report_plan_summary(plan)
        return resolution, context, plan

    def plan_dda_run_from_pride(
        self,
        task: InputTask,
        output_dir: str | Path,
    ) -> PridePlanResult:
        resolution = self.resolve_project(task.original_input)
        if resolution.primary_project:
            self._report(f"Selected primary project: {resolution.primary_project.project_accession}")
        else:
            self._report("No primary project could be resolved.")
        self._report_resolution_summary(resolution)
        context = self.build_context(resolution, task.file_name) if resolution.primary_project else ProjectContext(
            project_accession="unknown",
            file_name=task.file_name,
        )
        self._report(f"[2/5] Project context ready. SDRF rows: {len(context.sdrf_rows)}")
        self._report_metadata_summary(context)
        asset = self.resolve_asset(task, context, output_dir)
        self._report(
            f"[3/5] Resolved asset: {asset.matched_project_file or 'unknown'} "
            f"({asset.resolved_asset_type}, requires_conversion={asset.requires_conversion})"
        )
        self._report_asset_summary(asset)
        attributes = self.infer_attributes(context)
        self._report(f"[4/5] Attribute inference complete. acquisition_mode={attributes.acquisition_mode.value}")
        self._report_attribute_summary(attributes)
        source_data_path = asset.prepared_path or asset.local_path or Path(output_dir) / "assets" / "prepared" / f"{task.stem}.mzML"
        plan = plan_dda_execution(
            task_id=task.task_id,
            source_file_name=task.file_name,
            source_data_path=source_data_path,
            project_resolution=resolution,
            attributes=attributes,
            output_dir=output_dir,
        )
        self._report(f"[5/5] DDA execution plan ready. workflow={plan.fragpipe_workflow_path.name}")
        self._report_plan_summary(plan)
        return PridePlanResult(
            resolution=resolution,
            context=context,
            asset=asset,
            attributes=attributes,
            plan=plan,
        )

    def write_task_bundle(
        self,
        output_dir: str | Path,
        resolution: ProjectResolution,
        context: ProjectContext,
        attributes,
        plan: DdaExecutionPlan,
        asset: FileAsset | None = None,
    ) -> None:
        output_dir = Path(output_dir)
        write_json(output_dir / "project_resolution.json", resolution)
        write_json(output_dir / "metadata.json", context)
        if asset is not None:
            write_json(output_dir / "asset_resolution.json", asset)
        write_json(output_dir / "attributes.json", attributes)
        write_json(output_dir / "decision_trace.json", plan)
        write_json(output_dir / "converter_config.json", build_converter_config(plan))
        status = "needs_review" if plan.needs_review else "resolved"
        state = build_task_state_snapshot(
            task_id=plan.task_id,
            status=status,
            stage="planning",
            source_file=plan.source_file_name,
            project_accession=resolution.primary_project.project_accession if resolution.primary_project else None,
            notes=plan.blocking_issues if plan.needs_review else [],
        )
        write_task_state(output_dir / "task_state.json", state)
        if plan.needs_review:
            review_item = build_review_item(
                task_id=plan.task_id,
                source_file=plan.source_file_name,
                project_accession=resolution.primary_project.project_accession if resolution.primary_project else None,
                stage="planning",
                reasons=plan.blocking_issues,
            )
            append_review_item(output_dir / "review_queue.json", review_item)

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
                created_at=datetime.now(UTC),
                status="needs_review",
                project_accession=resolution.primary_project.project_accession if resolution.primary_project else None,
                source_file=task.file_name,
                source_data_path=str(source_data_path),
                notes=plan.blocking_issues,
            )
            write_json(Path(output_dir) / "run_manifest.json", manifest)
            write_task_state(
                Path(output_dir) / "task_state.json",
                build_task_state_snapshot(
                    task_id=task.task_id,
                    status="needs_review",
                    stage="execution",
                    source_file=task.file_name,
                    project_accession=resolution.primary_project.project_accession if resolution.primary_project else None,
                    notes=plan.blocking_issues,
                ),
            )
            return manifest

        fragpipe = FragPipeRunner(fragpipe_root=fragpipe_root, java_home=java_home)
        fragpipe.materialize_manifest(plan)
        fragpipe.materialize_workflow_copy(plan, attributes=attributes)
        fragpipe_result = fragpipe.run(plan, attributes=attributes)

        converter = MSDTConverterRunner(converter_root=converter_root)
        converter_result = converter.run(plan)

        manifest = RunManifest(
            task_id=task.task_id,
            created_at=datetime.now(UTC),
            status="completed",
            project_accession=resolution.primary_project.project_accession if resolution.primary_project else None,
            source_file=task.file_name,
            source_data_path=str(source_data_path),
            outputs={key: str(value) for key, value in plan.output_paths.items()},
            notes=[fragpipe_result.stdout, converter_result.stdout],
        )
        write_json(Path(output_dir) / "run_manifest.json", manifest)
        write_task_state(
            Path(output_dir) / "task_state.json",
            build_task_state_snapshot(
                task_id=task.task_id,
                status="completed",
                stage="execution",
                source_file=task.file_name,
                project_accession=resolution.primary_project.project_accession if resolution.primary_project else None,
                notes=[],
            ),
        )
        return manifest

    def run_pride_dda_msdt_docker(
        self,
        task: InputTask,
        output_dir: str | Path,
        image: str = "guomics2017/msdt-converter:v1.3",
    ) -> RunManifest:
        result = self.plan_dda_run_from_pride(task=task, output_dir=output_dir)
        prepared_path = self.prepare_asset(result.asset)
        bundle = materialize_dda_task_bundle(
            task=task,
            project_resolution=result.resolution,
            project_context=result.context,
            attributes=result.attributes,
            source_data_path=prepared_path,
            output_dir=output_dir,
        )
        self.write_task_bundle(
            output_dir,
            result.resolution,
            result.context,
            result.attributes,
            bundle.plan,
            asset=result.asset,
        )
        self._report(
            "Materialized execution assets: "
            f"workflow={bundle.materialized_workflow_path}; fasta={bundle.materialized_fasta_path}; "
            f"converter_config={bundle.converter_config_path}"
        )
        self._report("Materialized task bundle. Starting Docker MSDT pipeline.")
        runner = DockerMSDTConverterRunner(image=image, report=self.reporter)
        docker_result = runner.run(bundle)
        msdt_output = bundle.plan.output_paths.get("fp_msdt")
        if msdt_output is None or not msdt_output.exists():
            notes = [docker_result.stdout, f"MSDT output missing: {msdt_output}"]
            manifest = RunManifest(
                task_id=task.task_id,
                created_at=datetime.now(UTC),
                status="failed",
                project_accession=result.resolution.primary_project.project_accession if result.resolution.primary_project else None,
                source_file=task.file_name,
                source_data_path=str(prepared_path),
                outputs={key: str(value) for key, value in bundle.plan.output_paths.items()},
                notes=notes,
            )
            write_json(Path(output_dir) / "run_manifest.json", manifest)
            write_task_state(
                Path(output_dir) / "task_state.json",
                build_task_state_snapshot(
                    task_id=task.task_id,
                    status="failed",
                    stage="execution",
                    source_file=task.file_name,
                    project_accession=result.resolution.primary_project.project_accession if result.resolution.primary_project else None,
                    notes=notes,
                ),
            )
            review_item = build_review_item(
                task_id=task.task_id,
                source_file=task.file_name,
                project_accession=result.resolution.primary_project.project_accession if result.resolution.primary_project else None,
                stage="execution",
                reasons=[f"MSDT output missing: {msdt_output}"],
            )
            append_review_item(Path(output_dir) / "review_queue.json", review_item)
            return manifest
        manifest = RunManifest(
            task_id=task.task_id,
            created_at=datetime.now(UTC),
            status="completed",
            project_accession=result.resolution.primary_project.project_accession if result.resolution.primary_project else None,
            source_file=task.file_name,
            source_data_path=str(prepared_path),
            outputs={key: str(value) for key, value in bundle.plan.output_paths.items()},
            notes=[docker_result.stdout],
        )
        write_json(Path(output_dir) / "run_manifest.json", manifest)
        write_task_state(
            Path(output_dir) / "task_state.json",
            build_task_state_snapshot(
                task_id=task.task_id,
                status="completed",
                stage="execution",
                source_file=task.file_name,
                project_accession=result.resolution.primary_project.project_accession if result.resolution.primary_project else None,
                notes=[],
            ),
        )
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
