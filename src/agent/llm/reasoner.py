from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import httpx

from agent.inference.enzyme_semantics import complete_enzyme_workflow_overrides
from agent.models import AttributeSet, AttributeValue, ProjectContext


ReportFn = Callable[[Any], None]


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _debug_write(text: str) -> None:
    if not _env_flag("AGENT_LLM_DEBUG"):
        return
    import sys

    sys.stderr.write(text)
    sys.stderr.flush()


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
    def normalized(value: Any) -> str:
        text = _flatten(value).strip().lower()
        text = re.sub(r"\s*\([^)]*\)", "", text)
        text = re.sub(r"\s*<[^>]*>", "", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return " ".join(text.split())

    return normalized(left) == normalized(right)


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


def _is_high_confidence_llm(attribute: AttributeValue) -> bool:
    return attribute.source.startswith("llm_confirmed") and attribute.confidence >= 0.85


def _merge_attribute(current: AttributeValue, proposed: AttributeValue) -> AttributeValue:
    if _is_missing(proposed.value):
        return current
    if _is_missing(current.value):
        return proposed
    if isinstance(current.value, Mapping) and isinstance(proposed.value, Mapping):
        merged_value = dict(current.value)
        merged_value.update(proposed.value)
        return proposed.model_copy(update={"value": merged_value})
    if _same_value(current.value, proposed.value):
        return proposed
    if _is_high_confidence_llm(proposed) and not current.source.startswith("sdrf"):
        return proposed.model_copy(update={"conflict_flag": False})
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
        f"repository: {context.repository}",
        f"project_accession: {context.project_accession}",
        f"native_accession: {context.native_accession or ''}",
        f"px_accession: {context.px_accession or ''}",
        f"target_file: {context.file_name}",
    ]
    for key, metadata in context.metadata.items():
        if metadata.value:
            lines.append(f"{key} ({metadata.source}): {_flatten(metadata.value)}")
    project_file_names = [str(item.get("fileName", "")) for item in context.project_files if item.get("fileName")]
    if include_project_files and project_file_names:
        lines.append("project_files: " + "; ".join(project_file_names[:80]))
        parameter_like = [
            name
            for name in project_file_names
            if any(
                token in name.lower()
                for token in ("param", "workflow", "fragger", "fragpipe", "sage", "msfragger", "search", "readme", "metadata")
            )
        ]
        fasta_like = [
            name
            for name in project_file_names
            if name.lower().endswith((".fasta", ".fa", ".faa", ".fasta.gz", ".fa.gz", ".faa.gz"))
        ]
        if parameter_like:
            lines.append("parameter_or_workflow_files: " + "; ".join(parameter_like[:40]))
        if fasta_like:
            lines.append("fasta_files: " + "; ".join(fasta_like[:40]))
    if context.evidence_documents:
        evidence_lines = []
        for document in context.evidence_documents[:12]:
            source = str(document.get("source") or "repository.evidence")
            text = str(document.get("text") or document.get("value") or "")
            if text.strip():
                evidence_lines.append(f"{source}: {text[:2000]}")
        if evidence_lines:
            lines.append("evidence_documents:\n" + "\n".join(evidence_lines))
    return "\n".join(lines)


