from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import httpx

from agent.models import AttributeSet, AttributeValue, ProjectContext


ReportFn = Callable[[str], None]


class LLMReasoner(Protocol):
    def confirm_search_parameters(
        self,
        context: ProjectContext,
        attributes: AttributeSet,
    ) -> Mapping[str, AttributeValue]:
        """Return LLM-confirmed attribute updates keyed by AttributeSet field name."""


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_flatten(item) for item in value.values())
    if isinstance(value, list | tuple | set):
        return " ".join(_flatten(item) for item in value)
    return str(value)


def _is_missing(value: Any) -> bool:
    return value in (None, "", "unknown", "ambiguous") or value == {}


def _same_value(left: Any, right: Any) -> bool:
    return _flatten(left).strip().lower() == _flatten(right).strip().lower()


def _conflicting_attribute(current: AttributeValue, proposed: AttributeValue) -> AttributeValue:
    return current.model_copy(
        update={
            "conflict_flag": True,
            "evidence_excerpt": (
                f"{current.evidence_excerpt} | LLM suggested {proposed.value}: "
                f"{proposed.evidence_excerpt}"
            ).strip(),
        }
    )


def _merge_attribute(current: AttributeValue, proposed: AttributeValue) -> AttributeValue:
    if _is_missing(proposed.value):
        return current
    if _is_missing(current.value):
        return proposed
    if _same_value(current.value, proposed.value):
        return proposed
    if current.source.startswith("pride.") and current.confidence >= 0.9:
        return _conflicting_attribute(current, proposed)
    if current.confidence >= 0.9 and proposed.confidence < current.confidence:
        return _conflicting_attribute(current, proposed)
    if proposed.confidence >= current.confidence:
        return proposed
    return current


def _derive_instrument_family(instrument_name: Any) -> AttributeValue:
    name = _flatten(instrument_name)
    lowered = name.lower()
    if "orbitrap" in lowered or "exploris" in lowered or "q exactive" in lowered:
        return AttributeValue(
            value="orbitrap",
            confidence=0.85,
            source="llm_confirmed_derived",
            evidence_excerpt=f"Instrument name suggests Orbitrap family: {name}",
            conflict_flag=False,
        )
    if "tims" in lowered:
        return AttributeValue(
            value="tims",
            confidence=0.85,
            source="llm_confirmed_derived",
            evidence_excerpt=f"Instrument name suggests timsTOF family: {name}",
            conflict_flag=False,
        )
    if "tof" in lowered:
        return AttributeValue(
            value="tof",
            confidence=0.7,
            source="llm_confirmed_derived",
            evidence_excerpt=f"Instrument name suggests TOF family: {name}",
            conflict_flag=False,
        )
    return AttributeValue(value="unknown", confidence=0.0, source="none", evidence_excerpt="", conflict_flag=False)


def _coerce_attribute(value: Any) -> AttributeValue | None:
    if isinstance(value, AttributeValue):
        return value
    if isinstance(value, Mapping):
        try:
            return AttributeValue(**value)
        except (TypeError, ValueError):
            return None
    return None


def _metadata_context_text(context: ProjectContext, include_project_files: bool = True) -> str:
    lines = [
        f"project_accession: {context.project_accession}",
        f"target_file: {context.file_name}",
    ]
    for key, metadata in context.metadata.items():
        if metadata.value:
            lines.append(f"{key} ({metadata.source}): {_flatten(metadata.value)}")
    project_file_names = [str(item.get("fileName", "")) for item in context.project_files if item.get("fileName")]
    if include_project_files and project_file_names:
        lines.append("project_files: " + "; ".join(project_file_names[:80]))
    return "\n".join(lines)


