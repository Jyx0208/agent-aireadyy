from __future__ import annotations

import shutil
from pathlib import Path

from agent.execution.fragpipe import FragPipeRunner
from agent.execution.workflow import materialize_workflow_with_attributes
from agent.models import AttributeSet, DdaExecutionPlan, InputTask, MaterializedTaskBundle, ProjectContext, ProjectResolution
from agent.msdt_converter.runner import MSDTConverterRunner


def materialize_dda_task_bundle(
    task: InputTask,
    project_resolution: ProjectResolution,
    project_context: ProjectContext,
    attributes: AttributeSet,
    source_data_path: str | Path,
    output_dir: str | Path,
) -> MaterializedTaskBundle:
    from agent.decision.dda import plan_dda_execution

    plan = plan_dda_execution(
        task_id=task.task_id,
        source_file_name=task.file_name,
        source_data_path=source_data_path,
        project_resolution=project_resolution,
        attributes=attributes,
        output_dir=output_dir,
    )
    if plan.needs_review:
        raise ValueError(f"Cannot materialize a strict DDA bundle while the plan needs review: {plan.blocking_issues}")

    fragpipe = FragPipeRunner(fragpipe_root=Path("."))  # path is irrelevant for manifest/workflow materialization
    fragpipe.materialize_manifest(plan)
    task_root = Path(output_dir)
    workflows_dir = task_root / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflows_dir / plan.fragpipe_workflow_path.name
    materialize_workflow_with_attributes(plan.fragpipe_workflow_path, workflow_path, attributes)
    fasta_dir = task_root / "fasta"
    fasta_dir.mkdir(parents=True, exist_ok=True)
    materialized_fasta_path = fasta_dir / plan.fasta_path.name
    shutil.copyfile(plan.fasta_path, materialized_fasta_path)

    converter = MSDTConverterRunner(converter_root=Path("."))
    config_path = converter.write_config(plan)

    return MaterializedTaskBundle(
        plan=plan,
        converter_config_path=config_path,
        materialized_workflow_path=workflow_path,
        materialized_fasta_path=materialized_fasta_path,
        task_root=task_root,
    )