def _no_sdrf_attribute_prompt(context: ProjectContext, attributes: AttributeSet) -> str:
    current = attributes.model_dump(mode="json")
    return (
        "你是蛋白质组学数据分析专家。请确认质谱文件的元数据和搜库参数。返回 FLAT JSON 对象（不要嵌套）。\n\n"
        "必需 keys（每个都是 {value, confidence, source, evidence_excerpt, conflict_flag} 格式）：\n"
        "acquisition_mode, species, instrument_name, instrument_family, enzyme, labeling_strategy, "
        "fixed_mods, variable_mods, fractionation_hint, search_parameter_hints.\n\n"
        "search_parameter_hints.value 必须包含: missed_cleavages, precursor_tol, fragment_tol, "
        "min_peaks, max_variable_mods, data_family, "
        "recommended_workflow_name, recommended_fasta_name, recommended_fasta_url, recommended_fasta_source, "
        "workflow_parameter_overrides.\n"
        "workflow_parameter_overrides: object；仅填写需要写入所选 workflow 的 msfragger.* 参数；不需要微调时返回 {}。\n"
        "常用可调 key: msfragger.allowed_missed_cleavage_1, msfragger.misc.fragger.digest-mass-lo, msfragger.misc.fragger.digest-mass-hi, "
        "msfragger.search_enzyme_name_1/2, msfragger.search_enzyme_cut_1/2, msfragger.search_enzyme_sense_1/2, msfragger.num_enzyme_termini。\n"
        "多酶切时必须显式微调 workflow 酶切参数。例如 Trypsin + Arg-C：slot 1 保持 stricttrypsin/KR/C，slot 2 设置 Arg-C/R/C，"
        "msfragger.num_enzyme_termini=2；有依据时可将 missed cleavages 调到 3-4，并收紧 digest mass/length 范围。\n\n"
        "Semantic enzyme inference: do not rely only on literal enzyme tokens. Interpret protocol language "
        "using proteomics domain knowledge. For example, if the protocol says a lysine-specific endoproteinase, "
        "lysine-directed endoprotease, or digestion at lysine residues was used together with trypsin, infer "
        "enzyme.value='Trypsin/Lys-C' with high confidence only when the evidence supports both enzymes. For "
        "Trypsin/Lys-C, workflow_parameter_overrides must set slot 1 to stricttrypsin/KR/C, slot 2 to Lys-C/K/C, "
        "and msfragger.num_enzyme_termini=2.\n\n"
        "## DDA/DIA 判断（最关键）\n\n"
        "你必须根据以下信息判断数据采集模式（acquisition_mode）是 DDA 还是 DIA：\n\n"
        "### 判断依据\n"
        "1. SDRF 文件中的 'comment[data acquisition method]' 或 'comment[instrument]' 字段\n"
        "2. PRIDE 项目描述中的关键词\n"
        "3. 文件名中的线索（如包含 'DIA'、'SWATH'、'diaPASEF' 等通常是 DIA）\n"
        "4. Isolation Window（孤立窗口）信息：\n"
        "   - DDA: Isolation Window 较窄，通常 1-3 m/z，每次选择少数高强度离子碎裂\n"
        "   - DIA: Isolation Window 较宽，通常 10-50 m/z，覆盖大范围 m/z 进行全面扫描\n"
        "5. m/z 范围：\n"
        "   - DDA: 集中在目标离子附近，范围窄\n"
        "   - DIA: 覆盖 400-800+ m/z 宽范围\n\n"
        "### DDA 亚型判断\n"
        "如果判定为 DDA，还需进一步区分亚型：\n"
        "- **Label-free DDA (LFQ)**：无标记定量，检查 labeling_strategy 是否为 'none'/空\n"
        "- **TMT DDA**：检查 labeling_strategy 是否包含 'TMT'，需区分 TMT6/TMT10/TMT16\n"
        "- **iTRAQ DDA**：检查 labeling_strategy 是否包含 'iTRAQ'，区分 iTRAQ4/iTRAQ8\n"
        "- **SILAC DDA**：检查 labeling_strategy 是否包含 'SILAC'\n"
        "- **磷酸化 (phospho)**：检查 variable_mods 是否包含 'Phospho'\n"
        "- **泛素化 (ubiquitin)**：检查 variable_mods 是否包含 'GlyGly' 或 'ubiquitin'\n"
        "- **乙酰化 (acetyl)**：检查 variable_mods 是否包含 'Acetyl'\n"
        "- **糖基化 (glyco)**：检查 variable_mods 是否包含 'HexNAc' 或 'glyco'\n\n"
        "### DIA 亚型判断\n"
        "如果判定为 DIA，还需区分：\n"
        "- **常规 DIA**：标准 DIA 定量\n"
        "- **DIA 磷酸化**：DIA + Phospho 修饰\n"
        "- **diaPASEF**：timsTOF 的 DIA 模式\n"
        "- **DIA-Umpire**：先提取伪 MS/MS 再搜库\n\n"
        "## 可用 Workflows（必须从以下列表中选择）\n\n"
        "### DDA LFQ:\n"
        "  Default.workflow - 标准封闭数据库搜索\n"
        "  LFQ-MBR.workflow - Label-free DDA + Match-Between-Runs\n"
        "  LFQ-phospho.workflow - LFQ 磷酸化\n"
        "  LFQ-ubiquitin.workflow - LFQ 泛素化\n\n"
        "### DDA TMT:\n"
        "  TMT10.workflow - TMT 10-plex\n"
        "  TMT10-MS3.workflow - TMT 10-plex MS3\n"
        "  TMT10-bridge.workflow - TMT 10-plex 桥接设计\n"
        "  TMT10-phospho.workflow - TMT 10-plex 磷酸化\n"
        "  TMT10-MS3-phospho.workflow - TMT 10-plex MS3 磷酸化\n"
        "  TMT10-phospho-bridge.workflow - TMT 10-plex 磷酸化桥接\n"
        "  TMT10-acetyl.workflow - TMT 10-plex 乙酰化\n"
        "  TMT10-ubiquitin.workflow - TMT 10-plex 泛素化\n"
        "  TMT16.workflow - TMT 16-plex\n"
        "  TMT16-MS3.workflow - TMT 16-plex MS3\n"
        "  TMT16-phospho.workflow - TMT 16-plex 磷酸化\n"
        "  TMT16-acetyl.workflow - TMT 16-plex 乙酰化\n\n"
        "### DDA iTRAQ:\n"
        "  iTRAQ4.workflow - iTRAQ 4-plex\n"
        "  iTRAQ4-phospho.workflow - iTRAQ 4-plex 磷酸化\n\n"
        "### DDA SILAC:\n"
        "  SILAC3.workflow - SILAC 3-plex\n"
        "  SILAC3-phospho.workflow - SILAC 3-plex 磷酸化\n\n"
        "### DIA（注意：当前系统暂不支持 DIA 数据，会阻断运行）:\n"
        "  DIA_SpecLib_Quant.workflow - DIA 标准定量\n"
        "  DIA_SpecLib_Quant_Phospho.workflow - DIA 磷酸化\n"
        "  DIA_DIA-Umpire_SpecLib_Quant.workflow - DIA-Umpire 方法\n\n"
        "### 非特异酶切（HLA/免疫肽组学）:\n"
        "  Nonspecific-HLA.workflow - HLA 非特异性搜索\n"
        "  Nonspecific-HLA-C57.workflow - HLA C57 烷基化\n"
        "  Nonspecific-HLA-TMT10.workflow - HLA TMT10\n"
        "  Nonspecific-HLA-phospho.workflow - HLA 磷酸化\n"
        "  Nonspecific-HLA-glyco.workflow - HLA 糖肽\n"
        "  Nonspecific-HLA-DIA.workflow - HLA DIA\n"
        "  Nonspecific-peptidome.workflow - 非特异肽段\n\n"
        "### 特殊应用:\n"
        "  Open.workflow - 开放搜索\n"
        "  FPOP.workflow - FPOP 氧化标记\n"
        "  glyco-N-LFQ.workflow - N-糖基化 LFQ\n"
        "  glyco-N-TMT.workflow - N-糖基化 TMT\n\n"
        "## 输出要求\n"
        "- acquisition_mode: 'DDA' 或 'DIA'（必须明确）\n"
        "- recommended_workflow_name: 必须从上述列表中选择，格式如 'Default.workflow'\n"
        "- workflow_parameter_overrides: 必须放在 search_parameter_hints.value 中，使用精确 FragPipe key，例如 msfragger.search_enzyme_name_2。\n"
        "- Use source='llm_confirmed'. Prefer normalized values. Do not invent unsupported values.\n\n"
        f"Project context:\n{_metadata_context_text(context)}\n\n"
        f"Current attributes:\n{json.dumps(current, ensure_ascii=False)}"
    )


