from __future__ import annotations

import re
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from agent.models import AttributeSet, AttributeValue, DdaExecutionPlan, ProjectContext, ProjectResolution
from agent.pride.client import PrideClient


def _workspace_root() -> Path:
    module_path = Path(__file__)
    candidates = [
        module_path.parents[3],  # source checkout: <repo>/src/agent/decision/dda.py
        module_path.parents[2],  # installed wheel: <site-packages>/agent/decision/dda.py
        Path.cwd(),
    ]
    for candidate in candidates:
        if (candidate / "profiles" / "fragpipe").is_dir():
            return candidate
    return candidates[0]


def _detect_raw_type(source_data_path: Path, source_file_name: str = "") -> str:
    source_lower = source_file_name.lower()
    suffix = source_data_path.suffix.lower()
    if suffix == ".mgf":
        return "mgf"
    if suffix == ".mzid":
        return "mzid"
    if source_lower.endswith((".wiff", ".wiff2")) and suffix == ".mzml":
        return "wiff2mzml"
    if suffix == ".mzml":
        return "mzml"
    if suffix == ".d":
        return "tims"
    raise ValueError(f"Unsupported data format for MSDT-Converter: {source_data_path}")


def _search_thread_num() -> int:
    raw = os.getenv("AGENT_SEARCH_THREADS", "").strip()
    if not raw:
        return 10
    try:
        return max(1, int(raw))
    except ValueError:
        return 10


def _uniprot_proteome_url(proteome_id: str) -> str:
    query = f"%28proteome%3A{proteome_id}%29%20AND%20%28reviewed%3Atrue%29"
    return f"https://rest.uniprot.org/uniprotkb/stream?compressed=false&format=fasta&query={query}"


def _uniprot_multi_proteome_url(proteome_ids: list[str]) -> str:
    if len(proteome_ids) == 1:
        return _uniprot_proteome_url(proteome_ids[0])
    query = "%20OR%20".join(f"%28proteome%3A{proteome_id}%29" for proteome_id in proteome_ids)
    return f"https://rest.uniprot.org/uniprotkb/stream?compressed=false&format=fasta&query=%28{query}%29%20AND%20%28reviewed%3Atrue%29"


_KNOWN_UNIPROT_FASTAS = {
    "UP000005640": (("homo sapiens", "human"), "uniprot_human_UP000005640.fasta"),
    "UP000000589": (("mus musculus", "mouse"), "uniprot_mouse_UP000000589.fasta"),
    "UP000002494": (("rattus norvegicus", "rat"), "uniprot_rat_UP000002494.fasta"),
    "UP000002311": (("saccharomyces cerevisiae", "yeast"), "uniprot_yeast_UP000002311.fasta"),
    "UP000000625": (("escherichia coli", "e. coli"), "uniprot_ecoli_k12_UP000000625.fasta"),
    "UP000059680": (("oryza sativa", "rice"), "uniprot_oryza_sativa_UP000059680.fasta"),
}


def _known_uniprot_matches(species: str) -> list[tuple[str, str]]:
    normalized = species.lower()
    matches: list[tuple[str, str]] = []
    for proteome_id, (aliases, file_name) in _KNOWN_UNIPROT_FASTAS.items():
        if any(alias in normalized for alias in aliases):
            matches.append((proteome_id, file_name))
    return matches


def _combined_uniprot_fasta_name(matches: list[tuple[str, str]]) -> str:
    parts = []
    for _proteome_id, file_name in matches:
        stem = Path(file_name).stem
        parts.append(stem.removeprefix("uniprot_"))
    return f"uniprot_{'_'.join(parts)}.fasta"


def _species_fasta_choice(species: str) -> tuple[str, str, str | None]:
    matches = _known_uniprot_matches(species)
    if len(matches) >= 2:
        proteome_ids = [proteome_id for proteome_id, _file_name in matches]
        return _combined_uniprot_fasta_name(matches), "inferred", _uniprot_multi_proteome_url(proteome_ids)
    if len(matches) == 1:
        proteome_id, file_name = matches[0]
        return file_name, "inferred", _uniprot_proteome_url(proteome_id)
    return "reference_requires_review.fasta", "defaulted", None


