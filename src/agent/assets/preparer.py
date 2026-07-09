from __future__ import annotations

import subprocess
import gzip
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Callable

from agent.assets.downloader import download_file_asset, invalidate_file_asset_cache
from agent.docker_paths import docker_host_mount_path
from agent.models import FileAsset
from agent.utils import emit, run_command_streaming


class AssetPreparationError(RuntimeError):
    def __init__(self, message: str, local_path: Path | None = None) -> None:
        super().__init__(message)
        self.local_path = local_path


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
        emit(self.report, f"正在使用本地 msconvert 转换质谱文件：{source.name} -> {target.name}")
        run_command_streaming(command, report=self.report)
        if not target.exists():
            raise FileNotFoundError(f"msconvert 未生成预期的 mzML 文件：{target}")
        emit(self.report, f"转换完成：{target}")
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
        work_dir = docker_host_mount_path(source.parent)
        output_dir = docker_host_mount_path(target.parent)
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
        emit(self.report, f"正在使用 Docker ProteoWizard 转换质谱文件：{source.name} -> {target.name}")
        run_command_streaming(command, report=self.report)
        if not target.exists():
            raise FileNotFoundError(f"Docker ProteoWizard 未生成预期的 mzML 文件：{target}")
        emit(self.report, f"转换完成：{target}")
        return target


def _download_sidecars(client, asset: FileAsset, report: Callable[[str], None] | None = None) -> None:
    for sidecar in asset.sidecar_files:
        download_url = sidecar.get("download_url")
        local_path = sidecar.get("local_path")
        if not download_url or not local_path:
            continue
        target_path = Path(local_path)
        if target_path.exists() and target_path.is_file() and target_path.stat().st_size > 0:
            emit(report, f"复用已下载的 sidecar 文件：{target_path}")
            continue
        sidecar_asset = FileAsset(
            original_file_name=str(sidecar.get("file_name") or target_path.name),
            resolved_asset_type="unknown",
            project_accession=asset.project_accession,
            matched_project_file=str(sidecar.get("file_name") or target_path.name),
            download_url=str(download_url),
            local_path=target_path,
            prepared_path=target_path,
            requires_conversion=False,
            asset_confidence=1.0,
            match_type="sidecar",
        )
        emit(report, f"正在下载 sidecar 文件 {sidecar.get('file_name') or target_path.name} -> {target_path}")
        download_file_asset(client, sidecar_asset, report=report)


def _extract_archive(source: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    extract_root = target.parent / f"{target.name}.extracting"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True)

    lower_name = source.name.lower()
    if lower_name.endswith(".zip"):
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                destination = (extract_root / member.filename).resolve()
                destination.relative_to(extract_root.resolve())
            archive.extractall(extract_root)
    elif lower_name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(source) as archive:
            for member in archive.getmembers():
                destination = (extract_root / member.name).resolve()
                destination.relative_to(extract_root.resolve())
            archive.extractall(extract_root)
    else:
        raise ValueError(f"不支持的归档格式：{source}")

    candidates = [path for path in extract_root.rglob(target.name) if path.is_dir()]
    if candidates:
        shutil.move(str(candidates[0]), str(target))
    else:
        shutil.move(str(extract_root), str(target))
        extract_root = target

    if extract_root.exists() and extract_root != target:
        shutil.rmtree(extract_root)
    return target


def _extract_single_file(source: Path, target_dir: Path, suffixes: tuple[str, ...]) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    extract_root = target_dir / f"{source.name}.extracting"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True)

    lower_name = source.name.lower()
    if lower_name.endswith(".zip"):
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                destination = (extract_root / member.filename).resolve()
                destination.relative_to(extract_root.resolve())
            archive.extractall(extract_root)
    elif lower_name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(source) as archive:
            for member in archive.getmembers():
                destination = (extract_root / member.name).resolve()
                destination.relative_to(extract_root.resolve())
            archive.extractall(extract_root)
    else:
        raise ValueError(f"不支持的归档格式：{source}")

    candidates = [
        path
        for path in extract_root.rglob("*")
        if path.is_file() and path.name.lower().endswith(suffixes)
    ]
    if len(candidates) != 1:
        shutil.rmtree(extract_root)
        raise ValueError(f"归档文件中需要且只能包含 1 个可用文件，实际找到 {len(candidates)} 个：{source}")
    target_path = target_dir / candidates[0].name
    if target_path.exists():
        target_path.unlink()
    shutil.move(str(candidates[0]), str(target_path))
    shutil.rmtree(extract_root)
    return target_path