def _sdrf_attribute_prompt(context: ProjectContext, attributes: AttributeSet) -> str:
    current = attributes.model_dump(mode="json")
    return (
        "你是蛋白质组学数据分析专家。请根据 SDRF 行汇总文件级质谱属性。返回 FLAT JSON 对象（不要嵌套）。\n\n"
        "必需 keys（每个都是 {value, confidence, source, evidence_excerpt, conflict_flag} 格式）：\n"
        "acquisition_mode, species, instrument_name, instrument_family, enzyme, labeling_strategy, "
        "fixed_mods, variable_mods, fractionation_hint, search_parameter_hints.\n\n"
        "search_parameter_hints.value 必须包含: missed_cleavages, precursor_tol, fragment_tol, "
        "min_peaks, max_variable_mods, data_family, "
        "recommended_workflow_name, recommended_fasta_name, recommended_fasta_url, recommended_fasta_source, "
        "workflow_parameter_overrides.\n"
        "workflow_parameter_overrides: object；仅填写需要写入所选 workflow 的 msfragger.* 参数；不需要微调时返回 {}。\n"
        "常用可调 key: msfragger.allowed_missed_cleavage_1, msfragger.misc.fragger.digest-mass-lo, msfragger.misc.fragger.digest-mass-hi, "
        "msfragger.search_enzyme_name_1/2, msfragger.search_enzyme_cut_1/2, msfragger.search_enzyme_sense_1/2, msfragger.num_enzyme_termini。\n"
        "多酶切时必须显式微调 workflow 酶切参数。例如 Trypsin + Arg-C：slot 1 保持 stricttrypsin/KR/C，slot 2 设置 Arg-C/R/C，"
        "msfragger.num_enzyme_termini=2；有依据时可将 missed cleavages 调到 3-4，并收紧 digest mass/length 范围。\n\n"
        "Semantic enzyme inference: do not rely only on literal enzyme tokens. Interpret protocol language "
        "using proteomics domain knowledge. For example, if the protocol says a lysine-specific endoproteinase, "
        "lysine-directed endoprotease, or digestion at lysine residues was used together with trypsin, infer "
        "enzyme.value='Trypsin/Lys-C' with high confidence only when the evidence supports both enzymes. For "
        "Trypsin/Lys-C, workflow_parameter_overrides must set slot 1 to stricttrypsin/KR/C, slot 2 to Lys-C/K/C, "
        "and msfragger.num_enzyme_termini=2.\n\n"
        "## DDA/DIA 判断（最关键）\n\n"
        "你必须根据 SDRF 行中的信息判断数据采集模式（acquisition_mode）是 DDA 还是 DIA：\n\n"
        "### 判断依据\n"
        "1. SDRF 中的 'comment[data acquisition method]' 字段\n"
        "2. SDRF 中的 'comment[instrument]' 字段\n"
        "3. Isolation Window 信息：\n"
        "   - DDA: Isolation Window 较窄（1-3 m/z），每次选择少数高强度离子碎裂\n"
        "   - DIA: Isolation Window 较宽（10-50 m/z），覆盖大范围 m/z\n"
        "4. 文件名中的线索（'DIA'/'SWATH'/'diaPASEF' 通常是 DIA）\n\n"
        "### DDA 亚型判断\n"
        "- **LFQ**: labeling_strategy 为 'none'/空\n"
        "- **TMT**: labeling_strategy 含 'TMT'，区分 TMT6/TMT10/TMT16\n"
        "- **iTRAQ**: labeling_strategy 含 'iTRAQ'，区分 iTRAQ4/iTRAQ8\n"
        "- **SILAC**: labeling_strategy 含 'SILAC'\n"
        "- **磷酸化**: variable_mods 含 'Phospho'\n"
        "- **泛素化**: variable_mods 含 'GlyGly'/'ubiquitin'\n"
        "- **乙酰化**: variable_mods 含 'Acetyl'\n"
        "- **糖基化**: variable_mods 含 'HexNAc'/'glyco'\n\n"
        "## 可用 Workflows（必须从以下列表中选择）\n\n"
        "### DDA LFQ:\n"
        "  Default.workflow, LFQ-MBR.workflow, LFQ-phospho.workflow, LFQ-ubiquitin.workflow\n"
        "### DDA TMT:\n"
        "  TMT10.workflow, TMT10-MS3.workflow, TMT10-bridge.workflow, TMT10-phospho.workflow,\n"
        "  TMT10-MS3-phospho.workflow, TMT10-phospho-bridge.workflow, TMT10-acetyl.workflow,\n"
        "  TMT10-ubiquitin.workflow, TMT16.workflow, TMT16-MS3.workflow, TMT16-phospho.workflow,\n"
        "  TMT16-acetyl.workflow\n"
        "### DDA iTRAQ:\n"
        "  iTRAQ4.workflow, iTRAQ4-phospho.workflow\n"
        "### DDA SILAC:\n"
        "  SILAC3.workflow, SILAC3-phospho.workflow\n"
        "### DIA（注意：当前系统暂不支持 DIA，会阻断运行）:\n"
        "  DIA_SpecLib_Quant.workflow, DIA_SpecLib_Quant_Phospho.workflow, DIA_DIA-Umpire_SpecLib_Quant.workflow\n"
        "### 非特异酶切:\n"
        "  Nonspecific-HLA.workflow, Nonspecific-HLA-C57.workflow, Nonspecific-HLA-TMT10.workflow,\n"
        "  Nonspecific-HLA-phospho.workflow, Nonspecific-HLA-glyco.workflow, Nonspecific-peptidome.workflow\n"
        "### 特殊:\n"
        "  Open.workflow, FPOP.workflow, glyco-N-LFQ.workflow, glyco-N-TMT.workflow\n\n"
        "## 输出要求\n"
        "- acquisition_mode: 'DDA' 或 'DIA'（必须明确）\n"
        "- recommended_workflow_name: 必须从上述列表中选择\n"
        "- workflow_parameter_overrides: 必须放在 search_parameter_hints.value 中，使用精确 FragPipe key，例如 msfragger.search_enzyme_name_2。\n"
        "- Use source='llm_confirmed'. Prefer normalized values over raw NT=/AC= strings.\n"
        "- Do not invent parameters not grounded in SDRF rows or metadata.\n\n"
        f"Project context:\n{_metadata_context_text(context, include_project_files=False)}\n\n"
        f"Matched SDRF rows:\n{json.dumps(context.sdrf_rows, ensure_ascii=False)}\n\n"
        f"Current attributes:\n{json.dumps(current, ensure_ascii=False)}"
    )