def _uniprot_proteome_id_from_text(*values: Any) -> str | None:
    ids = _uniprot_proteome_ids_from_text(*values)
    return ids[0] if ids else None


def _uniprot_proteome_ids_from_text(*values: Any) -> list[str]:
    text = " ".join(str(value) for value in values if value)
    ids: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\bUP\d{9}\b", text, re.IGNORECASE):
        proteome_id = match.group(0).upper()
        if proteome_id not in seen:
            seen.add(proteome_id)
            ids.append(proteome_id)
    return ids


def _canonical_uniprot_fasta_name(proteome_id: str, fallback_name: str) -> str:
    known = _KNOWN_UNIPROT_FASTAS.get(proteome_id)
    if known is not None:
        return known[1]
    return fallback_name if proteome_id in fallback_name else f"uniprot_{proteome_id}.fasta"


def _canonical_uniprot_fasta_name_for_ids(proteome_ids: list[str], fallback_name: str) -> str:
    if len(proteome_ids) == 1:
        return _canonical_uniprot_fasta_name(proteome_ids[0], fallback_name)
    matches: list[tuple[str, str]] = []
    for proteome_id in proteome_ids:
        known = _KNOWN_UNIPROT_FASTAS.get(proteome_id)
        if known is None:
            return fallback_name
        matches.append((proteome_id, known[1]))
    return _combined_uniprot_fasta_name(matches)


def _metadata_text_values(project_context: ProjectContext | None, key: str) -> list[str]:
    if project_context is None:
        return []
    metadata_value = project_context.metadata.get(key)
    value = metadata_value.value if metadata_value is not None else []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)] if value else []


def _project_file_species_override(project_context: ProjectContext | None, source_file_name: str) -> str | None:
    if project_context is None or project_context.project_accession != "PXD016662":
        return None
    organisms = " ".join(_metadata_text_values(project_context, "organisms")).lower()
    if not ("rattus" in organisms and "homo sapiens" in organisms):
        return None
    name = source_file_name.lower()
    if re.search(r"(^|[_-])rat([_-]|\d|$)", name):
        return "Rattus norvegicus (rat)"
    if any(token in name for token in ("lfqprot", "lfqphos", "diaprot", "diafaims", "hela")):
        return "Homo sapiens (human)"
    return None


def _attributes_with_fasta_species_override(
    attributes: AttributeSet,
    project_context: ProjectContext | None,
    source_file_name: str,
) -> AttributeSet:
    source = str(attributes.species.source or "").strip().lower()
    if source.startswith("user_review"):
        return attributes
    species = _project_file_species_override(project_context, source_file_name)
    if species is None:
        return attributes
    return attributes.model_copy(
        update={
            "species": AttributeValue(
                value=species,
                confidence=1.0,
                source="file_name_rule",
                evidence_excerpt=f"PXD016662 file-level species inferred from file name: {source_file_name}",
                conflict_flag=False,
            )
        }
    )


def is_placeholder_fasta(path: Path) -> bool:
    try:
        sample = path.read_text(encoding="utf-8", errors="ignore")[:4096].lower()
    except OSError:
        return False
    return "placeholder" in sample or "mpeptideseqvence" in sample


def _safe_file_name(file_name: str) -> str:
    posix_name = PurePosixPath(file_name).name
    windows_name = PureWindowsPath(posix_name).name
    if windows_name in {"", ".", ".."}:
        raise ValueError(f"无效的文件名：{file_name}")
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
        return None, None, ["发现多个项目 FASTA 文件，需要人工选择。请使用 --reviewed-fasta-path 或 --reviewed-fasta-url 指定。"]

    fasta_record = fasta_records[0]
    file_name = _safe_file_name(str(fasta_record.get("fileName", "")))
    if file_name.lower().endswith(".gz"):
        file_name = Path(file_name).stem
    download_url = PrideClient.first_download_url(fasta_record)
    if not download_url:
        return None, None, [f"项目 FASTA 文件 {file_name} 没有可用的下载地址。"]
    return output_dir / "fasta" / file_name, download_url, []


