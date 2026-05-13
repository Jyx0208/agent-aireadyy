from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import requests


MSDT_CONVERTER_ZIP_URL = "https://codeload.github.com/guomics-lab/MSDT-Converter/zip/refs/heads/main"


def bootstrap_msdt_converter(destination: str | Path, url: str = MSDT_CONVERTER_ZIP_URL) -> Path:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return bootstrap_msdt_converter_from_zip(response.content, destination)


def bootstrap_msdt_converter_from_zip(zip_bytes: bytes, destination: str | Path) -> Path:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = [name for name in archive.namelist() if name.strip()]
        for name in names:
            member_path = Path(name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"MSDT-Converter 归档包含不安全路径：{name}")
        top_levels = {Path(name).parts[0] for name in names}
        if len(top_levels) != 1:
            raise ValueError("MSDT-Converter 归档文件结构异常。")
        extracted_root_name = next(iter(top_levels))
        temp_extract_dir = destination / extracted_root_name
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        archive.extractall(destination)

    final_root = destination / "MSDT-Converter"
    if final_root.exists():
        shutil.rmtree(final_root)
    try:
        temp_extract_dir.replace(final_root)
    except OSError:
        shutil.copytree(temp_extract_dir, final_root)
        shutil.rmtree(temp_extract_dir, ignore_errors=True)
    return final_root