def _has_prepared_artifact(path: Path) -> bool:
    if path.is_file():
        return path.stat().st_size > 0
    if path.is_dir():
        return any(path.iterdir())
    return False


def _can_reuse_prepared_asset(asset: FileAsset, local_path: Path | None = None) -> bool:
    if asset.prepared_path is None or not _has_prepared_artifact(asset.prepared_path):
        return False
    if local_path is not None and local_path == asset.prepared_path:
        return False
    return asset.requires_conversion or asset.resolved_asset_type in {"mzml", "mgf", "mzid", "tims"}


def _looks_like_corrupt_vendor_file_error(exc: Exception) -> bool:
    text = _preparation_error_message(exc).lower()
    return "corrupt raw file" in text or "rawfileimpl::ctor" in text or "error processing file" in text


def _preparation_error_message(exc: Exception) -> str:
    output = getattr(exc, "output", None) or getattr(exc, "stdout", None)
    if output:
        return f"{exc}\n{output}"
    return str(exc)


def prepare_file_asset(
    client,
    asset: FileAsset,
    converter: RawToMzMLConverter,
    fallback_converter=None,
    report: Callable[[str], None] | None = None,
) -> Path:
    if _can_reuse_prepared_asset(asset):
        emit(report, f"复用已准备好的数据文件：{asset.prepared_path}")
        return asset.prepared_path  # type: ignore[return-value]

    local_path = download_file_asset(client, asset, report=report)
    if _can_reuse_prepared_asset(asset, local_path=local_path):
        emit(report, f"复用已准备好的数据文件：{asset.prepared_path}")
        return asset.prepared_path  # type: ignore[return-value]

    _download_sidecars(client, asset, report=report)
    if (
        asset.resolved_asset_type in {"mzml", "mgf", "mzid"}
        and asset.prepared_path is not None
        and local_path != asset.prepared_path
        and local_path.name.lower().endswith((".mzml.gz", ".mgf.gz", ".mzid.gz"))
    ):
        asset.prepared_path.parent.mkdir(parents=True, exist_ok=True)
        emit(report, f"正在解压 gzip 压缩文件：{local_path.name} -> {asset.prepared_path.name}")
        with gzip.open(local_path, "rb") as source, asset.prepared_path.open("wb") as target:
            shutil.copyfileobj(source, target)
        emit(report, f"解压完成：{asset.prepared_path}")
        return asset.prepared_path
    if (
        asset.resolved_asset_type == "tims"
        and asset.prepared_path is not None
        and local_path != asset.prepared_path
        and local_path.name.lower().endswith((".d.zip", ".d.tar.gz", ".d.tgz"))
    ):
        emit(report, f"正在解压 Bruker .d 归档：{local_path.name} -> {asset.prepared_path.name}")
        return _extract_archive(local_path, asset.prepared_path)
    if not asset.requires_conversion:
        emit(report, f"数据文件已可直接用于执行：{local_path}")
        return local_path
    if not asset.prepared_path:
        raise ValueError("可转换的文件资产必须定义 prepared_path。")
    conversion_source = local_path
    if local_path.name.lower().endswith(".raw.zip"):
        emit(report, f"正在解压 RAW zip 归档：{local_path.name}")
        conversion_source = _extract_single_file(local_path, local_path.parent, (".raw",))
    elif asset.resolved_asset_type == "mzxml" and local_path.name.lower().endswith(".mzxml.gz"):
        conversion_source = local_path.with_suffix("")
        if not conversion_source.exists() or conversion_source.stat().st_size == 0:
            emit(report, f"正在解压 mzXML gzip 文件：{local_path.name} -> {conversion_source.name}")
            with gzip.open(local_path, "rb") as source, conversion_source.open("wb") as target:
                shutil.copyfileobj(source, target)
    emit(report, f"数据文件需要格式转换：{conversion_source.name} -> {asset.prepared_path.name}")
    try:
        return converter.convert_to_mzml(conversion_source, asset.prepared_path)
    except Exception as exc:
        emit(report, f"主转换器失败：{exc}")
        if fallback_converter is None:
            raise AssetPreparationError(str(exc), local_path=local_path) from exc
        emit(report, "正在切换到备用转换器")
        try:
            return fallback_converter.convert_to_mzml(conversion_source, asset.prepared_path)
        except Exception as fallback_exc:
            if _looks_like_corrupt_vendor_file_error(fallback_exc):
                emit(report, "ProteoWizard 报告 RAW/vendor 文件损坏；删除缓存并重新下载后重试一次。")
                invalidate_file_asset_cache(asset, report=report)
                if asset.prepared_path.exists():
                    asset.prepared_path.unlink()
                redownloaded_path = download_file_asset(client, asset, report=report)
                retry_source = redownloaded_path
                if redownloaded_path.name.lower().endswith(".raw.zip"):
                    retry_source = _extract_single_file(redownloaded_path, redownloaded_path.parent, (".raw",))
                try:
                    return fallback_converter.convert_to_mzml(retry_source, asset.prepared_path)
                except Exception as retry_exc:
                    raise AssetPreparationError(
                        f"{retry_exc}；已尝试删除缓存并重新下载一次，仍转换失败。请检查 PRIDE 原始文件是否损坏，或尝试安装本地 ProteoWizard msconvert。",
                        local_path=redownloaded_path,
                    ) from retry_exc
            raise AssetPreparationError(_preparation_error_message(fallback_exc), local_path=local_path) from fallback_exc


