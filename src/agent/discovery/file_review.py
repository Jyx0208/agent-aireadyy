from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from hashlib import sha256
from typing import Any

from agent.discovery.agentic import DiscoveryLLMClient
from agent.discovery.file_judgment import (
    FileJudgmentInput,
    check_file_evidence,
)
from agent.discovery.models import DatasetRequest, DiscoveredFile, DiscoveredProject


DEFAULT_FILE_REVIEW_BATCH_SIZE = 30


def file_evidence_values(file: DiscoveredFile) -> dict[str, Any]:
    values: dict[str, Any] = {
        f"{file.file_id}:identity": {
            "repository": file.repository,
            "project_accession": file.project_accession,
            "file_name": file.file_name,
            "native_id": file.file_accession_or_path,
        },
        f"{file.file_id}:repository_record": file.raw_record,
    }
    for index, evidence in enumerate(file.evidence):
        values[f"{file.file_id}:evidence:{index}"] = evidence.model_dump(mode="json")
    return values


def file_review_summary(file: DiscoveredFile) -> dict[str, Any]:
    evidence = file_evidence_values(file)
    return {
        "file_id": file.file_id,
        "project_accession": file.project_accession,
        "file_name": file.file_name,
        "file_type": file.file_type,
        "file_role": file.file_role,
        "selection_role": file.selection_role,
        "family_id": file.family_id,
        "companion_file_ids": file.companion_file_ids,
        "size_bytes": file.expected_size_bytes,
        "acquisition_mode": file.acquisition_mode,
        "species": file.species,
        "sdrf_match_status": file.sdrf_match_status,
        "validity_status": file.validity_status,
        "task_readiness_status": file.task_readiness_status,
        "limitations": file.evidence_warnings,
        "evidence": evidence,
        "available_evidence_refs": list(evidence),
    }