class OpenAICompatibleReasoner:
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-v4-flash",
        base_url: str = "https://api.deepseek.com",
        timeout: float = 300.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post_chat_completion(self, payload: dict[str, Any]) -> httpx.Response:
        last_error: Exception | None = None
        for _ in range(3):
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
            try:
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status_code = exc.response.status_code
                if status_code < 500:
                    raise
                time.sleep(1)
        if last_error is not None:
            raise last_error
        raise RuntimeError("大模型请求失败：未收到 HTTP 响应。")

    def _stream_chat_completion(self, payload: dict[str, Any], report: Callable | None = None) -> str:
        # 暂停 spinner，避免 \r 覆盖流式输出
        if report is not None:
            report({"kind": "activity_stop", "message": ""})
        stream_payload = {**payload, "stream": True}
        # 流式模式：connect/read/write 用不同超时，read 要足够长（模型思考可能很久）
        stream_timeout = httpx.Timeout(connect=30, read=self.timeout, write=30, pool=30)
        last_error: Exception | None = None
        for attempt in range(3):
            full_content = ""
            full_reasoning = ""
            _debug_write(f"[调试] 开始流式请求 attempt={attempt+1}, model={self.model}, timeout={self.timeout}s\n")
            try:
                with httpx.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=stream_payload,
                    timeout=stream_timeout,
                ) as response:
                    _debug_write(f"[调试] 已连接, status={response.status_code}\n")
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk["choices"][0].get("delta", {})
                            reasoning = delta.get("reasoning_content", "")
                            content = delta.get("content", "")
                            if reasoning:
                                full_reasoning += reasoning
                                _debug_write(f"\033[90m{reasoning}\033[0m")
                            if content:
                                full_content += content
                                _debug_write(content)
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                if full_reasoning or full_content:
                    _debug_write("\n")
                return full_content
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code < 500:
                    raise
                _debug_write(f"\n[重试 {attempt+1}/3] 服务器错误 {exc.response.status_code}，重试中...\n")
                time.sleep(2)
            except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                last_error = exc
                _debug_write(f"\n[重试 {attempt+1}/3] 请求超时，重试中...\n")
                time.sleep(2)
        if last_error is not None:
            raise last_error
        raise RuntimeError("大模型流式请求失败：未收到响应。")

    def confirm_search_parameters(
        self,
        context: ProjectContext,
        attributes: AttributeSet,
    ) -> Mapping[str, AttributeValue]:
        prompt = _sdrf_attribute_prompt(context, attributes) if context.sdrf_rows else _no_sdrf_attribute_prompt(context, attributes)
        _debug_write(f"\n[调试] prompt 长度={len(prompt)} 字符\n")
        _debug_write(f"[调试] prompt 内容:\n{'='*60}\n{prompt}\n{'='*60}\n")
        payload: dict[str, Any] = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是蛋白质组学数据分析专家。返回 FLAT JSON 对象（不要嵌套）。\n"
                        "绝对不要把结果包装在 'deterministic_attributes' 或 'sage_config' 等嵌套对象中。\n"
                        "直接返回顶层 key。\n\n"
                        "最关键的任务：\n"
                        "1. 判断 acquisition_mode 是 'DDA' 还是 'DIA'\n"
                        "2. 根据 DDA/DIA 亚型选择正确的 recommended_workflow_name\n"
                        "3. DDA 亚型包括：LFQ、TMT（6/10/16）、iTRAQ（4/8）、SILAC，以及修饰类型（phospho/ubiquitin/acetyl/glyco）\n"
                        "4. recommended_workflow_name 必须是 profiles/fragpipe/ 目录中存在的文件名"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        try:
            content = self._stream_chat_completion(payload)
        except httpx.HTTPStatusError as exc:
            _debug_write(f"[调试] JSON模式失败 status={exc.response.status_code}, 尝试无JSON模式\n")
            if exc.response.status_code < 500:
                raise
            fallback_payload = dict(payload)
            fallback_payload.pop("response_format", None)
            content = self._stream_chat_completion(fallback_payload)
        decoded = json.loads(content)
        return {
            key: attribute
            for key, value in decoded.items()
            if (attribute := _coerce_attribute(value)) is not None
        }


