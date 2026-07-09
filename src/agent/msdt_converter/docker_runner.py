from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable

from agent.docker_paths import docker_host_mount_path
from agent.models import MaterializedTaskBundle
from agent.utils import emit, run_command_streaming


class DockerMSDTConverterRunner:
    def __init__(
        self,
        image: str = "guomics2017/msdt-converter:v1.3",
        report: Callable[[str], None] | None = None,
    ):
        self.image = image
        self.report = report

    @staticmethod
    def _path_posix(path: Path) -> str:
        return path.resolve().as_posix()

    @staticmethod
    def _volumes_from_container() -> str | None:
        raw = os.getenv("AGENT_DOCKER_VOLUMES_FROM", "").strip()
        return raw or None

    def _container_path(self, bundle: MaterializedTaskBundle, path: Path) -> str:
        if self._volumes_from_container():
            return self._path_posix(path)
        try:
            resolved_path = path.resolve()
            resolved_root = bundle.task_root.resolve()
            relative = resolved_path.relative_to(resolved_root)
            return f"/workspace/{relative.as_posix()}"
        except ValueError:
            # 如果路径不在 task_root 下，尝试使用绝对路径的 POSIX 格式
            # 这在 Windows 上可能会产生不正确的路径，但至少不会崩溃
            return f"/workspace/{path.as_posix()}"

    def _sage_workdir(self, bundle: MaterializedTaskBundle) -> Path:
        return bundle.task_root / "sage"

    def _sage_search_result_path(self, bundle: MaterializedTaskBundle) -> Path:
        return self._sage_workdir(bundle) / f"{bundle.plan.source_data_path.stem}_search_result.tsv"

    @staticmethod
    def _fragpipe_java_options() -> str | None:
        raw = os.getenv("AGENT_FRAGPIPE_RAM_GB", "").strip()
        if not raw:
            return None
        try:
            ram_gb = int(raw)
        except ValueError:
            return None
        if ram_gb <= 0:
            return None
        return f"-Xmx{ram_gb}G"

    @staticmethod
    def _float_env(name: str) -> float | None:
        raw = os.getenv(name, "").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if value > 0 else None

    @staticmethod
    def _bool_env(name: str, default: bool = True) -> bool:
        raw = os.getenv(name, "").strip().casefold()
        if not raw:
            return default
        return raw not in {"0", "false", "no", "off"}

    @staticmethod
    def _cidfile_path(bundle: MaterializedTaskBundle) -> Path:
        return bundle.task_root / ".msdt_converter.cid"

    @staticmethod
    def _task_label(bundle: MaterializedTaskBundle) -> str:
        plan = getattr(bundle, "plan", None)
        raw = getattr(plan, "task_id", None) or Path(bundle.task_root).name
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw))[:120] or "unknown"

    def write_container_config(self, bundle: MaterializedTaskBundle) -> Path:
        is_mgf = bundle.plan.raw_data_type == "mgf"
        is_mzml = bundle.plan.raw_data_type == "mzml"
        config = {
            "generate_rawspectrum": {
                "need": not is_mgf,
                "data_type": bundle.plan.raw_data_type,
                "data_path": self._container_path(bundle, bundle.plan.source_data_path),
                "output": self._container_path(bundle, bundle.plan.rawspectrum_output_path),
            },
            "generate_sage_search_result": {
                "need": bundle.plan.raw_data_type in {"tims", "wiff2mzml"},
                "workdir": self._container_path(bundle, self._sage_workdir(bundle)),
                "fasta": self._container_path(bundle, bundle.materialized_fasta_path),
                "data_path": self._container_path(bundle, bundle.plan.source_data_path),
                "config_path": self._container_path(bundle, self._sage_workdir(bundle) / "sage_config.json"),
            },
            "generate_fragpipe_search_result": {
                "need": is_mzml,
                "workdir": self._container_path(bundle, bundle.plan.fragpipe_workdir),
                "data_path": self._container_path(bundle, bundle.plan.source_data_path),
                "fasta_path": self._container_path(bundle, bundle.materialized_fasta_path),
                "workflow_path": self._container_path(bundle, bundle.materialized_workflow_path),
                "manifest_path": self._container_path(bundle, bundle.plan.manifest_path),
                "thread_num": bundle.plan.thread_num,
            },
            "generate_msdt": {
                "need": not is_mgf,
                "tims": {
                    "need_tims": bundle.plan.raw_data_type == "tims",
                    "rawspectrum_path": self._container_path(bundle, bundle.plan.rawspectrum_output_path) if bundle.plan.raw_data_type == "tims" else "",
                    "sage_search_result_path": self._container_path(bundle, self._sage_search_result_path(bundle)) if bundle.plan.raw_data_type == "tims" else "",
                    "unify_residue": True,
                    "output": self._container_path(bundle, bundle.plan.output_paths["fp_msdt"]) if bundle.plan.raw_data_type == "tims" else "",
                },
                "mzml": {
                    "need_mzml": bundle.plan.raw_data_type == "mzml",
                    "need_sage": False,
                    "need_fragpipe": bundle.plan.raw_data_type == "mzml",
                    "rawspectrum_path": self._container_path(bundle, bundle.plan.rawspectrum_output_path) if bundle.plan.raw_data_type == "mzml" else "",
                    "sage_search_result_path": "",
                    "fp_pin_path": self._container_path(bundle, bundle.plan.expected_pin_path) if bundle.plan.raw_data_type == "mzml" else "",
                    "sage_unify_residue": True,
                    "fp_unify_residue": True,
                    "sage_output": "",
                    "fp_output": self._container_path(bundle, bundle.plan.output_paths["fp_msdt"]) if bundle.plan.raw_data_type == "mzml" else "",
                },
                "wiff": {
                    "need_wiff": bundle.plan.raw_data_type == "wiff2mzml",
                    "wiff_mzml_path": self._container_path(bundle, bundle.plan.source_data_path) if bundle.plan.raw_data_type == "wiff2mzml" else "",
                    "rawspectrum_path": self._container_path(bundle, bundle.plan.rawspectrum_output_path) if bundle.plan.raw_data_type == "wiff2mzml" else "",
                    "sage_search_result_path": self._container_path(bundle, self._sage_search_result_path(bundle)) if bundle.plan.raw_data_type == "wiff2mzml" else "",
                    "unify_residue": True,
                    "output": self._container_path(bundle, bundle.plan.output_paths["fp_msdt"]) if bundle.plan.raw_data_type == "wiff2mzml" else "",
                },
            },
            "convert_2_msdt": {
                "mgf": {
                    "need": is_mgf,
                    "mgf_path": self._container_path(bundle, bundle.plan.source_data_path) if is_mgf else "",
                    "output_path": self._container_path(bundle, bundle.plan.output_paths["fp_msdt"]) if is_mgf else "",
                    "field_type_dict": {
                        "TITLE": "string",
                        "PEPMASS": "float",
                        "CHARGE": "int",
                        "RTINSECONDS": "float",
                        "INSTRUMENT": "string",
                    },
                }
            },
            "msdt_2_mgf": {
                "need": False,
                "msdt_path": "",
                "output_path": "",
            },
        }
        bundle.converter_config_path.parent.mkdir(parents=True, exist_ok=True)
        bundle.converter_config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        emit(self.report, f"已写入 Docker MSDT-Converter 配置：{bundle.converter_config_path}")
        return bundle.converter_config_path

    def build_command(self, bundle: MaterializedTaskBundle) -> list[str]:
        container_config_path = self._container_path(bundle, bundle.converter_config_path)
        command = ["docker", "run", "--rm"]
        command.extend(["--cidfile", str(self._cidfile_path(bundle))])
        command.extend(["--label", f"agent.pride.task={self._task_label(bundle)}"])
        volumes_from = self._volumes_from_container()
        if volumes_from:
            command.extend(["--volumes-from", volumes_from])
        else:
            task_root = docker_host_mount_path(bundle.task_root)
            command.extend(["-v", f"{task_root}:/workspace"])
        command.extend(["-e", f"TZ={os.getenv('AGENT_MSDT_DOCKER_TZ') or os.getenv('TZ') or 'Asia/Shanghai'}"])
        java_options = self._fragpipe_java_options()
        if java_options:
            command.extend(["-e", f"_JAVA_OPTIONS={java_options}"])
        if os.name != "nt" and Path("/etc/localtime").exists():
            command.extend(["-v", "/etc/localtime:/etc/localtime:ro"])
        command.extend(
            [
                self.image,
                "-config",
                container_config_path,
            ]
        )
        return command

    def run(self, bundle: MaterializedTaskBundle) -> subprocess.CompletedProcess[str]:
        self.write_container_config(bundle)
        self._remove_stale_cidfile(bundle)
        command = self.build_command(bundle)
        emit(self.report, f"正在启动 MSDT-Converter Docker 镜像：{self.image}")
        return run_command_streaming(
            command,
            report=self.report,
            timeout_seconds=self._float_env("AGENT_MSDT_DOCKER_TIMEOUT_SECONDS"),
            idle_timeout_seconds=self._float_env("AGENT_MSDT_DOCKER_IDLE_TIMEOUT_SECONDS"),
            abort_predicate=self._abort_reason_from_output,
            on_abort=lambda reason: self._stop_child_container(bundle, reason),
        )

    def _abort_reason_from_output(self, line: str, _lines: list[str]) -> str | None:
        abort_mode = self._low_psm_abort_mode()
        if abort_mode == "off":
            return None
        if re.search(r"\bRT regression using 0 PSMs\b", line, flags=re.IGNORECASE):
            if abort_mode == "strict":
                return "low_psm_msbooster"
            if self._has_zero_search_psm_evidence(_lines):
                return "zero_psm_msbooster"
            return "low_psm_msbooster"
        return None

    @staticmethod
    def _low_psm_abort_mode() -> str:
        raw = os.getenv("AGENT_MSDT_ABORT_ON_LOW_PSM", "").strip().casefold()
        if raw in {"0", "false", "no", "off", "disabled"}:
            return "off"
        if raw in {"strict", "immediate"}:
            return "strict"
        return "evidence"

    @staticmethod
    def _has_zero_search_psm_evidence(lines: list[str]) -> bool:
        for text in lines:
            if re.search(r"\b0\s+unique peptides from\s+0\s+PSMs\b", text, flags=re.IGNORECASE):
                return True
        return False

    def _remove_stale_cidfile(self, bundle: MaterializedTaskBundle) -> None:
        cidfile = self._cidfile_path(bundle)
        if cidfile.exists():
            try:
                cidfile.unlink()
            except OSError:
                pass

    def _stop_child_container(self, bundle: MaterializedTaskBundle, reason: str) -> None:
        cidfile = self._cidfile_path(bundle)
        if not cidfile.exists():
            return
        container_id = cidfile.read_text(encoding="utf-8", errors="ignore").strip()
        if not container_id:
            return
        emit(self.report, f"agent_watchdog_stopping_msdt_container:{reason}:{container_id[:12]}")
        subprocess.run(["docker", "stop", container_id], capture_output=True, text=True, timeout=20, check=False)
