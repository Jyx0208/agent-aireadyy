from __future__ import annotations

from collections import Counter
from typing import Any

from agent.discovery.features import UNKNOWN, lc_gradient_bucket
from agent.discovery.models import DatasetRequest, DiscoveredFile, DiscoveredProject


SelectedItems = list[tuple[DiscoveredProject, list[DiscoveredFile]]]


def _known_values(values: list[str]) -> list[str]:
    return [value for value in values if value and value.casefold() != UNKNOWN]


def _file_key(file: DiscoveredFile) -> tuple[str, str]:
    return file.project_accession, file.file_name


def _candidate_score(
    file: DiscoveredFile,
    project: DiscoveredProject,
    seen_species: set[str],
    seen_instruments: set[str],
    seen_fragmentation: set[str],
    seen_lc: set[str],
) -> float:
    novelty = 0.0
    species = _known_values(file.species)
    instruments = _known_values(file.instrument_families)
    fragmentation = _known_values(file.fragmentation_methods)
    lc_bucket = lc_gradient_bucket(file.lc_gradient_minutes)

    if any(value not in seen_species for value in species):
        novelty += 4.0
    if any(value not in seen_instruments for value in instruments):
        novelty += 8.0
    if any(value not in seen_fragmentation for value in fragmentation):
        novelty += 8.0
    if lc_bucket != UNKNOWN and lc_bucket not in seen_lc:
        novelty += 4.0

    trust = file.trust_score if file.trust_score > 0 else file.confidence
    return (
        trust * 100.0
        + file.file_score
        + project.project_score * 0.15
        + file.memory_prior * 100.0
        + novelty
        - (5.0 if file.needs_review else 0.0)
    )


def select_diverse_items(
    items: list[tuple[DiscoveredProject, list[DiscoveredFile]]],
    request: DatasetRequest,
) -> SelectedItems:
    candidates: list[tuple[DiscoveredProject, DiscoveredFile]] = []
    for project, files in items:
        for file in files:
            if file.validity_status == "exclude":
                continue
            candidates.append((project, file))

    selected_keys: set[tuple[str, str]] = set()
    selected_projects: dict[str, DiscoveredProject] = {}
    files_by_project: dict[str, list[DiscoveredFile]] = {}
    seen_species: set[str] = set()
    seen_instruments: set[str] = set()
    seen_fragmentation: set[str] = set()
    seen_lc: set[str] = set()

    while len(selected_keys) < request.max_files:
        ranked: list[tuple[float, str, str, DiscoveredProject, DiscoveredFile]] = []
        for project, file in candidates:
            key = _file_key(file)
            if key in selected_keys:
                continue
            project_is_selected = project.project_accession in selected_projects
            if not project_is_selected and len(selected_projects) >= request.max_projects:
                continue
            if len(files_by_project.get(project.project_accession, [])) >= request.max_files_per_project:
                continue
            score = _candidate_score(
                file,
                project,
                seen_species,
                seen_instruments,
                seen_fragmentation,
                seen_lc,
            )
            ranked.append((score, project.project_accession, file.file_name.casefold(), project, file))
        if not ranked:
            break

        _, _, _, project, file = max(ranked, key=lambda item: (item[0], item[1], item[2]))
        selected_keys.add(_file_key(file))
        selected_projects.setdefault(project.project_accession, project)
        files_by_project.setdefault(project.project_accession, []).append(file)

        seen_species.update(_known_values(file.species))
        seen_instruments.update(_known_values(file.instrument_families))
        seen_fragmentation.update(_known_values(file.fragmentation_methods))
        lc_bucket = lc_gradient_bucket(file.lc_gradient_minutes)
        if lc_bucket != UNKNOWN:
            seen_lc.add(lc_bucket)

    return [
        (
            project.model_copy(update={"selected_file_count": len(files_by_project[accession])}),
            files_by_project[accession],
        )
        for accession, project in selected_projects.items()
        if files_by_project.get(accession)
    ]


def _count_list_values(files: list[DiscoveredFile], field_name: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for file in files:
        values = getattr(file, field_name, []) or [UNKNOWN]
        for value in values:
            counts[str(value or UNKNOWN)] += 1
    return dict(sorted(counts.items()))


def diversity_summary(files: list[DiscoveredFile]) -> dict[str, Any]:
    lc_counts: Counter[str] = Counter(lc_gradient_bucket(file.lc_gradient_minutes) for file in files)
    unknown_counts = {
        "species": sum(1 for file in files if not _known_values(file.species)),
        "instrument_family": sum(1 for file in files if not _known_values(file.instrument_families)),
        "fragmentation_method": sum(1 for file in files if not _known_values(file.fragmentation_methods)),
        "lc_gradient": sum(1 for file in files if lc_gradient_bucket(file.lc_gradient_minutes) == UNKNOWN),
    }
    return {
        "species_distribution": _count_list_values(files, "species"),
        "instrument_family_distribution": _count_list_values(files, "instrument_families"),
        "fragmentation_method_distribution": _count_list_values(files, "fragmentation_methods"),
        "lc_gradient_distribution": dict(sorted(lc_counts.items())),
        "unknown_counts": unknown_counts,
    }


def validity_summary(files: list[DiscoveredFile]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter(file.validity_status for file in files)
    reason_counts: Counter[str] = Counter()
    for file in files:
        for reason in file.validity_reasons:
            reason_counts[reason] += 1
    return {
        "validity_status_counts": dict(sorted(status_counts.items())),
        "validity_reason_counts": dict(sorted(reason_counts.items())),
    }
