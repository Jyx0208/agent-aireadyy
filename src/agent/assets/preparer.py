from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from agent.assets.downloader import download_file_asset
from agent.models import FileAsset
from agent.utils import emit, run_command_streaming


class RawToMzMLConverter:
    def __init__(self, executable: str = "msconvert", report: Callable[[str], None] | None = None):
        self.executable = executable
        self.report = report

    def convert_to_mzml(self, source: Path, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        source = source.resolve()
        target = target.resolve()

        command = [
            self.executable,
            str(source),
            "--mzML",
            "--outfile",
            target.name,
            "-o",
            str(target.parent),
        ]
        emit(self.report, f"Converting RAW asset with local msconvert: {source.name} -> {target.name}")
        run_command_streaming(command, report=self.report)
        if not target.exists():
            raise FileNotFoundError(f"msconvert did not produce the expected mzML file: {target}")
        emit(self.report, f"Conversion complete: {target}")
        return target


class DockerPwizConverter:
    def __init__(
        self,
        image: str = "chambm/pwiz-skyline-i-agree-to-the-vendor-licenses",
        report: Callable[[str], None] | None = None,
    ):
        self.image = image
        self.report = report

    def build_command(self, source: Path, target: Path) -> list[str]:
        work_dir = source.parent.resolve()
        output_dir = target.parent.resolve()
        return [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{work_dir}:/data",
            "-v",
            f"{output_dir}:/out",
            self.image,
            "wine",
            "msconvert",
            f"/data/{source.name}",
            "--mzML",
            "--filter",
            "peakPicking true 1-",
            "--outfile",
            target.name,
            "-o",
            "/out",
        ]

    def convert_to_mzml(self, source: Path, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        command = self.build_command(source, target)
        emit(self.report, f"Converting RAW asset with Docker ProteoWizard: {source.name} -> {target.name}")
        run_command_streaming(command, report=self.report)
        if not target.exists():
            raise FileNotFoundError(f"Docker ProteoWizard did not produce the expected mzML file: {target}")
        emit(self.report, f"Conversion complete: {target}")
        return target


def prepare_file_asset(
    client,
    asset: FileAsset,
    converter: RawToMzMLConverter,
    fallback_converter=None,
    report: Callable[[str], None] | None = None,
) -> Path:
    local_path = download_file_asset(client, asset, report=report)
    if not asset.requires_conversion:
        emit(report, f"Asset is already execution-ready: {local_path}")
        return local_path
    if not asset.prepared_path:
        raise ValueError("A convertible file asset must define a prepared_path.")
    emit(report, f"Preparing asset requires conversion: {local_path.name} -> {asset.prepared_path.name}")
    try:
        return converter.convert_to_mzml(local_path, asset.prepared_path)
    except Exception as exc:
        emit(report, f"Primary conversion failed: {exc}")
        if fallback_converter is None:
            raise
        emit(report, "Falling back to secondary converter")
        return fallback_converter.convert_to_mzml(local_path, asset.prepared_path)