def _reviewed_fasta_choice(
    output_dir: Path,
    reviewed_fasta_path: str | Path | None = None,
    reviewed_fasta_url: str | None = None,
    reviewed_fasta_name: str | None = None,
) -> tuple[Path | None, str | None]:
    if reviewed_fasta_path is not None:
        return Path(reviewed_fasta_path), None
    if reviewed_fasta_url:
        parsed_name = PurePosixPath(urlparse(reviewed_fasta_url).path).name
        file_name = _safe_file_name(reviewed_fasta_name or parsed_name or "reviewed_reference.fasta")
        if file_name.lower().endswith(".gz"):
            file_name = Path(file_name).stem
        if not file_name.lower().endswith((".fasta", ".fa", ".faa")):
            file_name = "reviewed_reference.fasta"
        return output_dir / "fasta" / file_name, reviewed_fasta_url
    return None, None


def _is_uniprot_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "uniprot.org" or host.endswith(".uniprot.org")


def _llm_recommended_fasta_choice(attributes: AttributeSet, output_dir: Path) -> tuple[Path | None, str | None]:
    hints = _hint_mapping(attributes)
    species_matches = _known_uniprot_matches(str(attributes.species.value))
    species_proteome_ids = {proteome_id for proteome_id, _file_name in species_matches}
    species_file_name, _species_mode, species_url = _species_fasta_choice(str(attributes.species.value))
    fasta_url = PrideClient._normalize_download_url(str(hints.get("recommended_fasta_url") or "").strip()) or ""
    proteome_ids = _uniprot_proteome_ids_from_text(
        fasta_url,
        hints.get("recommended_fasta_name"),
        hints.get("recommended_fasta_source"),
        hints.get("database"),
        attributes.species.value,
    )
    if fasta_url and _is_uniprot_url(fasta_url):
        if proteome_ids:
            if species_url and species_proteome_ids and not set(proteome_ids).issubset(species_proteome_ids):
                fasta_name = species_file_name
                fasta_url = species_url
            else:
                fasta_name = _canonical_uniprot_fasta_name_for_ids(proteome_ids, species_file_name)
                fasta_url = _uniprot_multi_proteome_url(proteome_ids)
        elif species_url:
            fasta_name = species_file_name
            fasta_url = species_url
        else:
            fasta_name = species_file_name
        return _reviewed_fasta_choice(output_dir, reviewed_fasta_url=fasta_url, reviewed_fasta_name=fasta_name)

    hint_text = " ".join(
        str(value)
        for value in (
            hints.get("recommended_fasta_name"),
            hints.get("recommended_fasta_source"),
            hints.get("database"),
            attributes.species.value,
        )
        if value
    )
    proteome_ids = _uniprot_proteome_ids_from_text(hint_text)
    if proteome_ids:
        if species_url and species_proteome_ids and not set(proteome_ids).issubset(species_proteome_ids):
            return _reviewed_fasta_choice(output_dir, reviewed_fasta_url=species_url, reviewed_fasta_name=species_file_name)
        fasta_name = _canonical_uniprot_fasta_name_for_ids(proteome_ids, species_file_name)
        return _reviewed_fasta_choice(
            output_dir,
            reviewed_fasta_url=_uniprot_multi_proteome_url(proteome_ids),
            reviewed_fasta_name=fasta_name,
        )

    if species_url:
        return _reviewed_fasta_choice(output_dir, reviewed_fasta_url=species_url, reviewed_fasta_name=species_file_name)
    return None, None


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


def _workflow_name_from_llm(attributes: AttributeSet) -> str | None:
    """从 LLM 推荐结果中获取 workflow 名称。

    必须由大模型推荐 workflow，不使用规则推断。
    优先从 search_parameter_hints 中提取，其次检查顶层属性。
    如果大模型未推荐或推荐的 workflow 不存在，返回 None。
    """
    hints = _hint_mapping(attributes)
    value = hints.get("recommended_workflow_name") or hints.get("workflow_name")
    if value:
        return str(value)
    # LLM 可能将 recommended_workflow_name 作为顶层属性返回
    if hasattr(attributes, "recommended_workflow_name") and attributes.recommended_workflow_name is not None:
        attr = attributes.recommended_workflow_name
        if attr.value and str(attr.value).strip():
            return str(attr.value)
    return None


