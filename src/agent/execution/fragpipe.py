from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agent.execution.workflow import materialize_workflow_with_attributes
from agent.models import AttributeSet, DdaExecutionPlan


class FragPipeRunner:
    def __init__(self, fragpipe_root: str | Path, java_home: str | Path | None = None):
        self.fragpipe_root = Path(fragpipe_root)
        self.java_home = Path(java_home) if java_home else None

    @property
    def executable(self) -> Path:
        return self.fragpipe_root / "bin" / "fragpipe"

    def materialize_manifest(self, plan: DdaExecutionPlan) -> Path:
        plan.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        plan.manifest_path.write_text(f"{plan.source_data_path}\texp\t\tDDA", encoding="utf-8")
        return plan.manifest_path

    def materialize_workflow_copy(self, plan: DdaExecutionPlan, attributes: AttributeSet | None = None) -> Path:
        plan.fragpipe_workdir.mkdir(parents=True, exist_ok=True)
        destination = plan.fragpipe_workdir / plan.fragpipe_workflow_path.name
        if attributes is None:
            shutil.copyfile(plan.fragpipe_workflow_path, destination)
        else:
            materialize_workflow_with_attributes(plan.fragpipe_workflow_path, destination, attributes)
        return destination

    def run(self, plan: DdaExecutionPlan, attributes: AttributeSet | None = None) -> subprocess.CompletedProcess[str]:
        if not self.executable.exists():
            raise FileNotFoundError(f"FragPipe executable not found: {self.executable}")
        manifest_path = self.materialize_manifest(plan)
        workflow_copy = self.materialize_workflow_copy(plan, attributes=attributes)
        env = None
        if self.java_home:
            env = {"JAVA_HOME": str(self.java_home)}
        cmd = [
            str(self.executable),
            "--headless",
            "--workflow",
            str(workflow_copy),
            "--manifest",
            str(manifest_path),
            "--workdir",
            str(plan.fragpipe_workdir),
            "--threads",
            str(plan.thread_num),
        ]
        return subprocess.run(cmd, check=True, text=True, capture_output=True, env=env)
