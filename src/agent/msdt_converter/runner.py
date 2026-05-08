from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from agent.models import DdaExecutionPlan
from agent.msdt_converter.config import build_converter_config


class MSDTConverterRunner:
    def __init__(self, converter_root: str | Path):
        self.converter_root = Path(converter_root)

    def write_config(self, plan: DdaExecutionPlan) -> Path:
        plan.converter_config_path.parent.mkdir(parents=True, exist_ok=True)
        config = build_converter_config(plan)
        plan.converter_config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return plan.converter_config_path

    def run(self, plan: DdaExecutionPlan) -> subprocess.CompletedProcess[str]:
        config_path = self.write_config(plan)
        convert_script = self.converter_root / "convert.py"
        if not convert_script.exists():
            raise FileNotFoundError(f"未找到 MSDT-Converter 的 convert.py 脚本：{convert_script}")
        cmd = [sys.executable, str(convert_script), "-config", str(config_path)]
        return subprocess.run(
            cmd,
            cwd=self.converter_root,
            check=True,
            text=True,
            capture_output=True,
        )