def _hint_mapping(attributes: AttributeSet) -> dict[str, Any]:
    hints = attributes.search_parameter_hints.value
    return dict(hints) if isinstance(hints, dict) else {}


def _llm_confirmed_workflow_name(attributes: AttributeSet, workspace_root: Path) -> str | None:
    """检查 LLM 推荐的 workflow 是否存在。"""
    workflow_name = _workflow_name_from_llm(attributes)
    if not workflow_name:
        return None
    safe_name = _safe_file_name(workflow_name)
    workflow_path = workspace_root / "profiles" / "fragpipe" / safe_name
    if workflow_path.exists() and workflow_path.is_file():
        return safe_name
    return None


def _blocking_issues(attributes: AttributeSet, workflow_name: str, raw_data_type: str) -> list[str]:
    issues: list[str] = []
    if raw_data_type == "mgf":
        return issues
    if raw_data_type == "mzid":
        return ["mzIdentML/mzid 是搜库结果文件，不含完整谱图；当前只能识别，不能自动生成标准 MSDT 输入。"]
    if _is_dia_mode(attributes):
        issues.append(
            "检测到 DIA（数据独立采集）数据。当前系统仅支持 DDA（数据依赖采集）数据处理流程。\n"
            "DIA 数据建议使用以下专用工具：\n"
            "  - Spectronaut（商业软件）\n"
            "  - DIA-NN（免费开源）\n"
            "  - FragPipe DIA workflow（需单独配置）\n"
            "如需处理 DIA 数据，请使用上述工具或联系开发者扩展 DIA 支持。"
        )
        return issues  # DIA 直接阻断，不检查其他条件
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
        if value.conflict_flag and not _is_resolved_attribute(value):
            issues.append(f"必需属性存在冲突：{name}")
    if attributes.search_parameter_hints.conflict_flag:
        issues.append(f"搜库参数需要人工复核：{attributes.search_parameter_hints.evidence_excerpt}")
    if not workflow_name:
        issues.append("未找到与当前推断属性匹配的 FragPipe workflow 模板。")
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


def _is_resolved_attribute(value: AttributeValue) -> bool:
    raw_value = value.value
    if raw_value in (None, "", "unknown"):
        return False
    source = str(value.source or "").strip().lower()
    trusted_sources = ("llm_confirmed", "sdrf", "mzml", "user_review")
    is_trusted_source = any(source.startswith(trusted) for trusted in trusted_sources)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if not normalized or normalized == "unknown":
            return False
        if re.search(r"\s*(;|\|)\s*", raw_value):
            return value.confidence >= 0.8 and is_trusted_source and len(_known_uniprot_matches(raw_value)) == 2
    if not value.conflict_flag:
        return True

    return value.confidence >= 0.8 and is_trusted_source


def _instrument_family_from_name(name: str) -> str:
    lowered = name.lower()
    if "orbitrap" in lowered or "exploris" in lowered or "q exactive" in lowered or "ltq" in lowered:
        return "orbitrap"
    if "tims" in lowered or "pasef" in lowered:
        return "tims"
    if "tof" in lowered:
        return "tof"
    return "unknown"


def _project_instrument_families(project_context: ProjectContext | None) -> set[str]:
    families = {
        _instrument_family_from_name(instrument)
        for instrument in _metadata_values(project_context, "instruments")
        if str(instrument).strip()
    }
    families.discard("unknown")
    return families


def _has_search_tolerances(attributes: AttributeSet) -> bool:
    hints = attributes.search_parameter_hints.value
    if not isinstance(hints, dict):
        return False
    return bool(hints.get("precursor_tol") and hints.get("fragment_tol"))


def _is_resolved_instrument_context(project_context: ProjectContext | None, attributes: AttributeSet) -> bool:
    if _is_resolved_attribute(attributes.instrument_name) and _is_resolved_attribute(attributes.instrument_family):
        return True
    if not _is_resolved_attribute(attributes.instrument_family):
        return False
    family = str(attributes.instrument_family.value or "").strip().lower()
    if family in {"", "unknown", "ambiguous"}:
        return False
    project_families = _project_instrument_families(project_context)
    return len(project_families) == 1 and family in project_families and _has_search_tolerances(attributes)