def _no_sdrf_attribute_prompt(context: ProjectContext, attributes: AttributeSet) -> str:
    current = attributes.model_dump(mode="json")
    return (
        "You are confirming proteomics raw-file metadata when no SDRF rows are available.\n"
        "Use only the project metadata, protocol descriptions, project file names, and target file name below.\n"
        "Return strict JSON. Keys may include acquisition_mode, species, instrument_name, instrument_family, "
        "enzyme, labeling_strategy, fixed_mods, variable_mods, fractionation_hint, search_parameter_hints.\n"
        "Each key must be an object with value, confidence, source, evidence_excerpt, conflict_flag.\n"
        "For Sage config generation, normalize search_parameter_hints using keys such as precursor_tol, "
        "fragment_tol, missed_cleavages, precursor_charge, isotope_errors, min_peaks, max_peaks, "
        "min_matched_peaks, max_variable_mods, tmt_channel_count, and data_family. "
        "data_family should be one of mzml, tims, thermo_raw, sciex_wiff, mgf, or unknown. "
        "Also include sidecar_patterns when project files imply companion files such as .wiff.scan. "
        "Normalize fixed_mods and variable_mods "
        "as human-readable modification strings with residue/site and mass when available, e.g. "
        "Carbamidomethyl (C) 57.02146, Oxidation (M) 15.9949, TMT (K) 229.16293.\n"
        "Normalize labeling_strategy to label-free, TMT, iTRAQ, SILAC, or unknown.\n"
        "Use source='llm_confirmed'. Keep evidence_excerpt short and grounded in the supplied text.\n"
        "Set confidence lower when inferred from weak naming patterns. Do not invent unsupported values.\n\n"
        f"Project context:\n{_metadata_context_text(context)}\n\n"
        f"Current rule-based attributes:\n{json.dumps(current, ensure_ascii=False)}"
    )


def _sdrf_attribute_prompt(context: ProjectContext, attributes: AttributeSet) -> str:
    current = attributes.model_dump(mode="json")
    return (
        "You are summarizing matched SDRF rows into a single file-level proteomics workflow decision.\n"
        "Treat SDRF as the primary source. Use project metadata only to disambiguate or normalize SDRF values.\n"
        "The matched SDRF rows may describe mixtures, ontology-coded strings such as NT=... or AC=..., "
        "and workflow/search settings spread across multiple rows.\n"
        "Return strict JSON. Keys may include acquisition_mode, species, instrument_name, instrument_family, "
        "enzyme, labeling_strategy, fixed_mods, variable_mods, fractionation_hint, search_parameter_hints.\n"
        "Each key must be an object with value, confidence, source, evidence_excerpt, conflict_flag.\n"
        "For Sage config generation, extract enzyme, missed_cleavages, precursor_tol, fragment_tol, "
        "fixed_mods, variable_mods, labeling_strategy, precursor_charge, isotope_errors, min_peaks, max_peaks, "
        "min_matched_peaks, max_variable_mods, tmt_channel_count, data_family, and sidecar_patterns when SDRF rows or "
        "project metadata explicitly provide them. Use normalized modification strings with residue/site and mass "
        "when available, e.g. Carbamidomethyl (C) 57.02146, Oxidation (M) 15.9949, TMT (K) 229.16293.\n"
        "Use source='llm_confirmed'. Prefer normalized human-readable values over raw NT=/AC= strings.\n"
        "When SDRF rows represent multiple organisms in one file, summarize species as a multi-species mixture.\n"
        "Do not invent parameters not grounded in the supplied SDRF rows or metadata.\n\n"
        f"Project context:\n{_metadata_context_text(context, include_project_files=False)}\n\n"
        f"Matched SDRF rows:\n{json.dumps(context.sdrf_rows, ensure_ascii=False)}\n\n"
        f"Current deterministic attributes:\n{json.dumps(current, ensure_ascii=False)}"
    )


class OpenAICompatibleReasoner:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.4",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def confirm_search_parameters(
        self,
        context: ProjectContext,
        attributes: AttributeSet,
    ) -> Mapping[str, AttributeValue]:
        prompt = _sdrf_attribute_prompt(context, attributes) if context.sdrf_rows else _no_sdrf_attribute_prompt(context, attributes)
        payload = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "Return only valid JSON for proteomics metadata confirmation.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        decoded = json.loads(content)
        return {
            key: attribute
            for key, value in decoded.items()
            if (attribute := _coerce_attribute(value)) is not None
        }