def prepare_local_file_asset(
    asset: FileAsset,
    converter: RawToMzMLConverter,
    fallback_converter=None,
    report: Callable[[str], None] | None = None,
) -> Path:
    """Prepare an already-local source file without requiring a download URL."""
    if _can_reuse_prepared_asset(asset):
        emit(report, f"Reusing prepared local asset: {asset.prepared_path}")
        return asset.prepared_path  # type: ignore[return-value]
    if asset.local_path is None:
        raise AssetPreparationError("Local source asset requires local_path.")
    local_path = Path(asset.local_path)
    if not local_path.exists():
        raise AssetPreparationError(f"Local source file does not exist: {local_path}", local_path=local_path)
    if local_path.is_file() and local_path.stat().st_size == 0:
        raise AssetPreparationError(f"Local source file is empty: {local_path}", local_path=local_path)
    if _can_reuse_prepared_asset(asset, local_path=local_path):
        emit(report, f"Reusing prepared local asset: {asset.prepared_path}")
        return asset.prepared_path  # type: ignore[return-value]
    if (
        asset.resolved_asset_type in {"mzml", "mgf", "mzid"}
        and asset.prepared_path is not None
        and local_path != asset.prepared_path
        and local_path.name.lower().endswith((".mzml.gz", ".mgf.gz", ".mzid.gz"))
    ):
        asset.prepared_path.parent.mkdir(parents=True, exist_ok=True)
        emit(report, f"Decompressing local gzip asset: {local_path.name} -> {asset.prepared_path.name}")
        with gzip.open(local_path, "rb") as source, asset.prepared_path.open("wb") as target:
            shutil.copyfileobj(source, target)
        emit(report, f"Local gzip asset decompressed: {asset.prepared_path}")
        return asset.prepared_path
    if (
        asset.resolved_asset_type == "tims"
        and asset.prepared_path is not None
        and local_path != asset.prepared_path
        and local_path.name.lower().endswith((".d.zip", ".d.tar.gz", ".d.tgz"))
    ):
        emit(report, f"Extracting local Bruker .d archive: {local_path.name} -> {asset.prepared_path.name}")
        return _extract_archive(local_path, asset.prepared_path)
    if not asset.requires_conversion:
        emit(report, f"Using local data file directly: {local_path}")
        return local_path
    if not asset.prepared_path:
        raise ValueError("Convertible local source asset must define prepared_path.")
    conversion_source = local_path
    if local_path.name.lower().endswith(".raw.zip"):
        emit(report, f"Extracting local RAW zip archive: {local_path.name}")
        conversion_source = _extract_single_file(local_path, local_path.parent, (".raw",))
    elif asset.resolved_asset_type == "mzxml" and local_path.name.lower().endswith(".mzxml.gz"):
        conversion_source = local_path.with_suffix("")
        if not conversion_source.exists() or conversion_source.stat().st_size == 0:
            emit(report, f"Decompressing local mzXML gzip asset: {local_path.name} -> {conversion_source.name}")
            with gzip.open(local_path, "rb") as source, conversion_source.open("wb") as target:
                shutil.copyfileobj(source, target)
    emit(report, f"Local source requires format conversion: {conversion_source.name} -> {asset.prepared_path.name}")
    try:
        return converter.convert_to_mzml(conversion_source, asset.prepared_path)
    except Exception as exc:
        emit(report, f"Primary local converter failed: {_preparation_error_message(exc)}")
        if fallback_converter is None:
            raise AssetPreparationError(_preparation_error_message(exc), local_path=local_path) from exc
        emit(report, "Switching to fallback local converter.")
        try:
            return fallback_converter.convert_to_mzml(conversion_source, asset.prepared_path)
        except Exception as fallback_exc:
            raise AssetPreparationError(_preparation_error_message(fallback_exc), local_path=local_path) from fallback_exc