def _llm_timeout_from_env(default: float = 300.0) -> float:
    raw_timeout = os.getenv("AGENT_LLM_TIMEOUT")
    if not raw_timeout:
        return default
    try:
        timeout = float(raw_timeout)
    except ValueError:
        return default
    return timeout if timeout > 0 else default


def default_llm_reasoner() -> LLMReasoner | None:
    api_key = os.getenv("AGENT_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    model = os.getenv("AGENT_LLM_MODEL", "deepseek-v4-flash")
    base_url = os.getenv("AGENT_LLM_BASE_URL", "https://api.deepseek.com")
    timeout = _llm_timeout_from_env()
    return OpenAICompatibleReasoner(api_key=api_key, model=model, base_url=base_url, timeout=timeout)


def _mark_no_sdrf_llm_blocked(attributes: AttributeSet, reason: str) -> AttributeSet:
    current = attributes.search_parameter_hints.value
    hints = dict(current) if isinstance(current, Mapping) else {}
    hints["llm_confirmation_error"] = reason
    blocked_hints = AttributeValue(
        value=hints,
        confidence=0.0,
        source="llm_required",
        evidence_excerpt=reason,
        conflict_flag=True,
    )
    return attributes.model_copy(update={"search_parameter_hints": blocked_hints})


def confirm_no_sdrf_parameters(
    context: ProjectContext,
    attributes: AttributeSet,
    llm_reasoner: LLMReasoner | None = None,
    report: ReportFn | None = None,
) -> AttributeSet:
    if context.sdrf_rows:
        return attributes

    if report is not None:
        report("未找到 SDRF 行；将结合 repository 元数据、项目描述、协议、文件名、证据文档和参数/FASTA 文件线索推断搜库参数。")

    reasoner = llm_reasoner or default_llm_reasoner()
    if reasoner is None:
        raise ValueError(
            "必须配置大模型 API 才能运行。未找到 SDRF 行时需要大模型推断搜库参数。\n"
            "请设置环境变量 AGENT_LLM_API_KEY。\n"
            "示例配置：\n"
            "  AGENT_LLM_API_KEY=your_deepseek_api_key\n"
            "  AGENT_LLM_BASE_URL=https://api.deepseek.com\n"
            "  AGENT_LLM_MODEL=deepseek-v4-flash"
        )

    if report is not None:
        report("\u6b63\u5728\u8c03\u7528\u5927\u6a21\u578b\u786e\u8ba4\u6587\u4ef6\u5c5e\u6027\u548c\u641c\u5e93\u53c2\u6570\u3002")

    try:
        if report is not None:
            report("大模型正在阅读 PRIDE 元数据并生成搜库参数…")
        updates = reasoner.confirm_search_parameters(context, attributes)
    except Exception as exc:
        reason = f"无 SDRF 输入时大模型确认失败：{exc}"
        if report is not None:
            report(f"大模型确认失败；未找到 SDRF 时不再使用规则猜测搜库参数，请人工复核。原因={exc}")
        return _mark_no_sdrf_llm_blocked(attributes, reason)
    merged = attributes.model_dump()
    # LLM 可能返回 search_parameter_hints 子字段作为顶层 key
    _hint_keys = {
        "recommended_workflow_name",
        "recommended_fasta_name",
        "recommended_fasta_url",
        "recommended_fasta_source",
        "workflow_rationale",
        "workflow_parameter_overrides",
        "fragpipe_workflow_overrides",
        "msfragger_parameter_overrides",
        "database",
    }
    extra_hints: dict[str, Any] = {}
    for key in _hint_keys:
        if key in updates and key not in AttributeSet.model_fields:
            val = updates[key]
            if isinstance(val, dict) and "value" in val:
                extra_hints[key] = val["value"]
            else:
                extra_hints[key] = val
    if extra_hints:
        current_hints = merged.get("search_parameter_hints", {})
        if isinstance(current_hints, dict) and isinstance(current_hints.get("value"), dict):
            current_hints["value"] = {**current_hints["value"], **extra_hints}
        else:
            current_hints = {"value": extra_hints, "confidence": 0.9, "source": "llm_confirmed",
                             "evidence_excerpt": "", "conflict_flag": False}
        merged["search_parameter_hints"] = current_hints
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
    result = complete_enzyme_workflow_overrides(result)

    if report is not None:
        report("\u5927\u6a21\u578b\u786e\u8ba4\u7ed3\u679c\u5df2\u5408\u5e76\u5230\u5c5e\u6027\u63a8\u65ad\u4e2d\u3002")
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
        raise ValueError(
            "必须配置大模型 API 才能运行。有 SDRF 行时需要大模型汇总 workflow 属性。\n"
            "请设置环境变量 AGENT_LLM_API_KEY。\n"
            "示例配置：\n"
            "  AGENT_LLM_API_KEY=your_deepseek_api_key\n"
            "  AGENT_LLM_BASE_URL=https://api.deepseek.com\n"
            "  AGENT_LLM_MODEL=deepseek-v4-flash"
        )

    if report is not None:
        report(f"\u627e\u5230\u5339\u914d\u7684 SDRF \u884c\uff08{len(context.sdrf_rows)} \u884c\uff09\uff1b\u6b63\u5728\u7528\u5927\u6a21\u578b\u6c47\u603b\u6587\u4ef6\u7ea7 workflow \u5c5e\u6027\u3002")

    try:
        if report is not None:
            report("大模型正在汇总 SDRF 行和 workflow 属性…")
        updates = reasoner.confirm_search_parameters(context, attributes)
    except Exception as exc:
        if report is not None:
            report(f"\u5927\u6a21\u578b SDRF \u6c47\u603b\u5931\u8d25\uff1b\u4fdd\u7559\u786e\u5b9a\u6027 SDRF \u63a8\u65ad\u7ed3\u679c\u3002\u539f\u56e0={exc}")
        return attributes

    merged = attributes.model_dump()
    # LLM 可能返回 search_parameter_hints 子字段（如 recommended_workflow_name）作为顶层 key
    _hint_keys = {
        "recommended_workflow_name",
        "recommended_fasta_name",
        "recommended_fasta_url",
        "recommended_fasta_source",
        "workflow_rationale",
        "workflow_parameter_overrides",
        "fragpipe_workflow_overrides",
        "msfragger_parameter_overrides",
        "database",
    }
    extra_hints: dict[str, Any] = {}
    for key in _hint_keys:
        if key in updates and key not in AttributeSet.model_fields:
            val = updates[key]
            if isinstance(val, dict) and "value" in val:
                extra_hints[key] = val["value"]
            else:
                extra_hints[key] = val
    if extra_hints:
        current_hints = merged.get("search_parameter_hints", {})
        if isinstance(current_hints, dict) and isinstance(current_hints.get("value"), dict):
            current_hints["value"] = {**current_hints["value"], **extra_hints}
        else:
            current_hints = {"value": extra_hints, "confidence": 0.9, "source": "llm_confirmed",
                             "evidence_excerpt": "", "conflict_flag": False}
        merged["search_parameter_hints"] = current_hints
    for field_name, proposed_value in updates.items():
        if field_name not in AttributeSet.model_fields:
            continue
        proposed = _coerce_attribute(proposed_value)
        if proposed is None:
            continue
        if _is_missing(proposed.value):
            continue
        current = getattr(attributes, field_name)
        if field_name == "search_parameter_hints":
            merged[field_name] = _merge_attribute(current, proposed)
        elif proposed.confidence >= 0.85:
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
    result = complete_enzyme_workflow_overrides(result)

    if report is not None:
        report("\u5927\u6a21\u578b SDRF \u6c47\u603b\u7ed3\u679c\u5df2\u5408\u5e76\u5230\u5c5e\u6027\u63a8\u65ad\u4e2d\u3002")
    return result