def default_llm_reasoner() -> LLMReasoner | None:
    api_key = os.getenv("AGENT_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    model = os.getenv("AGENT_LLM_MODEL", "gpt-5.4")
    base_url = os.getenv("AGENT_LLM_BASE_URL", "https://api.openai.com/v1")
    return OpenAICompatibleReasoner(api_key=api_key, model=model, base_url=base_url)


def confirm_no_sdrf_parameters(
    context: ProjectContext,
    attributes: AttributeSet,
    llm_reasoner: LLMReasoner | None = None,
    report: ReportFn | None = None,
) -> AttributeSet:
    if context.sdrf_rows:
        return attributes

    if report is not None:
        report("未找到 SDRF 行；将结合项目描述和文件名线索判断参数。")

    reasoner = llm_reasoner or default_llm_reasoner()
    if reasoner is None:
        if report is not None:
            report("未配置大模型推理器；保留规则推断结果。")
        return attributes

    if report is not None:
        report("正在调用大模型确认文件属性和搜库参数。")

    try:
        updates = reasoner.confirm_search_parameters(context, attributes)
    except Exception as exc:
        if report is not None:
            report(f"大模型确认失败；保留规则推断结果。原因={exc}")
        return attributes
    merged = attributes.model_dump()
    for field_name, proposed_value in updates.items():
        if field_name not in AttributeSet.model_fields:
            continue
        proposed = _coerce_attribute(proposed_value)
        if proposed is None:
            continue
        current = getattr(attributes, field_name)
        merged[field_name] = _merge_attribute(current, proposed)

    result = AttributeSet(**merged)
    if (
        _is_missing(result.instrument_family.value)
        and not _is_missing(result.instrument_name.value)
        and result.instrument_name.source.startswith("llm_confirmed")
    ):
        result = result.model_copy(update={"instrument_family": _derive_instrument_family(result.instrument_name.value)})

    if report is not None:
        report("大模型确认结果已合并到属性推断中。")
    return result


def confirm_sdrf_parameters(
    context: ProjectContext,
    attributes: AttributeSet,
    llm_reasoner: LLMReasoner | None = None,
    report: ReportFn | None = None,
) -> AttributeSet:
    if not context.sdrf_rows:
        return attributes

    reasoner = llm_reasoner or default_llm_reasoner()
    if reasoner is None:
        return attributes

    if report is not None:
        report(f"找到匹配的 SDRF 行（{len(context.sdrf_rows)} 行）；正在用大模型汇总文件级 workflow 属性。")

    try:
        updates = reasoner.confirm_search_parameters(context, attributes)
    except Exception as exc:
        if report is not None:
            report(f"大模型 SDRF 汇总失败；保留确定性 SDRF 推断结果。原因={exc}")
        return attributes

    merged = attributes.model_dump()
    for field_name, proposed_value in updates.items():
        if field_name not in AttributeSet.model_fields:
            continue
        proposed = _coerce_attribute(proposed_value)
        if proposed is None:
            continue
        if _is_missing(proposed.value):
            continue
        current = getattr(attributes, field_name)
        if proposed.confidence >= 0.85:
            merged[field_name] = proposed
        else:
            merged[field_name] = _merge_attribute(current, proposed)

    result = AttributeSet(**merged)
    if (
        _is_missing(result.instrument_family.value)
        and not _is_missing(result.instrument_name.value)
        and result.instrument_name.source.startswith("llm_confirmed")
    ):
        result = result.model_copy(update={"instrument_family": _derive_instrument_family(result.instrument_name.value)})

    if report is not None:
        report("大模型 SDRF 汇总结果已合并到属性推断中。")
    return result
