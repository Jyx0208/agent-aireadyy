from __future__ import annotations

import json
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
    def _container_path(bundle: MaterializedTaskBundle, path: Path) -> str:
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
        task_root = docker_host_mount_path(bundle.task_root)
        container_config_path = f"/workspace/{bundle.converter_config_path.name}"
        return [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{task_root}:/workspace",
            self.image,
            "-config",
            container_config_path,
        ]

    def run(self, bundle: MaterializedTaskBundle) -> subprocess.CompletedProcess[str]:
        self.write_container_config(bundle)
        command = self.build_command(bundle)
        emit(self.report, f"正在启动 MSDT-Converter Docker 镜像：{self.image}")
        return run_command_streaming(command, report=self.report)
