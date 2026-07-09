from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from agent.ai_ready.input_locator import (
    AiReadyInputLocationResult,
    LocatedAiReadyInput,
    _inspect_file,
    _is_candidate_file,
    select_ai_ready_inputs,
)
from agent.models import JsonModel
from agent.utils import write_json


AgentRunArtifactRole = Literal[
    "task_state_json",
    "decision_trace_json",
    "run_manifest_json",
    "converter_config_json",
    "psm_table",
    "peptide_table",
    "pin_table",
    "peaklist_mgf",
    "fragpipe_pin",
    "msdt_parquet",
    "rawspectrum_parquet",
    "generic_ai_ready_parquet",
    "downloaded_acquisition",
]


class LocatedAgentRunArtifact(JsonModel):
    path: str
    artifact_role: AgentRunArtifactRole
    size_bytes: int = 0
    usable_for_tasks: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentRunInputLocationResult(JsonModel):
    status: str
    agent_run_dir: str
    output_dir: str
    artifacts: list[LocatedAgentRunArtifact] = Field(default_factory=list)
    ai_ready_inputs: list[LocatedAiReadyInput] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    json_path: str
    csv_path: str


def locate_agent_run_inputs(
    *,
    agent_run_dir: str | Path,
    output_dir: str | Path,
    max_input_file_mb: int = 2048,
    allow_large_input: bool = False,
) -> AgentRunInputLocationResult:
    agent_run_dir = Path(agent_run_dir)
    if not agent_run_dir.exists():
        raise ValueError(f"Agent run directory does not exist: {agent_run_dir}")
    if not agent_run_dir.is_dir():
        raise ValueError(f"--agent-run-dir must be a directory: {agent_run_dir}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    max_bytes = max(0, int(max_input_file_mb)) * 1024 * 1024
    artifacts: list[LocatedAgentRunArtifact] = []
    ai_ready_inputs: list[LocatedAiReadyInput] = []
    for path in sorted(agent_run_dir.rglob("*")):
        if not path.is_file():
            continue
        role = _agent_artifact_role(agent_run_dir, path)
        if role is None:
            continue
        size_bytes = _safe_size(path)
        warnings: list[str] = []
        if _is_table_or_peaklist_role(role) and not allow_large_input and max_bytes and size_bytes > max_bytes:
            warnings.append(f"input_file_too_large:{size_bytes}>{max_bytes}")
        artifact = LocatedAgentRunArtifact(
            path=str(path),
            artifact_role=role,
            size_bytes=size_bytes,
            usable_for_tasks=_tasks_for_role(role),
            warnings=warnings,
        )
        artifacts.append(artifact)
        if _is_table_or_peaklist_role(role) and not warnings and _is_candidate_file(path):
            inspected = _inspect_file(path)
            if inspected is not None:
                ai_ready_inputs.append(inspected)

    summary = _build_agent_run_summary(artifacts, ai_ready_inputs)
    status = "blocked" if not artifacts else "completed"
    json_path = output_dir / "agent_run_input_locations.json"
    csv_path = output_dir / "agent_run_input_locations.csv"
    result = AgentRunInputLocationResult(
        status=status,
        agent_run_dir=str(agent_run_dir),
        output_dir=str(output_dir),
        artifacts=artifacts,
        ai_ready_inputs=ai_ready_inputs,
        summary=summary,
        json_path=str(json_path),
        csv_path=str(csv_path),
    )
    write_json(json_path, result.model_dump(mode="json"))
    _write_agent_run_locations_csv(csv_path, artifacts)
    return result


def select_agent_run_ai_ready_inputs(
    result: AgentRunInputLocationResult,
    *,
    task_type: str | None = None,
) -> tuple[list[Path], list[Path]]:
    compatible = AiReadyInputLocationResult(
        status=result.status,
        search_dir=result.agent_run_dir,
        output_dir=result.output_dir,
        entries=result.ai_ready_inputs,
        summary=result.summary,
        json_path=result.json_path,
        csv_path=result.csv_path,
    )
    return select_ai_ready_inputs(compatible, task_type=task_type)


def _agent_artifact_role(root: Path, path: Path) -> AgentRunArtifactRole | None:
    rel_parts = tuple(part.casefold() for part in path.relative_to(root).parts)
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if name == "task_state.json":
        return "task_state_json"
    if name == "decision_trace.json":
        return "decision_trace_json"
    if name == "run_manifest.json":
        return "run_manifest_json"
    if name == "converter_config.json":
        return "converter_config_json"
    if rel_parts[:2] == ("assets", "downloads") and name.endswith(
        (".raw", ".raw.zip", ".mzml", ".mzml.gz", ".mzxml", ".wiff", ".d")
    ):
        return "downloaded_acquisition"
    if suffix == ".mgf":
        return "peaklist_mgf"
    if suffix == ".pin":
        return "fragpipe_pin" if "fragpipe" in rel_parts else "pin_table"
    if name in {"psm.tsv", "combined_psm.tsv"}:
        return "psm_table"
    if name in {"peptide.tsv", "combined_peptide.tsv"}:
        return "peptide_table"
    if suffix == ".parquet" and rel_parts[:1] == ("msdt",):
        return "msdt_parquet"
    if suffix == ".parquet" and rel_parts[:1] == ("rawspectrum",):
        return "rawspectrum_parquet"
    if suffix == ".parquet" and rel_parts[:1] == ("ai_ready",) and name.endswith("_ai_ready.parquet"):
        return "generic_ai_ready_parquet"
    return None


def _is_table_or_peaklist_role(role: str) -> bool:
    return role in {"psm_table", "peptide_table", "pin_table", "fragpipe_pin", "peaklist_mgf"}


def _tasks_for_role(role: str) -> list[str]:
    if role == "peptide_table":
        return ["rt_prediction"]
    if role == "psm_table":
        return ["rt_prediction", "fragment_intensity_prediction", "denovo", "ptm_denovo", "chimeric_interpretation"]
    if role in {"pin_table", "fragpipe_pin"}:
        return ["psm_scoring"]
    if role == "peaklist_mgf":
        return ["fragment_intensity_prediction", "denovo", "ptm_denovo", "chimeric_interpretation"]
    if role in {"msdt_parquet", "generic_ai_ready_parquet", "rawspectrum_parquet"}:
        return ["generic_ai_ready_available"]
    if role == "downloaded_acquisition":
        return ["needs_prepare_or_full"]
    return []


def _safe_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _build_agent_run_summary(
    artifacts: list[LocatedAgentRunArtifact],
    ai_ready_inputs: list[LocatedAiReadyInput],
) -> dict[str, Any]:
    by_role: dict[str, int] = {}
    total_bytes = 0
    for artifact in artifacts:
        by_role[artifact.artifact_role] = by_role.get(artifact.artifact_role, 0) + 1
        total_bytes += artifact.size_bytes
    generic = [item for item in artifacts if item.artifact_role in {"msdt_parquet", "generic_ai_ready_parquet"}]
    warnings = _dedupe([warning for artifact in artifacts for warning in artifact.warnings])
    return {
        "located_artifacts": len(artifacts),
        "role_counts": dict(sorted(by_role.items())),
        "downloaded_acquisition_count": by_role.get("downloaded_acquisition", 0),
        "search_result_count": sum(1 for item in ai_ready_inputs if item.file_role != "peaklist_mgf"),
        "peaklist_count": sum(1 for item in ai_ready_inputs if item.file_role == "peaklist_mgf"),
        "generic_ai_ready_available": bool(generic),
        "generic_ai_ready_count": len(generic),
        "total_artifact_bytes": total_bytes,
        "has_rt_table": any(item.has_rt for item in ai_ready_inputs if item.file_role != "peaklist_mgf"),
        "has_target_decoy_table": any(item.has_target_decoy for item in ai_ready_inputs if item.file_role != "peaklist_mgf"),
        "has_modified_sequence_table": any(item.has_modified_sequence for item in ai_ready_inputs if item.file_role != "peaklist_mgf"),
        "warnings": warnings,
    }


def _write_agent_run_locations_csv(path: Path, artifacts: list[LocatedAgentRunArtifact]) -> None:
    fieldnames = ["path", "artifact_role", "size_bytes", "usable_for_tasks", "warnings"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for artifact in artifacts:
            payload = artifact.model_dump(mode="json")
            writer.writerow(
                {
                    key: json.dumps(payload[key], ensure_ascii=False, sort_keys=True)
                    if isinstance(payload.get(key), (list, dict))
                    else payload.get(key, "")
                    for key in fieldnames
                }
            )


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
