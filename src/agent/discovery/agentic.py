from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol

import httpx
from pydantic import Field

from agent.discovery.models import DatasetManifest, DatasetRequest
from agent.discovery.query_builder import build_pride_queries
from agent.discovery.task_profiles import TaskProfile
from agent.models import JsonModel


class DiscoveryTaskSpec(JsonModel):
    task_type: str = "ptm_discovery"
    target_ptm: str | None = "phospho"
    species_include: list[str] = Field(default_factory=list)
    acquisition_mode: str | None = "dda"
    labeling_strategy: str = "label_free"
    modification_scope: str | None = "phospho"
    immunopeptide_scope: str | None = None
    hla_class: list[str] = Field(default_factory=list)
    hla_alleles: list[str] = Field(default_factory=list)
    diversity_objectives: list[str] = Field(default_factory=list)
    required_evidence_level: str = "mixed_or_file"
    required_labels: list[str] = Field(default_factory=list)
    required_metadata: list[str] = Field(default_factory=list)
    next_pipeline_steps: list[str] = Field(default_factory=list)
    ai_ready_target_schema: str | None = None
    task_profile_status: str | None = None
    notes: list[str] = Field(default_factory=list)


class AgenticQuery(JsonModel):
    query: str
    purpose: str = ""


class AgenticTraceStep(JsonModel):
    step: str
    thought: str
    action: str | None = None
    observation: str | None = None


