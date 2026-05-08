from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path
from typing import Callable, Any

from agent.models import AttributeSet, DdaExecutionPlan, InputTask, MaterializedTaskBundle, ProjectContext, ProjectResolution
from agent.execution.workflow import materialize_workflow_with_attributes
from agent.decision.dda import is_placeholder_fasta
from agent.msdt_converter.runner import MSDTConverterRunner
from agent.msdt_converter.sage_config import build_sage_config
from agent.pride.client import PrideClient


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _materialize_source_data(source_data_path: str | Path, task_root: Path) -> Path:
    source_path = Path(source_data_path)
    if _path_within(source_path, task_root) or not source_path.exists():
        return source_path

    input_dir = task_root / "input"
    target_path = input_dir / source_path.name
    input_dir.mkdir(parents=True, exist_ok=True)
    if source_path.is_dir():
        if target_path.exists():
            shutil.rmtree(target_path)
        shutil.copytree(source_path, target_path)
    else:
        shutil.copyfile(source_path, target_path)
    return target_path


def _download_fasta(client: PrideClient, url: str, target_path: Path, report: Callable[[Any], None] | None = None) -> Path:
    if target_path.exists() and target_path.stat().st_size == 0:
        target_path.unlink()
    if url.lower().endswith(".gz") and not target_path.name.lower().endswith(".gz"):
        compressed_path = target_path.with_name(f"{target_path.name}.gz")
        if compressed_path.exists() and compressed_path.stat().st_size == 0:
            compressed_path.unlink()
        client.download_to_path(url, compressed_path, report=report)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(compressed_path, "rb") as source, target_path.open("wb") as target:
            shutil.copyfileobj(source, target)
        return target_path
    return client.download_to_path(url, target_path, report=report)


def _write_sage_config(plan: DdaExecutionPlan, attributes: AttributeSet) -> Path:
    sage_config_path = plan.fragpipe_workdir.parent / "sage" / "sage_config.json"
    sage_config_path.parent.mkdir(parents=True, exist_ok=True)
    config = build_sage_config(plan, attributes)
    sage_config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return sage_config_path


def materialize_dda_task_bundle(
    task: InputTask,
    project_resolution: ProjectResolution,
    project_context: ProjectContext,
    attributes: AttributeSet,
    source_data_path: str | Path,
    output_dir: str | Path,
    reviewed_fasta_path: str | Path | None = None,
    reviewed_fasta_url: str | None = None,
    reviewed_fasta_name: str | None = None,
    accept_search_parameter_review: bool = False,
    report: Callable[[Any], None] | None = None,
) -> MaterializedTaskBundle:
    from agent.decision.dda import plan_dda_execution

    task_root = Path(output_dir)
    materialized_source_data_path = _materialize_source_data(source_data_path, task_root)
    plan = plan_dda_execution(
        task_id=task.task_id,
        source_file_name=task.file_name,
        source_data_path=materialized_source_data_path,
        project_resolution=project_resolution,
        attributes=attributes,
        output_dir=output_dir,
        project_context=project_context,
        reviewed_fasta_path=reviewed_fasta_path,
        reviewed_fasta_url=reviewed_fasta_url,
        reviewed_fasta_name=reviewed_fasta_name,
        accept_search_parameter_review=accept_search_parameter_review,
    )
    if plan.needs_review:
        raise ValueError(f"无法生成严格的 DDA 任务包：计划需要人工复核。原因：{plan.blocking_issues}")
    if is_placeholder_fasta(plan.fasta_path) and not plan.fasta_download_url:
        raise ValueError(f"无法生成严格的 DDA 任务包：FASTA 文件是占位文件。{plan.fasta_path}")

    workflows_dir = task_root / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflows_dir / plan.fragpipe_workflow_path.name
    materialize_workflow_with_attributes(plan.fragpipe_workflow_path, workflow_path, attributes)
    fasta_dir = task_root / "fasta"
    fasta_dir.mkdir(parents=True, exist_ok=True)
    materialized_fasta_path = fasta_dir / plan.fasta_path.name
    if plan.fasta_download_url:
        client = PrideClient()
        try:
            _download_fasta(client, plan.fasta_download_url, materialized_fasta_path, report=report)
        finally:
            client.close()
    else:
        shutil.copyfile(plan.fasta_path, materialized_fasta_path)
    if is_placeholder_fasta(materialized_fasta_path):
        raise ValueError(f"下载/生成的 FASTA 文件是占位文件，不能用于搜库：{materialized_fasta_path}")

    _write_sage_config(plan, attributes)

    converter = MSDTConverterRunner(converter_root=Path("."))
    config_path = converter.write_config(plan)

    return MaterializedTaskBundle(
        plan=plan,
        converter_config_path=config_path,
        materialized_workflow_path=workflow_path,
        materialized_fasta_path=materialized_fasta_path,
        task_root=task_root,
    )