def _context_blocking_issues(project_context: ProjectContext | None, attributes: AttributeSet) -> list[str]:
    issues: list[str] = []
    experiment_types = " | ".join(_metadata_values(project_context, "experimentTypes")).lower()
    if "top-down" in experiment_types or "top down" in experiment_types:
        issues.append("当前 bottom-up MSDT 搜库流程不支持 Top-down 蛋白质组学项目。")
    if project_context is not None and not project_context.sdrf_rows:
        organisms = set(_metadata_values(project_context, "organisms"))
        instruments = set(_metadata_values(project_context, "instruments"))
        if len(organisms) > 1 and not _is_resolved_attribute(attributes.species):
            issues.append("未找到匹配的 SDRF 行，且项目包含多个物种；无法确定文件级物种信息。")
        if len(instruments) > 1 and not _is_resolved_instrument_context(project_context, attributes):
            issues.append("未找到匹配的 SDRF 行，且项目包含多个仪器；无法确定文件级仪器信息。")
    return issues


def _fasta_blocking_issues(
    project_context: ProjectContext | None,
    attributes: AttributeSet,
    fasta_path: Path,
    fasta_mode: str,
    raw_data_type: str,
    fasta_download_url: str | None = None,
) -> list[str]:
    if raw_data_type in {"mgf", "mzid"}:
        return []
    if fasta_mode == "defaulted" and not fasta_download_url:
        species = str(attributes.species.value or "").strip() or "unknown"
        return [
            f"未找到可从 UniProt 下载的真实 FASTA（物种：{species}）。"
            "默认占位 FASTA 不能用于真实搜库；请让 LLM 给出 UniProt proteome ID，"
            "或勾选/指定 PRIDE 项目 FASTA。"
        ]
    if is_placeholder_fasta(fasta_path) and not fasta_download_url:
        return [
            f"FASTA 文件 {fasta_path.name} 是占位文件，不能用于真实搜库。"
            "请通过 --reviewed-fasta-path 或 --reviewed-fasta-url 参数提供真实的蛋白质序列数据库，"
            "或确保 PRIDE 项目中包含可用的 FASTA 文件。"
        ]
    if fasta_mode != "defaulted" or project_context is None or project_context.sdrf_rows:
        return []
    species = str(attributes.species.value or "").strip()
    if not species:
        return []
    hints = _hint_mapping(attributes)
    database_hint = str(hints.get("database") or "").strip()
    fasta_name = str(hints.get("recommended_fasta_name") or "").strip()
    fasta_source = str(hints.get("recommended_fasta_source") or "").strip()
    fasta_url = str(hints.get("recommended_fasta_url") or "").strip()
    suffix = f"；数据库线索：{database_hint}" if database_hint else ""
    if fasta_name:
        suffix += f"；LLM 建议 FASTA：{fasta_name}"
    if fasta_source:
        suffix += f"；来源：{fasta_source}"
    if fasta_url:
        suffix += f"；URL：{fasta_url}"
    return [
        f"未找到项目 FASTA 文件，且当前物种（{species}）没有内置参考库。"
        f"当前没有真实 FASTA，不能用于可靠搜库。"
        f"请通过 --reviewed-fasta-path 或 --reviewed-fasta-url 参数提供真实的蛋白质序列数据库。"
        f"{suffix}"
    ]


