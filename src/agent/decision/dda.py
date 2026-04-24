from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from agent.models import AttributeSet, DdaExecutionPlan, ProjectContext, ProjectResolution
from agent.pride.client import PrideClient


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _detect_raw_type(source_data_path: Path, source_file_name: str = "") -> str:
    source_lower = source_file_name.lower()
    suffix = source_data_path.suffix.lower()
    if source_lower.endswith((".wiff", ".wiff2")) and suffix == ".mzml":
        return "wiff2mzml"
    if suffix == ".mzml":
        return "mzml"
    if suffix == ".d":
        return "tims"
    raise ValueError(f"Unsupported source data path for MSDT-Converter: {source_data_path}")


def _species_fasta_name(species: str) -> tuple[str, str]:
    normalized = species.lower()
    multi_species_groups = [
        ("homo sapiens", "human"),
        ("mus musculus", "mouse"),
        ("escherichia coli", "e. coli"),
        ("saccharomyces cerevisiae", "yeast"),
    ]
    if sum(1 for aliases in multi_species_groups if any(alias in normalized for alias in aliases)) > 1:
        return "generic_reference_with_contaminants.fasta", "defaulted"
    if "homo sapiens" in normalized or "human" in normalized:
        return "Homo_sapiens_reviewed.fasta", "inferred"
    if "mus musculus" in normalized or "mouse" in normalized:
        return "Mus_musculus_reviewed.fasta", "inferred"
    return "generic_reference_with_contaminants.fasta", "defaulted"


def _safe_file_name(file_name: str) -> str:
    posix_name = PurePosixPath(file_name).name
    windows_name = PureWindowsPath(posix_name).name
    if windows_name in {"", ".", ".."}:
        raise ValueError(f"Invalid file name: {file_name}")
    return windows_name


def _project_fasta_records(project_context: ProjectContext | None) -> list[dict]:
    if project_context is None:
        return []
    records = []
    for file_record in project_context.project_files:
        file_name = str(file_record.get("fileName", ""))
        if file_name.lower().endswith((".fasta", ".fa", ".faa", ".fasta.gz", ".fa.gz", ".faa.gz")):
            records.append(file_record)
    return records


def _project_fasta_choice(
    project_context: ProjectContext | None,
    output_dir: Path,
) -> tuple[Path | None, str | None, list[str]]:
    fasta_records = _project_fasta_records(project_context)
    if not fasta_records:
        return None, None, []
    if len(fasta_records) > 1:
        return None, None, ["发现多个项目 FASTA 文件，需要人工选择。"]

    fasta_record = fasta_records[0]
    file_name = _safe_file_name(str(fasta_record.get("fileName", "")))
    if file_name.lower().endswith(".gz"):
        file_name = Path(file_name).stem
    download_url = PrideClient.first_download_url(fasta_record)
    if not download_url:
        return None, None, [f"项目 FASTA 文件 {file_name} 没有可下载地址。"]
    return output_dir / "fasta" / file_name, download_url, []


def _acquisition_text(attributes: AttributeSet) -> str:
    return str(attributes.acquisition_mode.value or "").strip().lower()


def _is_dia_mode(attributes: AttributeSet) -> bool:
    mode = _acquisition_text(attributes)
    return "dia" in mode or "swath" in mode or "data-independent" in mode or "data independent" in mode


def _is_dda_mode(attributes: AttributeSet) -> bool:
    mode = _acquisition_text(attributes)
    if _is_dia_mode(attributes):
        return False
    return "dda" in mode or "pasef" in mode or "data-dependent" in mode or "data dependent" in mode


def _workflow_name(attributes: AttributeSet, raw_data_type: str) -> str:
    label = str(attributes.labeling_strategy.value).lower()
    species = str(attributes.species.value).lower()
    multi_species_groups = [
        ("homo sapiens", "human"),
        ("mus musculus", "mouse"),
        ("escherichia coli", "e. coli"),
        ("saccharomyces cerevisiae", "yeast"),
    ]
    is_multi_species = sum(1 for aliases in multi_species_groups if any(alias in species for alias in aliases)) > 1
    if not _is_dda_mode(attributes):
        return ""
    if not is_multi_species and ("human" in species or "homo sapiens" in species):
        if "tmt" in label:
            return "TMT_DDA_human.workflow"
        if "itraq" in label:
            return "iTRAQ_DDA_human.workflow"
        if raw_data_type == "tims":
            return "LFQ_DDA_human_noNQ_tims.workflow"
        return "LFQ_DDA_human_noNQ.workflow"
    if "tmt" in label:
        return "TMT_DDA_generic.workflow"
    if "itraq" in label:
        return "iTRAQ_DDA_generic.workflow"
    if raw_data_type == "tims":
        return "LFQ_DDA_generic_tims.workflow"
    return "LFQ_DDA_generic.workflow"


