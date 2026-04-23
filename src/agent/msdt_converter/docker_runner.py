from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

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
        relative = path.resolve().relative_to(bundle.task_root.resolve())
        return f"/workspace/{relative.as_posix()}"

    def write_container_config(self, bundle: MaterializedTaskBundle) -> Path:
        config = {
            "generate_rawspectrum": {
                "need": True,
                "data_type": bundle.plan.raw_data_type,
                "data_path": self._container_path(bundle, bundle.plan.source_data_path),
                "output": self._container_path(bundle, bundle.plan.rawspectrum_output_path),
            },
            "generate_sage_search_result": {
                "need": False,
                "workdir": self._container_path(bundle, bundle.task_root / "sage"),
                "fasta": self._container_path(bundle, bundle.materialized_fasta_path),
                "data_path": self._container_path(bundle, bundle.plan.source_data_path),
                "config_path": self._container_path(bundle, bundle.task_root / "sage" / "sage_config.json"),
            },
            "generate_fragpipe_search_result": {
                "need": True,
                "workdir": self._container_path(bundle, bundle.plan.fragpipe_workdir),
                "data_path": self._container_path(bundle, bundle.plan.source_data_path),
                "fasta_path": self._container_path(bundle, bundle.materialized_fasta_path),
                "workflow_path": self._container_path(bundle, bundle.materialized_workflow_path),
                "manifest_path": self._container_path(bundle, bundle.plan.manifest_path),
                "thread_num": bundle.plan.thread_num,
            },
            "generate_msdt": {
                "need": True,
                "tims": {
                    "need_tims": bundle.plan.raw_data_type == "tims",
                    "rawspectrum_path": self._container_path(bundle, bundle.plan.rawspectrum_output_path) if bundle.plan.raw_data_type == "tims" else "",
                    "sage_search_result_path": "",
                    "unify_residue": True,
                    "output": self._container_path(bundle, bundle.plan.output_paths["fp_msdt"]) if bundle.plan.raw_data_type == "tims" else "",
                },
                "mzml": {
                    "need_mzml": bundle.plan.raw_data_type == "mzml",
                    "need_sage": False,
                    "need_fragpipe": True,
                    "rawspectrum_path": self._container_path(bundle, bundle.plan.rawspectrum_output_path) if bundle.plan.raw_data_type == "mzml" else "",
                    "sage_search_result_path": "",
                    "fp_pin_path": self._container_path(bundle, bundle.plan.expected_pin_path) if bundle.plan.raw_data_type == "mzml" else "",
                    "sage_unify_residue": True,
                    "fp_unify_residue": True,
                    "sage_output": "",
                    "fp_output": self._container_path(bundle, bundle.plan.output_paths["fp_msdt"]) if bundle.plan.raw_data_type == "mzml" else "",
                },
                "wiff": {
                    "need_wiff": False,
                    "wiff_mzml_path": "",
                    "rawspectrum_path": "",
                    "sage_search_result_path": "",
                    "unify_residue": True,
                    "output": "",
                },
            },
            "convert_2_msdt": {
                "mgf": {
                    "need": False,
                    "mgf_path": "",
                    "output_path": "",
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
        emit(self.report, f"Wrote Docker MSDT-Converter config: {bundle.converter_config_path}")
        return bundle.converter_config_path

    def build_command(self, bundle: MaterializedTaskBundle) -> list[str]:
        task_root = bundle.task_root.resolve()
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
        emit(self.report, f"Launching MSDT-Converter Docker image: {self.image}")
        return run_command_streaming(command, report=self.report)
