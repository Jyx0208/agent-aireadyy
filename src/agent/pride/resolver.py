from __future__ import annotations

import re
from datetime import date
from pathlib import PurePath
from typing import Any

from agent.input.normalizer import normalize_input
from agent.models import InputTask, ProjectCandidate, ProjectResolution
from agent.pride.client import PrideClient


def _candidate_sort_key(candidate: ProjectCandidate) -> tuple[int, float, date]:
    anchor_date = candidate.publication_date or candidate.submission_date or date.max
    return (-candidate.match_score, -candidate.metadata_consistency, anchor_date)


def resolve_primary_project(candidates: list[ProjectCandidate]) -> ProjectResolution:
    if not candidates:
        return ProjectResolution.empty()

    ordered = sorted(candidates, key=_candidate_sort_key)
    primary = ordered[0]
    alternatives = ordered[1:]

    reasons = [f"Selected {primary.project_accession} by highest match score"]
    if alternatives and primary.match_score == alternatives[0].match_score:
        if primary.metadata_consistency > alternatives[0].metadata_consistency:
            reasons.append("metadata consistency wins before date comparison")
        else:
            reasons.append("earliest project date wins when score and consistency are equal")

    confidence = min(1.0, primary.match_score / 100 + primary.metadata_consistency / 2)
    return ProjectResolution(
        primary_project=primary,
        alternative_projects=alternatives,
        resolution_reason="; ".join(reasons),
        resolution_confidence=confidence,
        needs_review=False,
    )


def _normalized_variants(task: InputTask) -> list[tuple[str, str]]:
    variants = [
        (task.file_name, "exact"),
        (task.stem, "stem"),
    ]
    stem_prefix = re.split(r"[-_]", task.stem)[0]
    if stem_prefix and stem_prefix != task.stem:
        variants.append((stem_prefix, "prefix"))
    deduped: list[tuple[str, str]] = []
    seen = set()
    for value, kind in variants:
        key = value.lower()
        if key not in seen:
            deduped.append((value, kind))
            seen.add(key)
    return deduped


def _score_file_match(target_name: str, file_name: str, variant_type: str) -> tuple[int, str] | None:
    target = target_name.lower()
    candidate = file_name.lower()
    if candidate == target:
        return 100, "exact file match"
    candidate_stem = PurePath(candidate).stem
    if variant_type == "stem" and candidate_stem == target:
        return 90, "stem file match"
    if variant_type == "prefix" and candidate.startswith(target):
        return 70, "prefix file match"
    if variant_type == "stem" and target in candidate_stem:
        return 60, "stem substring file match"
    return None


def _metadata_consistency(project: dict[str, Any]) -> float:
    checks = [
        bool(project.get("organisms")),
        bool(project.get("instruments")),
        bool(project.get("sampleProcessingProtocol")),
        bool(project.get("dataProcessingProtocol")),
        bool(project.get("keywords")),
    ]
    return sum(checks) / len(checks)


def find_project_candidates(client: PrideClient, task: InputTask) -> list[ProjectCandidate]:
    candidates: dict[str, ProjectCandidate] = {}
    for query, query_type in _normalized_variants(task):
        for project in client.search_projects(query):
            accession = project["accession"]
            files = client.list_project_files(accession, keyword=query)
            for file_record in files:
                scored = _score_file_match(query, file_record.get("fileName", ""), query_type)
                if not scored:
                    continue
                score, evidence = scored
                existing = candidates.get(accession)
                candidate = ProjectCandidate(
                    project_accession=accession,
                    matched_file=file_record["fileName"],
                    match_type=query_type,
                    match_score=score,
                    publication_date=_parse_date(project.get("publicationDate")),
                    submission_date=_parse_date(project.get("submissionDate")),
                    evidence=[evidence],
                    metadata_consistency=_metadata_consistency(project),
                )
                if existing is None or _candidate_sort_key(candidate) < _candidate_sort_key(existing):
                    candidates[accession] = candidate
    return list(candidates.values())


def resolve_input_to_project(client: PrideClient, raw_input: str) -> ProjectResolution:
    task = normalize_input(raw_input)
    candidates = find_project_candidates(client, task)
    return resolve_primary_project(candidates)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])
