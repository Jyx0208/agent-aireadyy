from __future__ import annotations

from hashlib import sha256
from pathlib import PurePath
from typing import Iterable, Literal

from pydantic import Field

from agent.discovery.file_judgment import FileJudgmentInput
from agent.models import JsonModel


FileRelationKind = Literal[
    "sdrf_explicit",
    "repository_native",
    "unique_basename",
    "run_fraction",
    "llm_confirmed",
]


class FileRelation(JsonModel):
    primary_file_id: str
    companion_file_id: str
    relation_kind: FileRelationKind
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)


class FileFamily(JsonModel):
    family_id: str
    project_accession: str
    primary_file_ids: list[str] = Field(default_factory=list)
    companion_file_ids: list[str] = Field(default_factory=list)
    evidence_file_ids: list[str] = Field(default_factory=list)
    relations: list[FileRelation] = Field(default_factory=list)
    status: Literal["complete", "investigate"] = "complete"
    limitations: list[str] = Field(default_factory=list)


def stable_family_id(project_accession: str, primary_file_ids: Iterable[str]) -> str:
    token = "\0".join(
        [str(project_accession or "").strip().upper(), *sorted(set(primary_file_ids))]
    )
    return f"family_{sha256(token.encode('utf-8')).hexdigest()[:20]}"


def normalized_run_name(file_name: str) -> str:
    name = PurePath(str(file_name or "").replace("\\", "/")).name.casefold()
    for suffix in (".sdrf.tsv", ".sdrf.txt", ".mzml.gz", ".raw", ".mzml", ".mgf", ".d"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return PurePath(name).stem


def freeze_file_selection(
    requested_file_ids: Iterable[str],
    judgments: dict[str, FileJudgmentInput],
    families: Iterable[FileFamily] = (),
) -> tuple[list[str], list[str]]:
    """Close a file selection over required companions without duplicating them."""

    selected = list(dict.fromkeys(str(value) for value in requested_file_ids))
    selected_set = set(selected)
    blockers: list[str] = []
    family_by_primary = {
        primary_id: family
        for family in families
        for primary_id in family.primary_file_ids
    }
    for file_id in list(selected):
        judgment = judgments.get(file_id)
        if judgment is None or judgment.decision != "include":
            blockers.append(f"file_not_included:{file_id}")
            continue
        family = family_by_primary.get(file_id)
        required = list(
            dict.fromkeys(
                [*judgment.companion_file_ids, *(family.companion_file_ids if family else [])]
            )
        )
        if family is not None and family.status == "investigate":
            blockers.append(f"file_family_requires_investigation:{file_id}")
            continue
        for companion_id in required:
            companion = judgments.get(companion_id)
            if companion is None or companion.decision != "include":
                blockers.append(f"required_companion_missing:{file_id}:{companion_id}")
                continue
            if companion_id not in selected_set:
                selected_set.add(companion_id)
                selected.append(companion_id)
    return selected, blockers