def plan_dda_execution(
    task_id: str,
    source_file_name: str,
    source_data_path: str | Path,
    project_resolution: ProjectResolution,
    attributes: AttributeSet,
    output_dir: str | Path,
    project_context: ProjectContext | None = None,
    reviewed_fasta_path: str | Path | None = None,
    reviewed_fasta_url: str | None = None,
    reviewed_fasta_name: str | None = None,
    prefer_project_fasta: bool = False,
    accept_search_parameter_review: bool = False,
) -> DdaExecutionPlan:
    source_data_path = Path(source_data_path)
    output_dir = Path(output_dir)
    raw_data_type = _detect_raw_type(source_data_path, source_file_name)
    workspace_root = _workspace_root()
    decision_attributes = _attributes_with_fasta_species_override(attributes, project_context, source_file_name)
    reviewed_path, reviewed_url = _reviewed_fasta_choice(
        output_dir,
        reviewed_fasta_path=reviewed_fasta_path,
        reviewed_fasta_url=reviewed_fasta_url,
        reviewed_fasta_name=reviewed_fasta_name,
    )
    llm_fasta_path, llm_fasta_url = _llm_recommended_fasta_choice(decision_attributes, output_dir)
    if reviewed_path is not None:
        fasta_path = reviewed_path
        fasta_mode = "reviewed"
        project_fasta_url = reviewed_url
        fasta_issues = []
    elif prefer_project_fasta:
        project_fasta_path, project_fasta_url, fasta_issues = _project_fasta_choice(project_context, output_dir)
        if project_fasta_path is not None:
            fasta_path = project_fasta_path
            fasta_mode = "reproduced"
        elif not fasta_issues and llm_fasta_path is not None:
            fasta_path = llm_fasta_path
            fasta_mode = "inferred"
            project_fasta_url = llm_fasta_url
        else:
            fasta_file_name, fasta_mode, inferred_fasta_url = _species_fasta_choice(str(decision_attributes.species.value))
            fasta_path = output_dir / "fasta" / fasta_file_name
            project_fasta_url = inferred_fasta_url
    elif llm_fasta_path is not None:
        fasta_path = llm_fasta_path
        fasta_mode = "inferred"
        project_fasta_url = llm_fasta_url
        fasta_issues = []
    else:
        project_fasta_path, project_fasta_url, fasta_issues = _project_fasta_choice(project_context, output_dir)
        if project_fasta_path is not None:
            fasta_path = project_fasta_path
            fasta_mode = "reproduced"
        else:
            fasta_file_name, fasta_mode, inferred_fasta_url = _species_fasta_choice(str(decision_attributes.species.value))
            fasta_path = output_dir / "fasta" / fasta_file_name
            project_fasta_url = inferred_fasta_url
    # 必须由大模型推荐 workflow，不使用规则推断
    workflow_name = _llm_confirmed_workflow_name(attributes, workspace_root)
    if not workflow_name:
        llm_workflow = _workflow_name_from_llm(attributes)
        if llm_workflow:
            blocking_issues = [f"大模型推荐的 workflow '{llm_workflow}' 不存在于 profiles/fragpipe/ 目录中。请检查 workflow 名称是否正确。"]
        else:
            blocking_issues = ["大模型未推荐 workflow。必须配置 LLM API 并确保大模型能正确推荐 workflow。请检查 AGENT_LLM_API_KEY 配置。"]
        workflow_name = ""  # 设置为空，后续会因为 blocking_issues 而被阻止
    else:
        blocking_issues = _blocking_issues(decision_attributes, workflow_name, raw_data_type)
    blocking_issues.extend(fasta_issues)
    blocking_issues.extend(_context_blocking_issues(project_context, decision_attributes))
    blocking_issues.extend(
        _fasta_blocking_issues(
            project_context,
            decision_attributes,
            fasta_path,
            fasta_mode,
            raw_data_type,
            fasta_download_url=project_fasta_url,
        )
    )
    if accept_search_parameter_review:
        blocking_issues = [issue for issue in blocking_issues if "搜库参数需要人工复核" not in issue]

    rawspectrum_dir = output_dir / "rawspectrum"
    fragpipe_dir = output_dir / "fragpipe"
    msdt_dir = output_dir / "msdt"
    logs_dir = output_dir / "logs"

    rawspectrum_output_path = rawspectrum_dir / f"{source_data_path.stem}_rawspectrum.parquet"
    manifest_path = fragpipe_dir / "fragpipe-files.fp-manifest"
    expected_pin_path = fragpipe_dir / "exp" / f"{source_data_path.stem}_edited.pin"
    converter_config_path = output_dir / "converter_config.json"
    if raw_data_type == "mgf":
        msdt_output_name = f"{source_data_path.stem}_mgf_msdt.parquet"
    else:
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
        thread_num=_search_thread_num(),
        needs_review=bool(blocking_issues),
        blocking_issues=blocking_issues,
    )
