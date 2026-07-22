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
    request: DatasetRequest,
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
    generation = (
        file.instrument_generation_score
        if file.instrument_generation_score is not None
        else project.instrument_generation_score
    )
    instrument_preference_bonus = 0.0
    if generation is not None and request.instrument_preference in {
        "newer",
        "newer_with_legacy_floor",
    }:
        instrument_preference_bonus = float(generation) * 24.0
    elif generation is not None and request.instrument_preference == "classic":
        instrument_preference_bonus = (1.0 - float(generation)) * 18.0
    return (
        trust * 100.0
        + file.file_score
        + (project.calibrated_project_score if project.calibrated_project_score is not None else project.project_score) * 0.15
        + file.memory_prior * 100.0
        + novelty
        + instrument_preference_bonus
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

    portfolio_mode = (
        bool(getattr(request, "harvest_all_qualified", False))
        or str(getattr(request, "quantity_scope", "") or "") == "portfolio"
        or str(getattr(request, "portfolio_size_preference", "") or "").startswith("maximize")
        or int(request.max_projects) >= 100
    )
    # Soft ceilings for curated pilots; maximize harvests keep all quality candidates.
    project_limit = (
        max(int(request.max_projects), len(items), 5000)
        if portfolio_mode
        else int(request.max_projects)
    )
    file_limit = (
        max(int(request.max_files), sum(len(files) for _project, files in items), 100000)
        if portfolio_mode
        else int(request.max_files)
    )
    per_project_limit = (
        max(int(request.max_files_per_project), 500)
        if portfolio_mode
        else int(request.max_files_per_project)
    )

    while len(selected_keys) < file_limit:
        ranked: list[tuple[float, str, str, DiscoveredProject, DiscoveredFile]] = []
        for project, file in candidates:
            key = _file_key(file)
            if key in selected_keys:
                continue
            project_is_selected = project.project_accession in selected_projects
            if not project_is_selected and len(selected_projects) >= project_limit:
                continue
            if len(files_by_project.get(project.project_accession, [])) >= per_project_limit:
                continue
            score = _candidate_score(
                file,
                project,
                request,
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
    generation_counts: Counter[str] = Counter(
        str(file.instrument_generation_label or UNKNOWN) for file in files
    )
    unknown_counts = {
        "species": sum(1 for file in files if not _known_values(file.species)),
        "instrument_family": sum(1 for file in files if not _known_values(file.instrument_families)),
        "fragmentation_method": sum(1 for file in files if not _known_values(file.fragmentation_methods)),
        "lc_gradient": sum(1 for file in files if lc_gradient_bucket(file.lc_gradient_minutes) == UNKNOWN),
    }
    return {
        "species_distribution": _count_list_values(files, "species"),
        "instrument_family_distribution": _count_list_values(files, "instrument_families"),
        "instrument_generation_distribution": dict(sorted(generation_counts.items())),
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