class AgenticDiscoveryPlan(JsonModel):
    request: DatasetRequest
    task_spec: DiscoveryTaskSpec
    queries: list[str] = Field(default_factory=list)
    query_notes: list[AgenticQuery] = Field(default_factory=list)
    trace: list[AgenticTraceStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggested_next_queries: list[str] = Field(default_factory=list)


class DiscoveryLLMClient(Protocol):
    def complete_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Return a JSON object from an LLM completion."""


class OpenAICompatibleDiscoveryLLM:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
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
        return _coerce_json_object(content)


def default_discovery_llm_client() -> DiscoveryLLMClient | None:
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("AGENT_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    model = os.getenv("DEEPSEEK_MODEL") or os.getenv("AGENT_LLM_MODEL") or "deepseek-chat"
    base_url = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("AGENT_LLM_BASE_URL") or "https://api.deepseek.com"
    timeout = _float_env("DEEPSEEK_TIMEOUT") or _float_env("AGENT_LLM_TIMEOUT") or 120.0
    return OpenAICompatibleDiscoveryLLM(api_key=api_key, model=model, base_url=base_url, timeout=timeout)


def _float_env(name: str) -> float | None:
    raw_value = os.getenv(name)
    if not raw_value:
        return None
    try:
        value = float(raw_value)
    except ValueError:
        return None
    return value if value > 0 else None


def _coerce_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        decoded = json.loads(match.group(0))
    if not isinstance(decoded, dict):
        raise ValueError("LLM response must be a JSON object.")
    return decoded


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _safe_task_spec(
    payload: dict[str, Any],
    request: DatasetRequest,
    task_profile: TaskProfile | None = None,
) -> DiscoveryTaskSpec:
    raw = payload.get("task_spec") if isinstance(payload.get("task_spec"), dict) else {}
    request_ptm_is_unknown = str(request.ptm_type or "").casefold() in {"", "unknown_ptm", "unknown", "any"}
    default_task_type = task_profile.task_type if task_profile else ("general_discovery" if request.goal == "general" and request_ptm_is_unknown else "ptm_discovery")
    target_ptm = None if request.goal == "general" and request_ptm_is_unknown else request.ptm_type
    modification_scope = None if request.goal == "general" and request_ptm_is_unknown else (request.modification_scope or request.ptm_type)
    resolved_target_ptm = target_ptm or str(raw.get("target_ptm") or "") or None
    resolved_modification_scope = modification_scope or str(raw.get("modification_scope") or "") or None
    resolved_species = list(request.species) or _safe_string_list(raw.get("species_include"), [])
    spec = DiscoveryTaskSpec(
        task_type=str(raw.get("task_type") or default_task_type),
        target_ptm=str(raw.get("target_ptm") or target_ptm) if (raw.get("target_ptm") or target_ptm) else None,
        species_include=[str(item) for item in raw.get("species_include", request.species) if str(item).strip()]
        if isinstance(raw.get("species_include", request.species), list)
        else list(request.species),
        acquisition_mode=str(raw.get("acquisition_mode") or request.acquisition_mode),
        labeling_strategy=str(raw.get("labeling_strategy") or request.labeling_strategy),
        modification_scope=str(raw.get("modification_scope") or modification_scope) if (raw.get("modification_scope") or modification_scope) else None,
        immunopeptide_scope=str(raw.get("immunopeptide_scope") or request.immunopeptide_scope or "") or None,
        hla_class=_safe_string_list(raw.get("hla_class"), request.hla_class),
        hla_alleles=_safe_string_list(raw.get("hla_alleles"), request.hla_alleles),
        diversity_objectives=[str(item) for item in raw.get("diversity_objectives", []) if str(item).strip()]
        if isinstance(raw.get("diversity_objectives", []), list)
        else [],
        required_evidence_level=str(raw.get("required_evidence_level") or "mixed_or_file"),
        required_labels=_safe_string_list(raw.get("required_labels"), task_profile.required_labels if task_profile else []),
        required_metadata=_safe_string_list(raw.get("required_metadata"), task_profile.required_metadata if task_profile else []),
        next_pipeline_steps=_safe_string_list(raw.get("next_pipeline_steps"), task_profile.next_pipeline_steps if task_profile else []),
        ai_ready_target_schema=str(raw.get("ai_ready_target_schema") or (task_profile.ai_ready_target_schema if task_profile else "") or "")
        or None,
        task_profile_status=task_profile.implementation_status if task_profile else None,
        notes=[str(item) for item in raw.get("notes", []) if str(item).strip()] if isinstance(raw.get("notes", []), list) else [],
    )
    return spec.model_copy(
        update={
            "task_type": task_profile.task_type if task_profile else spec.task_type,
            "target_ptm": resolved_target_ptm,
            "species_include": resolved_species,
            "acquisition_mode": request.acquisition_mode,
            "labeling_strategy": request.labeling_strategy,
            "modification_scope": resolved_modification_scope,
            "immunopeptide_scope": request.immunopeptide_scope,
            "hla_class": request.hla_class,
            "hla_alleles": request.hla_alleles,
        }
    )


def _safe_string_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    items = [str(item) for item in value if str(item).strip()]
    return items or list(fallback)


def _safe_query_notes(payload: dict[str, Any]) -> list[AgenticQuery]:
    raw_queries = payload.get("queries", [])
    notes: list[AgenticQuery] = []
    if isinstance(raw_queries, list):
        for item in raw_queries:
            if isinstance(item, dict):
                query = str(item.get("query") or "").strip()
                purpose = str(item.get("purpose") or "").strip()
            else:
                query = str(item or "").strip()
                purpose = ""
            if query:
                notes.append(AgenticQuery(query=query, purpose=purpose))
    return notes


def _safe_trace(payload: dict[str, Any]) -> list[AgenticTraceStep]:
    raw_trace = payload.get("trace", [])
    trace: list[AgenticTraceStep] = []
    if isinstance(raw_trace, list):
        for index, item in enumerate(raw_trace, start=1):
            if not isinstance(item, dict):
                continue
            trace.append(
                AgenticTraceStep(
                    step=str(item.get("step") or f"step_{index}"),
                    thought=str(item.get("thought") or ""),
                    action=str(item.get("action") or "") or None,
                    observation=str(item.get("observation") or "") or None,
                )
            )
    return trace


def _planner_system_prompt() -> str:
    return (
        "You are an agentic proteomics dataset discovery planner. "
        "Return only JSON. You do not decide whether files are valid; validators will do that. "
        "Your job is to transform a modeling/discovery request into PRIDE search queries and a concise reasoning trace. "
        "Species are open by default and should be treated as diversity/preference unless the user explicitly says only/exclude. "
        "Treat TMT/iTRAQ as weak-but-allowed labeling strategy evidence, not as unsupported acquisition. "
        "Use ontology-guided semantic PTM interpretation: normalize synonyms, enrichment methods, abbreviations, and context phrases "
        "such as phosphotyrosine enrichment, kinase signaling, Ti/Fe/Ga-IMAC, MOAC, PolyMAC, Titansphere, GlyGly/K-GG, Kac, HILIC, lectin, Kme/Rme. "
        "Use ontology-guided immunopeptidomics interpretation: normalize HLA/MHC ligandome, immunopeptidome, HLA/MHC eluted ligand, "
        "neoantigen, antigen presentation, HLA-IP/MHC-IP, W6/32, pan-HLA, HLA class I/II, MHC class I/II, and HLA alleles such as HLA-A*02:01. "
        "DIA/PRM/SRM/MRM are unsupported for this DDA-first workflow and should be surfaced as warnings, not silently mapped to DDA."
    )


def _planner_user_prompt(
    prompt: str,
    request: DatasetRequest,
    task_profile: TaskProfile | None = None,
) -> str:
    baseline_queries = build_pride_queries(request)
    task_profile_text = (
        "\nModeling task profile JSON:\n"
        + json.dumps(task_profile.model_dump(mode="json"), ensure_ascii=False)
        + "\n"
        if task_profile is not None
        else ""
    )
    return (
        "Create a PRIDE dataset discovery query plan.\n\n"
        f"User request:\n{prompt.strip()}\n\n"
        f"Hard request constraints JSON:\n{json.dumps(request.model_dump(mode='json'), ensure_ascii=False)}\n\n"
        f"{task_profile_text}"
        f"Baseline deterministic queries:\n{json.dumps(baseline_queries, ensure_ascii=False)}\n\n"
        "Return JSON with keys:\n"
        "- task_spec: {task_type, target_ptm, species_include, acquisition_mode, labeling_strategy, modification_scope, immunopeptide_scope, hla_class, hla_alleles, diversity_objectives, required_evidence_level, required_labels, required_metadata, next_pipeline_steps, ai_ready_target_schema, notes}\n"
        "- queries: list of {query, purpose}; include concise PRIDE search strings only\n"
        "- trace: list of {step, thought, action, observation}\n"
        "- warnings: list of short strings\n\n"
        "Keep hard constraints unchanged. Species are not limited to a fixed core set; include requested species as query seeds, "
        "but do not exclude other species unless the user explicitly requests include_only or exclude behavior. "
        "Supported PTM scopes include phospho, acetyl, ubiquitin/GlyGly, glyco, methyl, or unknown_ptm. "
        "If the request is HLA/MHC ligandome, immunopeptidomics, eluted ligand, or neoantigen oriented, use goal=immunopeptidomics semantics and query HLA/MHC ligand terms instead of forcing a PTM scope. "
        "For phospho include semantic clusters for phosphoproteome/phosphopeptide enrichment, pSer/pThr/pTyr, kinase signaling, "
        "phosphosite localization, Ti/Fe/Ga/Ti4+-IMAC, MOAC, PolyMAC, Titansphere, titanium dioxide beads, and phosphotyrosine antibody enrichment. "
        "Prefer diverse evidence terms such as PTM enrichment, acquisition mode, labeling strategy, species aliases, "
        "instrument or fragmentation terms when useful. Do not include download or search execution steps."
    )


class AgenticDiscoveryPlanner:
    def __init__(self, llm_client: DiscoveryLLMClient) -> None:
        self.llm_client = llm_client

    def plan(
        self,
        *,
        prompt: str,
        request: DatasetRequest,
        task_profile: TaskProfile | None = None,
    ) -> AgenticDiscoveryPlan:
        payload = self.llm_client.complete_json(
            system_prompt=_planner_system_prompt(),
            user_prompt=_planner_user_prompt(prompt, request, task_profile=task_profile),
        )
        task_spec = _safe_task_spec(payload, request, task_profile=task_profile)
        query_notes = _safe_query_notes(payload)
        llm_queries = [item.query for item in query_notes]
        baseline_request = request
        if task_spec.target_ptm and str(request.ptm_type or "").casefold() in {"", "unknown_ptm", "unknown", "any"}:
            baseline_request = request.model_copy(update={"goal": "ptm", "ptm_type": task_spec.target_ptm})
        deterministic_queries = build_pride_queries(baseline_request)
        queries = _dedupe([*llm_queries, *deterministic_queries])[: max(8, min(40, request.max_candidate_projects))]
        warnings = [str(item) for item in payload.get("warnings", []) if str(item).strip()] if isinstance(payload.get("warnings", []), list) else []
        trace = _safe_trace(payload)
        if not trace:
            trace = [
                AgenticTraceStep(
                    step="initial_query_plan",
                    thought="Generated PRIDE search queries from the user request and deterministic baseline.",
                    action="plan_queries",
                )
            ]
        return AgenticDiscoveryPlan(
            request=request,
            task_spec=task_spec,
            queries=queries,
            query_notes=query_notes,
            trace=trace,
            warnings=warnings,
        )


def default_agentic_discovery_planner() -> AgenticDiscoveryPlanner | None:
    client = default_discovery_llm_client()
    return AgenticDiscoveryPlanner(client) if client is not None else None


def build_agentic_self_check(plan: AgenticDiscoveryPlan, manifest: DatasetManifest) -> AgenticDiscoveryPlan:
    summary = manifest.summary
    warnings = list(plan.warnings)
    suggested_queries: list[str] = []
    trace = list(plan.trace)

    selected_files = int(summary.get("selected_files") or len(manifest.files))
    selected_projects = int(summary.get("selected_projects") or len(manifest.projects))
    if selected_files == 0 or selected_projects == 0:
        warnings.append("no_selected_files")
        suggested_queries.extend(build_pride_queries(plan.request))

    unknown_counts = summary.get("unknown_counts") if isinstance(summary.get("unknown_counts"), dict) else {}
    if int(unknown_counts.get("fragmentation_method") or 0) > selected_files / 2:
        warnings.append("fragmentation_diversity_or_metadata_weak")
        if plan.task_spec.target_ptm != "phospho":
            bases = plan.request.query_terms[:3] or ["proteomics"]
            suggested_queries.extend([f"{base} HCD" for base in bases])
            suggested_queries.extend([f"{base} CID" for base in bases[:2]])
        else:
            suggested_queries.extend(["phosphoproteomics HCD", "phosphoproteomics CID", "phosphoproteomics ETD"])

    evidence_distribution = summary.get("evidence_level_distribution") if isinstance(summary.get("evidence_level_distribution"), dict) else {}
    project_level_count = int(evidence_distribution.get("project") or 0)
    if selected_files and project_level_count > selected_files / 3:
        warnings.append("project_level_evidence_overrepresented")
        if plan.task_spec.target_ptm != "phospho":
            bases = plan.request.query_terms[:3] or ["proteomics"]
            suggested_queries.extend([f"{species} {base} raw" for species in plan.request.species for base in bases[:2]])
        else:
            suggested_queries.extend([f"{species} phospho raw" for species in plan.request.species])

    instrument_distribution = summary.get("instrument_family_distribution") if isinstance(summary.get("instrument_family_distribution"), dict) else {}
    if len(instrument_distribution) <= 1 and selected_files >= 10:
        warnings.append("instrument_diversity_low")
        if plan.task_spec.target_ptm != "phospho":
            base = (plan.request.query_terms or ["proteomics"])[0]
            suggested_queries.extend([f"{base} timsTOF", f"{base} Q Exactive", f"{base} Orbitrap Fusion"])
        else:
            suggested_queries.extend(["phosphoproteomics timsTOF", "phosphoproteomics Q Exactive", "phosphoproteomics Orbitrap Fusion"])

    trace.append(
        AgenticTraceStep(
            step="post_discovery_self_check",
            thought="Reviewed quality, evidence boundary, and diversity summary after discovery execution.",
            action="suggest_next_queries" if suggested_queries else "accept_current_manifest",
            observation=json.dumps(
                {
                    "selected_projects": selected_projects,
                    "selected_files": selected_files,
                    "validity": summary.get("validity_status_counts", {}),
                    "evidence_level_distribution": evidence_distribution,
                    "unknown_counts": unknown_counts,
                },
                ensure_ascii=False,
            ),
        )
    )
    return plan.model_copy(
        update={
            "trace": trace,
            "warnings": _dedupe(warnings),
            "suggested_next_queries": _dedupe(suggested_queries),
        }
    )