def _blocking_issues(attributes: AttributeSet, workflow_name: str) -> list[str]:
    issues: list[str] = []
    if _is_dia_mode(attributes):
        issues.append("当前版本暂不支持 DIA/SWATH 数据自动生成严格 MSDT-Converter 搜库输入。")
    elif not _is_dda_mode(attributes):
        issues.append(f"无法确认采集模式是否为 DDA：{attributes.acquisition_mode.value}")
    required = {
        "物种": attributes.species,
        "仪器类型": attributes.instrument_family,
        "酶切酶": attributes.enzyme,
    }
    for name, value in required.items():
        if value.value in (None, "", "unknown"):
            issues.append(f"缺少必需属性：{name}")
        if value.conflict_flag:
            issues.append(f"必需属性存在冲突：{name}")
    if not workflow_name:
        issues.append("没有找到与当前推断属性匹配且已验证的 FragPipe workflow 模板。")
    return issues


def _metadata_values(project_context: ProjectContext | None, key: str) -> list[str]:
    if project_context is None:
        return []
    metadata = project_context.metadata.get(key)
    if metadata is None or not metadata.value:
        return []
    if isinstance(metadata.value, list):
        return [str(value) for value in metadata.value if str(value).strip()]
    return [str(metadata.value)]


def _context_blocking_issues(project_context: ProjectContext | None) -> list[str]:
    issues: list[str] = []
    experiment_types = " | ".join(_metadata_values(project_context, "experimentTypes")).lower()
    if "top-down" in experiment_types or "top down" in experiment_types:
        issues.append("当前 bottom-up MSDT 搜库流程不支持 Top-down proteomics 项目。")
    if project_context is not None and not project_context.sdrf_rows:
        organisms = set(_metadata_values(project_context, "organisms"))
        instruments = set(_metadata_values(project_context, "instruments"))
        if len(organisms) > 1:
            issues.append("未找到匹配 SDRF 行，且项目级 metadata 包含多个物种；文件级物种不明确。")
        if len(instruments) > 1:
            issues.append("未找到匹配 SDRF 行，且项目级 metadata 包含多个仪器；文件级仪器不明确。")
    return issues


def plan_dda_execution(
    task_id: str,
    source_file_name: str,
    source_data_path: str | Path,
    project_resolution: ProjectResolution,
    attributes: AttributeSet,
    output_dir: str | Path,
    project_context: ProjectContext | None = None,
) -> DdaExecutionPlan:
    source_data_path = Path(source_data_path)
    output_dir = Path(output_dir)
    raw_data_type = _detect_raw_type(source_data_path, source_file_name)
    workspace_root = _workspace_root()
    project_fasta_path, project_fasta_url, fasta_issues = _project_fasta_choice(project_context, output_dir)
    if project_fasta_path is not None:
        fasta_path = project_fasta_path
        fasta_mode = "reproduced"
    else:
        fasta_file_name, fasta_mode = _species_fasta_name(str(attributes.species.value))
        fasta_path = workspace_root / "profiles" / "fasta" / fasta_file_name
    workflow_name = _workflow_name(attributes, raw_data_type)
    blocking_issues = _blocking_issues(attributes, workflow_name)
    blocking_issues.extend(fasta_issues)
    blocking_issues.extend(_context_blocking_issues(project_context))

    rawspectrum_dir = output_dir / "rawspectrum"
    fragpipe_dir = output_dir / "fragpipe"
    msdt_dir = output_dir / "msdt"
    logs_dir = output_dir / "logs"

    rawspectrum_output_path = rawspectrum_dir / f"{source_data_path.stem}_rawspectrum.parquet"
    manifest_path = fragpipe_dir / "fragpipe-files.fp-manifest"
    expected_pin_path = fragpipe_dir / "exp" / f"{source_data_path.stem}_edited.pin"
    converter_config_path = output_dir / "converter_config.json"
    msdt_output_name = f"{source_data_path.stem}_sage_msdt.parquet" if raw_data_type in {"tims", "wiff2mzml"} else f"{source_data_path.stem}_fp_msdt.parquet"
    output_paths = {
        "fp_msdt": msdt_dir / msdt_output_name,
        "ai_ready": output_dir / "ai_ready" / f"{source_data_path.stem}_ai_ready.parquet",
        "run_log": logs_dir / "run.log",
    }

    return DdaExecutionPlan(
        task_id=task_id,
        source_file_name=source_file_name,
        source_data_path=source_data_path,
        raw_data_type=raw_data_type,  # type: ignore[arg-type]
        fasta_path=fasta_path,
        fasta_selection_mode=fasta_mode,  # type: ignore[arg-type]
        fasta_download_url=project_fasta_url,
        fragpipe_workflow_path=workspace_root / "profiles" / "fragpipe" / workflow_name,
        manifest_path=manifest_path,
        converter_config_path=converter_config_path,
        rawspectrum_output_path=rawspectrum_output_path,
        fragpipe_workdir=fragpipe_dir,
        expected_pin_path=expected_pin_path,
        expected_pin_glob=str(expected_pin_path),
        output_paths=output_paths,
        needs_review=bool(blocking_issues),
        blocking_issues=blocking_issues,
    )
