from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from agent.ai_ready.exporter import export_ai_ready_bundle
from agent.audit.review import append_review_item, build_review_item, build_task_state_snapshot, write_task_state
from agent.assets.downloader import download_file_asset
from agent.assets.preparer import AssetPreparationError, DockerPwizConverter, RawToMzMLConverter, prepare_file_asset
from agent.assets.resolver import resolve_file_asset
from agent.decision.dda import plan_dda_execution
from agent.execution.bundle import materialize_dda_task_bundle
from agent.inference.rules import infer_attributes
from agent.inference.mzml_metadata import infer_instrument_family_from_name, parse_mzml_instrument
from agent.llm.reasoner import LLMReasoner, confirm_no_sdrf_parameters, confirm_sdrf_parameters
from agent.metadata.context import build_project_context
from agent.models import AttributeValue, DdaExecutionPlan, FileAsset, InputTask, PridePlanResult, ProjectContext, ProjectResolution, RunManifest
from agent.msdt_converter.config import build_converter_config
from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner
from agent.msdt_converter.runner import MSDTConverterRunner
from agent.pride.client import PrideClient
from agent.pride.resolver import resolve_input_to_project
from agent.utils import write_json


class ReviewRequiredError(RuntimeError):
    pass


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

        # 强制要求 LLM 推理器
        if self.llm_reasoner is None:
            from agent.llm.reasoner import default_llm_reasoner
            self.llm_reasoner = default_llm_reasoner()

        if self.llm_reasoner is None:
            raise ValueError(
                "必须配置大模型 API 才能运行。请设置环境变量 AGENT_LLM_API_KEY。\n"
                "示例配置：\n"
                "  AGENT_LLM_API_KEY=your_api_key\n"
                "  AGENT_LLM_BASE_URL=https://api.siliconflow.cn/v1\n"
                "  AGENT_LLM_MODEL=deepseek-v4-flash"
            )

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

    @staticmethod
    def _search_hints(attributes) -> dict[str, Any]:
        hints = attributes.search_parameter_hints.value
        return dict(hints) if isinstance(hints, dict) else {}

    @staticmethod
    def _can_retry_with_reviewed_llm_fasta(plan: DdaExecutionPlan, attributes) -> bool:
        hints = AgentService._search_hints(attributes)
        return (
            plan.needs_review
            and bool(hints.get("recommended_fasta_url"))
            and any("占位" in issue or "placeholder" in issue.lower() for issue in plan.blocking_issues)
        )

    @staticmethod
    def _search_review_issues(plan: DdaExecutionPlan) -> list[str]:
        return [issue for issue in plan.blocking_issues if "搜库参数需要人工复核" in issue]

    @staticmethod
    def _can_retry_with_mzml_instrument(plan: DdaExecutionPlan) -> bool:
        if not plan.needs_review or not any("仪器" in issue for issue in plan.blocking_issues):
            return False
        hard_markers = (
            "DIA",
            "mzIdentML",
            "Top-down",
            "Top down",
            "workflow",
            "FASTA",
            "占位",
            "缺少必需属性：酶切酶",
            "无法确认采集模式",
        )
        return not any(any(marker in issue for marker in hard_markers) for issue in plan.blocking_issues)

    @staticmethod
    def _accept_reviewed_search_parameters(plan: DdaExecutionPlan) -> DdaExecutionPlan:
        remaining_issues = [issue for issue in plan.blocking_issues if "搜库参数需要人工复核" not in issue]
        return plan.model_copy(update={"blocking_issues": remaining_issues, "needs_review": bool(remaining_issues)})

    @staticmethod
    def _runtime_log_path(output_dir: str | Path) -> Path:
        return Path(output_dir) / "logs" / "runtime.log"

    @staticmethod
    def _write_run_log(output_dir: str | Path, stdout: str, stderr: str = "") -> Path:
        log_path = Path(output_dir) / "logs" / "run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        content = stdout
        if stderr:
            content = f"{content}\n\n[stderr]\n{stderr}" if content else f"[stderr]\n{stderr}"
        log_path.write_text(content, encoding="utf-8")
        return log_path

    @staticmethod
    def _uniprot_proteome_url(proteome_id: str) -> str:
        return f"https://rest.uniprot.org/uniprotkb/stream?compressed=false&format=fasta&query=%28proteome%3A{proteome_id}%29"

    @staticmethod
    def _fasta_recommendation(result: PridePlanResult) -> dict[str, Any] | None:
        hints = AgentService._search_hints(result.attributes)
        hint_text = " ".join(
            str(value)
            for value in (
                hints.get("recommended_fasta_name"),
                hints.get("recommended_fasta_source"),
                hints.get("database"),
                result.attributes.species.value,
            )
            if value
        )
        proteome_match = re.search(r"\bUP\d{9}\b", hint_text, re.IGNORECASE)
        derived_url = AgentService._uniprot_proteome_url(proteome_match.group(0).upper()) if proteome_match else None
        url = hints.get("recommended_fasta_url") or derived_url or result.plan.fasta_download_url
        if not url:
            return None
        return {
            "name": result.plan.fasta_path.name,
            "url": url,
            "source": hints.get("recommended_fasta_source") or ("LLM/agent 根据物种或 UniProt proteome ID 推断"),
            "database": hints.get("database"),
            "workflow": hints.get("recommended_workflow_name"),
        }

    def _report_resolution_summary(self, resolution: ProjectResolution) -> None:
        primary = resolution.primary_project
        if primary is None:
            self._report("项目解析摘要：未解析到主 PRIDE 项目。")
            return
        self._report(
            "项目解析摘要："
            f"项目={primary.project_accession}；匹配文件={primary.matched_file}；"
            f"匹配类型={primary.match_type}；匹配分数={primary.match_score}；"
            f"解析置信度={resolution.resolution_confidence:.2f}"
        )
        if resolution.resolution_reason:
            self._report(f"解析原因：{resolution.resolution_reason}")

    def _report_metadata_summary(self, context: ProjectContext) -> None:
        organisms = self._format_value(context.metadata.get("organisms").value) if context.metadata.get("organisms") else ""
        instruments = self._format_value(context.metadata.get("instruments").value) if context.metadata.get("instruments") else ""
        experiment_types = self._format_value(context.metadata.get("experimentTypes").value) if context.metadata.get("experimentTypes") else ""
        self._report(
            "项目元数据摘要："
            f"SDRF 行数={len(context.sdrf_rows)}；物种={organisms or '未知'}；"
            f"仪器={instruments or '未知'}；实验类型={experiment_types or '未知'}"
        )

    def _report_attribute_summary(self, attributes) -> None:
        hints = attributes.search_parameter_hints.value if isinstance(attributes.search_parameter_hints.value, dict) else {}
        self._report(
            "属性判断："
            f"采集模式={attributes.acquisition_mode.value} [{attributes.acquisition_mode.source}, {attributes.acquisition_mode.confidence:.2f}]；"
            f"物种={attributes.species.value} [{attributes.species.source}, {attributes.species.confidence:.2f}]；"
            f"仪器={attributes.instrument_name.value} [{attributes.instrument_name.source}, {attributes.instrument_name.confidence:.2f}]；"
            f"酶={attributes.enzyme.value} [{attributes.enzyme.source}, {attributes.enzyme.confidence:.2f}]"
        )
        self._report(
            "搜库参数判断："
            f"参数={self._format_value(attributes.search_parameter_hints.value) or '无'}；"
            f"固定修饰={self._format_value(attributes.fixed_mods.value) or '无'}；"
            f"可变修饰={self._format_value(attributes.variable_mods.value) or '无'}"
        )
        if hints.get("data_family") or hints.get("sidecar_patterns"):
            self._report(
                "数据适配提示："
                f"数据类型={hints.get('data_family', '未知')}；"
                f"sidecar 文件模式={self._format_value(hints.get('sidecar_patterns', [])) or '无'}"
            )

    def _report_plan_summary(self, plan: DdaExecutionPlan) -> None:
        if plan.fasta_download_url:
            self._report(f"FASTA 下载源：{plan.fasta_download_url}")
        self._report(
            "执行计划："
            f"原始数据类型={plan.raw_data_type}；FASTA={plan.fasta_path.name} ({plan.fasta_selection_mode})；"
            f"workflow={plan.fragpipe_workflow_path.name}；线程数={plan.thread_num}"
        )
        self._report(
            "预期输出："
            f"rawspectrum={plan.rawspectrum_output_path}；fp_pin={plan.expected_pin_path}；"
            f"fp_msdt={plan.output_paths['fp_msdt']}"
        )

    def _report_asset_summary(self, asset: FileAsset) -> None:
        self._report(
            "文件资产判断："
            f"解析类型={asset.resolved_asset_type}；匹配文件={asset.matched_project_file or '未知'}；"
            f"是否需要转换={asset.requires_conversion}；资产置信度={asset.asset_confidence:.2f}"
        )

    def resolve_project(self, raw_input: str) -> ProjectResolution:
        self._report(f"[1/5] 正在根据文件名解析 PRIDE 项目：{raw_input}")
        self._report({"kind": "activity_start", "label": "正在查询 PRIDE Archive API 并匹配项目/文件…"})
        try:
            return resolve_input_to_project(self.pride_client, raw_input)
        finally:
            self._report({"kind": "activity_stop", "message": "PRIDE 查询完成。"})

    def build_context(self, resolution: ProjectResolution, file_name: str) -> ProjectContext:
        if not resolution.primary_project:
            raise ValueError("无法构建项目上下文：缺少主项目。")
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

    @staticmethod
    def _attributes_with_review_overrides(attributes, overrides: dict[str, Any] | None):
        if not overrides:
            return attributes
        updates: dict[str, AttributeValue] = {}
        species = str(overrides.get("species") or "").strip()
        if species:
            updates["species"] = AttributeValue(
                value=species,
                confidence=1.0,
                source="user_review",
                evidence_excerpt=f"用户复核选择：{species}",
                conflict_flag=False,
            )
        instrument_name = str(overrides.get("instrument_name") or "").strip()
        if instrument_name:
            family = infer_instrument_family_from_name(instrument_name)
            updates["instrument_name"] = AttributeValue(
                value=instrument_name,
                confidence=1.0,
                source="user_review",
                evidence_excerpt=f"用户复核选择：{instrument_name}",
                conflict_flag=False,
            )
            updates["instrument_family"] = AttributeValue(
                value=family,
                confidence=1.0 if family != "unknown" else 0.4,
                source="user_review",
                evidence_excerpt=f"用户复核选择：{instrument_name}",
                conflict_flag=family == "unknown",
            )
        return attributes.model_copy(update=updates) if updates else attributes

    def apply_review_overrides_to_result(
        self,
        result: PridePlanResult,
        overrides: dict[str, Any] | None,
        task: InputTask,
        output_dir: str | Path,
        prefer_project_fasta: bool = False,
    ) -> PridePlanResult:
        attributes = self._attributes_with_review_overrides(result.attributes, overrides)
        if attributes is result.attributes:
            return result
        source_data_path = (
            result.asset.prepared_path
            or result.asset.local_path
            or Path(output_dir) / "assets" / "prepared" / f"{task.stem}.mzML"
        )
        plan = plan_dda_execution(
            task_id=task.task_id,
            source_file_name=task.file_name,
            source_data_path=source_data_path,
            project_resolution=result.resolution,
            attributes=attributes,
            output_dir=output_dir,
            project_context=result.context,
            prefer_project_fasta=prefer_project_fasta,
        )
        return result.model_copy(update={"attributes": attributes, "plan": plan})

    def replan_with_mzml_instrument(
        self,
        result: PridePlanResult,
        prepared_path: str | Path,
        task: InputTask,
        output_dir: str | Path,
        prefer_project_fasta: bool = False,
        reviewed_fasta_path: str | Path | None = None,
        reviewed_fasta_url: str | None = None,
        reviewed_fasta_name: str | None = None,
        accept_search_parameter_review: bool = False,
    ) -> PridePlanResult:
        metadata = parse_mzml_instrument(prepared_path)
        if metadata is None:
            self._report("未能从 mzML 中解析到文件级仪器信息；保留人工复核。")
            return result
        self._report(f"已从 mzML 解析文件级仪器：{metadata.name}（{metadata.family}）。")
        attributes = result.attributes.model_copy(
            update={
                "instrument_name": AttributeValue(
                    value=metadata.name,
                    confidence=1.0,
                    source="mzml",
                    evidence_excerpt=metadata.evidence,
                    conflict_flag=False,
                ),
                "instrument_family": AttributeValue(
                    value=metadata.family,
                    confidence=1.0 if metadata.family != "unknown" else 0.4,
                    source="mzml",
                    evidence_excerpt=metadata.evidence,
                    conflict_flag=metadata.family == "unknown",
                ),
            }
        )
        plan = plan_dda_execution(
            task_id=task.task_id,
            source_file_name=task.file_name,
            source_data_path=prepared_path,
            project_resolution=result.resolution,
            attributes=attributes,
            output_dir=output_dir,
            project_context=result.context,
            reviewed_fasta_path=reviewed_fasta_path,
            reviewed_fasta_url=reviewed_fasta_url,
            reviewed_fasta_name=reviewed_fasta_name,
            prefer_project_fasta=prefer_project_fasta,
            accept_search_parameter_review=accept_search_parameter_review,
        )
        return result.model_copy(update={"attributes": attributes, "plan": plan})

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
            project_context=context,
        )
        self._report_plan_summary(plan)
        return resolution, context, plan

    def plan_dda_run_from_pride(
        self,
        task: InputTask,
        output_dir: str | Path,
        reviewed_fasta_path: str | Path | None = None,
        reviewed_fasta_url: str | None = None,
        reviewed_fasta_name: str | None = None,
        prefer_project_fasta: bool = False,
    ) -> PridePlanResult:
        resolution = self.resolve_project(task.original_input)
        if resolution.primary_project:
            self._report(f"已选择主项目：{resolution.primary_project.project_accession}")
        else:
            self._report("未能解析到主项目。")
        self._report_resolution_summary(resolution)
        context = self.build_context(resolution, task.file_name) if resolution.primary_project else ProjectContext(
            project_accession="unknown",
            file_name=task.file_name,
        )
        self._report(f"[2/5] 项目上下文已准备完成。SDRF 行数：{len(context.sdrf_rows)}")
        self._report_metadata_summary(context)
        asset = self.resolve_asset(task, context, output_dir)
        self._report(
            f"[3/5] 已解析数据文件：{asset.matched_project_file or '未知'} "
            f"（类型={asset.resolved_asset_type}，是否需要转换={asset.requires_conversion}）"
        )
        self._report_asset_summary(asset)
        attributes = self.infer_attributes(context)
        self._report(f"[4/5] 文件属性推断完成。采集模式={attributes.acquisition_mode.value}")
        self._report_attribute_summary(attributes)
        source_data_path = asset.prepared_path or asset.local_path or Path(output_dir) / "assets" / "prepared" / f"{task.stem}.mzML"
        plan = plan_dda_execution(
            task_id=task.task_id,
            source_file_name=task.file_name,
            source_data_path=source_data_path,
            project_resolution=resolution,
            attributes=attributes,
            output_dir=output_dir,
            project_context=context,
            reviewed_fasta_path=reviewed_fasta_path,
            reviewed_fasta_url=reviewed_fasta_url,
            reviewed_fasta_name=reviewed_fasta_name,
            prefer_project_fasta=prefer_project_fasta,
        )
        self._report(f"[5/5] DDA 执行计划已生成。workflow={plan.fragpipe_workflow_path.name}")
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
        converter_root: str | Path,
    ) -> RunManifest:
        resolution, context, plan = self.plan_dda_run(task, source_data_path, output_dir)
        attributes = self.infer_attributes(context)
        if plan.needs_review:
            self.write_task_bundle(output_dir, resolution, context, attributes, plan)
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

        bundle = materialize_dda_task_bundle(
            task=task,
            project_resolution=resolution,
            project_context=context,
            attributes=attributes,
            source_data_path=source_data_path,
            output_dir=output_dir,
        )
        self.write_task_bundle(output_dir, resolution, context, attributes, bundle.plan)

        converter = MSDTConverterRunner(converter_root=converter_root)
        converter_result = converter.run(bundle.plan)

        manifest = RunManifest(
            task_id=task.task_id,
            created_at=datetime.now(UTC),
            status="completed",
            project_accession=resolution.primary_project.project_accession if resolution.primary_project else None,
            source_file=task.file_name,
            source_data_path=str(source_data_path),
            outputs={key: str(value) for key, value in bundle.plan.output_paths.items()},
            notes=[converter_result.stdout],
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
        reviewed_fasta_path: str | Path | None = None,
        reviewed_fasta_url: str | None = None,
        confirm_search_parameters: Callable[[PridePlanResult], bool] | None = None,
    ) -> RunManifest:
        bundle, result, prepared_path = self.prepare_pride_msdt_docker_input(
            task=task,
            output_dir=output_dir,
            reviewed_fasta_path=reviewed_fasta_path,
            reviewed_fasta_url=reviewed_fasta_url,
            confirm_search_parameters=confirm_search_parameters,
        )
        self._report("任务输入包已生成，开始运行 MSDT-Converter Docker 流程。")
        runner = DockerMSDTConverterRunner(image=image, report=self.reporter)
        docker_result = runner.run(bundle)
        run_log_path = self._write_run_log(output_dir, docker_result.stdout, docker_result.stderr)
        outputs = {key: str(value) for key, value in bundle.plan.output_paths.items()}
        outputs["run_log"] = str(run_log_path)
        runtime_log_path = self._runtime_log_path(output_dir)
        if runtime_log_path.exists():
            outputs["runtime_log"] = str(runtime_log_path)
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
                outputs=outputs,
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
        initial_manifest = RunManifest(
            task_id=task.task_id,
            created_at=datetime.now(UTC),
            status="completed",
            project_accession=result.resolution.primary_project.project_accession if result.resolution.primary_project else None,
            source_file=task.file_name,
            source_data_path=str(prepared_path),
            outputs=outputs,
            notes=[docker_result.stdout],
        )
        ai_ready_path = self.export_ai_ready(
            msdt_path=msdt_output,
            output_dir=Path(output_dir) / "ai_ready",
            project_accession=initial_manifest.project_accession or "",
            source_file=task.file_name,
            attribute_evidence=result.attributes.model_dump(mode="json"),
            decision_trace=bundle.plan.model_dump(mode="json"),
            run_manifest=initial_manifest.model_dump(mode="json"),
        )
        outputs["ai_ready"] = str(ai_ready_path)
        manifest = initial_manifest.model_copy(update={"outputs": outputs})
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

    def prepare_pride_msdt_docker_input(
        self,
        task: InputTask,
        output_dir: str | Path,
        reviewed_fasta_path: str | Path | None = None,
        reviewed_fasta_url: str | None = None,
        reviewed_fasta_name: str | None = None,
        prefer_project_fasta: bool = False,
        confirm_llm_recommended_fasta: Callable[[dict[str, Any]], bool] | None = None,
        confirm_search_parameters: Callable[[PridePlanResult], bool] | None = None,
    ):
        result = self.plan_dda_run_from_pride(
            task=task,
            output_dir=output_dir,
            reviewed_fasta_path=reviewed_fasta_path,
            reviewed_fasta_url=reviewed_fasta_url,
            reviewed_fasta_name=reviewed_fasta_name,
            prefer_project_fasta=prefer_project_fasta,
        )
        active_reviewed_fasta_path = reviewed_fasta_path
        active_reviewed_fasta_url = reviewed_fasta_url
        active_reviewed_fasta_name = reviewed_fasta_name
        search_parameters_reviewed = False
        if confirm_llm_recommended_fasta is not None and active_reviewed_fasta_path is None and active_reviewed_fasta_url is None:
            recommendation = self._fasta_recommendation(result)
            if recommendation is not None and confirm_llm_recommended_fasta(recommendation):
                active_reviewed_fasta_url = str(recommendation["url"])
                active_reviewed_fasta_name = str(recommendation["name"] or "")
                self._report(f"已确认使用 LLM 推荐 FASTA：{active_reviewed_fasta_name or active_reviewed_fasta_url}")
                source_data_path = (
                    result.asset.prepared_path
                    or result.asset.local_path
                    or Path(output_dir) / "assets" / "prepared" / f"{task.stem}.mzML"
                )
                reviewed_plan = plan_dda_execution(
                    task_id=task.task_id,
                    source_file_name=task.file_name,
                    source_data_path=source_data_path,
                    project_resolution=result.resolution,
                    attributes=result.attributes,
                    output_dir=output_dir,
                    project_context=result.context,
                    reviewed_fasta_url=active_reviewed_fasta_url,
                    reviewed_fasta_name=active_reviewed_fasta_name,
                    prefer_project_fasta=prefer_project_fasta,
                )
                result = result.model_copy(update={"plan": reviewed_plan})
        if result.plan.needs_review and self._search_review_issues(result.plan):
            if confirm_search_parameters is not None and confirm_search_parameters(result):
                accepted_plan = self._accept_reviewed_search_parameters(result.plan)
                result = result.model_copy(update={"plan": accepted_plan})
                search_parameters_reviewed = True
                self._report("人工已确认搜库参数；继续处理剩余步骤。")
        prepared_path: Path | None = None
        if result.plan.needs_review and self._can_retry_with_mzml_instrument(result.plan):
            self._report("检测到项目级多个仪器；先准备/转换 mzML，并尝试从 mzML 解析文件级仪器。")
            prepared_path = self.prepare_asset(result.asset)
            result = self.replan_with_mzml_instrument(
                result,
                prepared_path,
                task,
                output_dir,
                prefer_project_fasta=prefer_project_fasta,
                reviewed_fasta_path=active_reviewed_fasta_path,
                reviewed_fasta_url=active_reviewed_fasta_url,
                reviewed_fasta_name=active_reviewed_fasta_name,
                accept_search_parameter_review=search_parameters_reviewed,
            )
        if result.plan.needs_review:
            self.write_task_bundle(
                output_dir,
                result.resolution,
                result.context,
                result.attributes,
                result.plan,
                asset=result.asset,
            )
            message = f"当前计划需要人工复核，暂不下载或准备数据文件。原因：{result.plan.blocking_issues}"
            self._report(message)
            raise ReviewRequiredError(message)
        try:
            if prepared_path is None:
                prepared_path = self.prepare_asset(result.asset)
        except AssetPreparationError as exc:
            output_dir = Path(output_dir)
            reason = (
                "数据文件准备需要 RAW/vendor 格式转换，但当前没有可用转换器。"
                "请安装 ProteoWizard msconvert，或启动 Docker Desktop 以使用 ProteoWizard Docker 备用转换。"
                f"已下载的文件保留在：{exc.local_path}。详细信息：{exc}"
            )
            self._report(reason)
            self.write_task_bundle(
                output_dir,
                result.resolution,
                result.context,
                result.attributes,
                result.plan.model_copy(update={"needs_review": True, "blocking_issues": result.plan.blocking_issues + [reason]}),
                asset=result.asset,
            )
            append_review_item(
                output_dir / "review_queue.json",
                build_review_item(
                    task_id=task.task_id,
                    source_file=task.file_name,
                    project_accession=result.resolution.primary_project.project_accession if result.resolution.primary_project else None,
                    stage="asset_preparation",
                    reasons=[reason],
                ),
            )
            raise
        bundle = materialize_dda_task_bundle(
            task=task,
            project_resolution=result.resolution,
            project_context=result.context,
            attributes=result.attributes,
            source_data_path=prepared_path,
            output_dir=output_dir,
            reviewed_fasta_path=active_reviewed_fasta_path,
            reviewed_fasta_url=active_reviewed_fasta_url,
            reviewed_fasta_name=active_reviewed_fasta_name,
            prefer_project_fasta=prefer_project_fasta,
            accept_search_parameter_review=search_parameters_reviewed,
            report=self.reporter,
        )
        self.write_task_bundle(
            output_dir,
            result.resolution,
            result.context,
            result.attributes,
            bundle.plan,
            asset=result.asset,
        )
        docker_runner = DockerMSDTConverterRunner(image="guomics2017/msdt-converter:v1.3", report=self.reporter)
        if hasattr(docker_runner, "write_container_config"):
            docker_runner.write_container_config(bundle)
        self._report(
            "MSDT-Converter 输入包已生成："
            f"workflow={bundle.materialized_workflow_path}；fasta={bundle.materialized_fasta_path}；"
            f"converter_config={bundle.converter_config_path}"
        )
        return bundle, result, prepared_path

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