def strategy_hash(request: DatasetRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def evidence_hash(file: DiscoveredFile) -> str:
    payload = json.dumps(
        file_evidence_values(file),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _chunks(items: list[DiscoveredFile], size: int) -> Iterable[list[DiscoveredFile]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _project_context(
    projects: Mapping[str, DiscoveredProject],
    files: list[DiscoveredFile],
) -> list[dict[str, Any]]:
    accessions = {file.project_accession.upper() for file in files}
    return [
        {
            "project_accession": project.project_accession,
            "title": project.project_title,
            "description": project.project_description,
            "species": project.species,
            "acquisition_mode": project.acquisition_mode,
            "sdrf": project.sdrf_summary,
        }
        for accession, project in projects.items()
        if accession in accessions
    ]


def _structural_prompt(
    request: DatasetRequest,
    projects: Mapping[str, DiscoveredProject],
    files: list[DiscoveredFile],
) -> str:
    return (
        "Review every file independently for the requested scientific task. Project metadata is "
        "shared context, never automatic permission to include all files. Return JSON with a "
        "judgments array and exactly one item per file_id. Use only supplied evidence refs. "
        "Include requires grade 2 or 3, hard_gate pass, and direct file evidence. Missing essential "
        "information means investigate. Obvious non-input documents may be excluded briefly. "
        "For include, do not write the final long paragraph yet; give 1-4 short "
        "reason_outline items and reason_status=pending. For investigate or exclude, write a "
        "concise, explicit reason_text now and set reason_status=ready, so every reviewed file "
        "has a visible reason.\n\n"
        "Each judgment must contain: file_id, project_accession, file_name, selection_role "
        "(primary_input|required_companion|evidence_only), review_status=reviewed, decision "
        "(include|investigate|exclude), grade (0-3 or null), hard_gate (pass|unknown|fail), "
        "confidence, evidence_refs, limitations, missing_information, companion_file_ids, "
        "reason_outline, reason_text, reason_status.\n\n"
        f"Task JSON:\n{json.dumps(request.model_dump(mode='json'), ensure_ascii=False)}\n\n"
        f"Project context JSON:\n{json.dumps(_project_context(projects, files), ensure_ascii=False)}\n\n"
        f"File evidence JSON:\n{json.dumps([file_review_summary(file) for file in files], ensure_ascii=False)}"
    )


def _reason_prompt(
    request: DatasetRequest,
    files: list[DiscoveredFile],
    judgments: list[FileJudgmentInput],
) -> str:
    return (
        "Write one coherent Chinese selection-reason paragraph for every supplied selected file. "
        "Each paragraph must explain why the exact file fits the task, the evidence used, required "
        "companions, and material limitations. Do not copy one project paragraph across files and "
        "do not invent facts. Return JSON {reasons:[{file_id, reason_text}]}.\n\n"
        f"Task JSON:\n{json.dumps(request.model_dump(mode='json'), ensure_ascii=False)}\n\n"
        f"Selected files JSON:\n{json.dumps([file_review_summary(file) for file in files], ensure_ascii=False)}\n\n"
        f"Structural judgments JSON:\n{json.dumps([item.model_dump(mode='json') for item in judgments], ensure_ascii=False)}"
    )


def _parse_structural_batch(
    payload: Mapping[str, Any],
    files: list[DiscoveredFile],
    request: DatasetRequest,
    model_id: str,
) -> dict[str, FileJudgmentInput]:
    file_by_id = {file.file_id: file for file in files}
    parsed: dict[str, FileJudgmentInput] = {}
    rows = payload.get("judgments")
    if not isinstance(rows, list):
        return parsed
    request_hash = strategy_hash(request)
    for row in rows:
        if not isinstance(row, dict):
            continue
        file = file_by_id.get(str(row.get("file_id") or ""))
        if file is None:
            continue
        candidate = FileJudgmentInput.model_validate(
            {
                **row,
                "file_id": file.file_id,
                "project_accession": file.project_accession,
                "file_name": file.file_name,
                "selection_role": file.selection_role,
                "family_id": file.family_id,
                "companion_file_ids": list(
                    dict.fromkeys(
                        [
                            *file.companion_file_ids,
                            *(row.get("companion_file_ids") or []),
                        ]
                    )
                ),
                "strategy_hash": request_hash,
                "evidence_hash": evidence_hash(file),
                "model_id": model_id,
                "reason_scope": "file",
                "judgment_source": "llm",
            }
        )
        evidence_check = check_file_evidence(candidate, file_evidence_values(file))
        if evidence_check.status == "fail":
            candidate = candidate.model_copy(
                update={
                    "decision": "investigate",
                    "grade": None,
                    "hard_gate": "unknown",
                    "missing_information": [
                        *candidate.missing_information,
                        "模型引用了不存在的文件证据",
                    ],
                    "reason_text": "暂不纳入：模型引用的文件证据不存在，需要重新核对后再判断。",
                    "reason_status": "ready",
                }
            )
        parsed[file.file_id] = candidate
    return parsed


def review_files_in_batches(
    client: DiscoveryLLMClient,
    *,
    request: DatasetRequest,
    projects: Iterable[DiscoveredProject],
    files: Iterable[DiscoveredFile],
    batch_size: int = DEFAULT_FILE_REVIEW_BATCH_SIZE,
) -> dict[str, FileJudgmentInput]:
    """Run one compact structural LLM review per file, retrying only omissions."""

    file_list = list(files)
    project_map = {project.project_accession.upper(): project for project in projects}
    model_id = str(getattr(client, "model", "configured-llm"))
    judgments: dict[str, FileJudgmentInput] = {}
    for batch in _chunks(file_list, max(1, min(int(batch_size), 50))):
        payload = client.complete_json(
            system_prompt="You are a scientific proteomics file-selection reviewer.",
            user_prompt=_structural_prompt(request, project_map, batch),
        )
        parsed = _parse_structural_batch(payload, batch, request, model_id)
        missing = [file for file in batch if file.file_id not in parsed]
        if missing:
            retry_payload = client.complete_json(
                system_prompt="Return judgments only for the omitted file IDs.",
                user_prompt=_structural_prompt(request, project_map, missing),
            )
            parsed.update(
                _parse_structural_batch(retry_payload, missing, request, model_id)
            )
        judgments.update(parsed)
    return judgments


def generate_selected_file_reasons(
    client: DiscoveryLLMClient,
    *,
    request: DatasetRequest,
    files: Iterable[DiscoveredFile],
    judgments: Mapping[str, FileJudgmentInput],
    batch_size: int = DEFAULT_FILE_REVIEW_BATCH_SIZE,
) -> dict[str, FileJudgmentInput]:
    """Generate long text only for structurally selected files."""

    selected = [
        file
        for file in files
        if judgments.get(file.file_id) is not None
        and judgments[file.file_id].decision == "include"
    ]
    updated = dict(judgments)
    for batch in _chunks(selected, max(1, min(int(batch_size), 50))):
        pending = list(batch)
        for _attempt in range(2):
            if not pending:
                break
            payload = client.complete_json(
                system_prompt="Write evidence-grounded Chinese file-level reasons.",
                user_prompt=_reason_prompt(
                    request,
                    pending,
                    [updated[file.file_id] for file in pending],
                ),
            )
            rows = payload.get("reasons")
            if not isinstance(rows, list):
                continue
            received: set[str] = set()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                file_id = str(row.get("file_id") or "")
                reason_text = " ".join(str(row.get("reason_text") or "").split())
                if file_id not in {file.file_id for file in pending} or not reason_text:
                    continue
                updated[file_id] = updated[file_id].model_copy(
                    update={"reason_text": reason_text, "reason_status": "ready"}
                )
                received.add(file_id)
            pending = [file for file in pending if file.file_id not in received]
        for file in pending:
            updated[file.file_id] = updated[file.file_id].model_copy(
                update={"reason_status": "error"}
            )
    return updated
