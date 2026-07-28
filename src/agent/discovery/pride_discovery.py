from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from math import ceil
import re
from typing import Any, Callable, Literal

from agent.discovery.diversity import diversity_summary, select_diverse_items, validity_summary
from agent.discovery.features import extract_file_features, extract_project_features
from agent.discovery.memory import (
    DiscoveryMemory,
    memory_feedback_for_candidate,
    memory_prior_for_file,
    memory_prior_for_project,
)
from agent.discovery.models import (
    DatasetManifest,
    DatasetRequest,
    DiscoveredFile,
    DiscoveredProject,
    DiscoveryEvidence,
)
from agent.discovery.ontology import interpret_immunopeptide_metadata
from agent.discovery.query_builder import build_pride_queries, prepare_pride_search_queries
from agent.discovery.scoring import (
    build_discovered_project,
    classify_file_role,
    score_file,
    score_project,
)
from agent.metadata.context import (
    build_sdrf_file_index,
    detect_sdrf_file,
    extract_sdrf_assay_values,
    load_sdrf_rows,
    select_sdrf_rows_for_file,
    summarize_sdrf_rows,
)
from agent.pride.client import PrideClient, search_projects_paginated


InspectionOutcomeCategory = Literal[
    "usable_files",
    "scientific_exclusion",
    "no_usable_files",
    "inspection_failure",
    "not_inspected",
]


def _matched_sdrf_immunopeptide_evidence(
    rows: list[dict[str, Any]],
) -> list[DiscoveryEvidence]:
    immunopeptide_values: list[tuple[str, str]] = []
    other_values: list[tuple[str, str]] = []
    for column, value in extract_sdrf_assay_values(rows):
        semantic = interpret_immunopeptide_metadata(re.sub(r"[_-]+", " ", value))
        target = immunopeptide_values if semantic.scope == "immunopeptidomics" else other_values
        target.append((column, value))

    if immunopeptide_values and other_values:
        descriptions = list(
            dict.fromkeys(
                f"{column}={value}" for column, value in [*immunopeptide_values, *other_values]
            )
        )
        return [
            DiscoveryEvidence(
                field="sdrf:assay",
                source="sdrf_assay_conflict",
                text="; ".join(descriptions[:8])[:1000],
            )
        ]

    return [
        DiscoveryEvidence(
            field=f"sdrf:{column}",
            source="immunopeptidomics",
            text=value,
            weight=9,
        )
        for column, value in dict.fromkeys(immunopeptide_values)
    ]


def _project_accession(project: dict[str, Any]) -> str:
    return str(project.get("accession") or project.get("projectAccession") or "")


def _dedupe_projects(projects: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for project in projects:
        accession = _project_accession(project)
        if not accession or accession in seen:
            continue
        seen.add(accession)
        deduped.append(project)
        if len(deduped) >= limit:
            break
    return deduped


def _rank_candidate_projects(
    projects: list[dict[str, Any]], request: DatasetRequest, limit: int
) -> list[dict[str, Any]]:
    deduped = _dedupe_projects(projects, len(projects))
    scored = []
    for project in deduped:
        try:
            score = score_project(project, request)
        except Exception:  # A malformed candidate is classified during inspection.
            score = None
        scored.append((score, project))
    ranked = sorted(
        scored,
        key=lambda item: (
            item[0] is None,
            item[0].excluded if item[0] is not None else True,
            item[0].needs_review if item[0] is not None else True,
            -item[0].project_score if item[0] is not None else 0.0,
            _project_accession(item[1]),
        ),
    )
    return [project for _score, project in ranked[:limit]]


def _sort_projects(items: list[tuple[DiscoveredProject, list[DiscoveredFile]]]) -> list[tuple[DiscoveredProject, list[DiscoveredFile]]]:
    return sorted(
        items,
        key=lambda item: (
            -(item[0].calibrated_project_score if item[0].calibrated_project_score is not None else item[0].project_score),
            item[0].needs_review,
            item[0].project_accession,
        ),
    )


def _sort_files(files: list[DiscoveredFile]) -> list[DiscoveredFile]:
    return sorted(files, key=lambda file: (-file.trust_score, -file.file_score, file.needs_review, file.file_name.casefold()))


def _file_context_summary(files: list[DiscoveredFile]) -> dict[str, Any]:
    warning_counts: Counter[str] = Counter()
    for file in files:
        warning_counts.update(file.evidence_warnings)
    return {
        "evidence_level_distribution": dict(Counter(file.evidence_level for file in files)),
        "sdrf_match_status_distribution": dict(Counter(file.sdrf_match_status for file in files)),
        "evidence_warning_counts": dict(sorted(warning_counts.items())),
    }


def discover_pride_dataset(
    request: DatasetRequest,
    client: PrideClient | None = None,
    memory: DiscoveryMemory | None = None,
    queries: list[str] | None = None,
    candidate_records: list[dict[str, Any]] | None = None,
    report: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    early_stop_on_limits: bool = False,
) -> DatasetManifest:
    owns_client = client is None
    pride = client or PrideClient()
    if candidate_records is None:
        proposed_queries = queries or build_pride_queries(request)
        queries = prepare_pride_search_queries(proposed_queries) or proposed_queries
        searched_records: list[dict[str, Any]] = []
    else:
        proposed_queries = list(queries or [])
        queries = list(queries or [])
        searched_records = list(candidate_records)
    failures: list[dict[str, str]] = []
    review_decisions = memory.load_review_decisions() if memory is not None else []

    def _report(message: str) -> None:
        if report is not None:
            report(message)

    def _check_cancel() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Discovery cancelled.")

    try:
        if candidate_records is None and queries != proposed_queries:
            _report(
                f"Adapted {len(proposed_queries)} semantic query term(s) into "
                f"{len(queries)} high-recall PRIDE keyword seed(s): {'; '.join(queries)}"
            )
        # Page through PRIDE project search, but reserve budget for inspection.
        # Each inspected project typically costs get_project + list_files (+ SDRF).
        if candidate_records is None:
            search_page_size = 100
            target_hits = max(request.max_candidate_projects, request.max_projects * 5, 100)
            per_query_pages = max(1, min(4, ceil(target_hits / max(1, len(queries)) / search_page_size)))
            if str(request.quantity_scope or "") == "portfolio" or str(
                request.portfolio_size_preference or ""
            ).startswith("maximize"):
                per_query_pages = max(per_query_pages, 2)
            per_query_max_results = max(
                50,
                min(
                    request.max_candidate_projects,
                    ceil(request.max_candidate_projects * 2 / max(1, len(queries))),
                ),
            )
            for query in queries:
                _check_cancel()
                _report(
                    f"Searching PRIDE projects: {query} "
                    f"(up to {per_query_pages} page(s), max {per_query_max_results} hits)."
                )
                try:
                    batch = search_projects_paginated(
                        pride,
                        query,
                        page_size=search_page_size,
                        max_pages=per_query_pages,
                        max_results=per_query_max_results,
                    )
                    searched_records.extend(batch)
                    _report(
                        f"Project search returned {len(batch)} hit(s) for '{query}' "
                        f"({len(searched_records)} raw records so far)."
                    )
                except Exception as exc:  # pragma: no cover - defensive network boundary
                    failures.append({"stage": "search_projects", "query": query, "error": str(exc)})
                    _report(f"Project search failed for query '{query}': {exc}")
                    if "hard_repository_request_limit" in str(exc):
                        _report("Repository request budget exhausted during search; stopping further queries.")
                        break

        candidates = _rank_candidate_projects(
            searched_records, request, request.max_candidate_projects
        )
        max_inspect = max(request.max_projects * 3, min(len(candidates), 120))
        if len(candidates) > max_inspect:
            _report(
                f"Limiting inspection to top {max_inspect} of {len(candidates)} ranked candidates "
                "to preserve repository request budget."
            )
            candidates = candidates[:max_inspect]
        _report(f"Deduped to {len(candidates)} candidate project(s).")
        scored_items: list[tuple[DiscoveredProject, list[DiscoveredFile]]] = []
        excluded_projects = 0
        excluded_files = 0
        inspection_outcomes: dict[str, dict[str, Any]] = {}

        def _record_inspection_outcome(
            accession: str,
            category: InspectionOutcomeCategory,
            *,
            stage: str | None = None,
            reason: str | None = None,
            error: str | None = None,
            raw_file_count: int = 0,
            usable_file_count: int = 0,
            excluded_file_count: int = 0,
            file_role_counts: dict[str, int] | None = None,
            filter_reason_counts: dict[str, int] | None = None,
        ) -> None:
            inspection_outcomes[accession] = {
                "project_accession": accession,
                "category": category,
                "stage": stage,
                "reason": reason,
                "error": error,
                "raw_file_count": raw_file_count,
                "usable_file_count": usable_file_count,
                "excluded_file_count": excluded_file_count,
                "file_role_counts": dict(sorted((file_role_counts or {}).items())),
                "filter_reason_counts": dict(
                    sorted((filter_reason_counts or {}).items())
                ),
            }

        for candidate in candidates:
            _check_cancel()
            accession = _project_accession(candidate)
            _report(f"Inspecting project {accession}.")
            try:
                project_record = pride.get_project(accession)
            except Exception as exc:  # pragma: no cover - defensive network boundary
                failures.append({"stage": "get_project", "project": accession, "error": str(exc)})
                _record_inspection_outcome(
                    accession,
                    "inspection_failure",
                    stage="get_project",
                    reason="repository_or_network_failure",
                    error=str(exc),
                )
                _report(f"Project metadata failed for {accession}: {exc}")
                if "hard_repository_request_limit" in str(exc):
                    _report(
                        "Repository request budget exhausted during inspection; "
                        "stopping further project metadata fetches."
                    )
                    break
                continue

            try:
                project_score = score_project(project_record, request)
            except Exception as exc:  # pragma: no cover - defensive parser boundary
                failures.append(
                    {"stage": "score_project", "project": accession, "error": str(exc)}
                )
                _record_inspection_outcome(
                    accession,
                    "inspection_failure",
                    stage="score_project",
                    reason="parse_failure",
                    error=str(exc),
                )
                _report(f"Project metadata parsing failed for {accession}: {exc}")
                continue
            evidence_fields = sorted(
                {
                    str(item.field)
                    for item in project_score.evidence
                    if str(item.field).strip()
                }
            )
            _report(
                f"{accession}: metadata scored; retrieval score "
                f"{project_score.project_score:.2f}, confidence "
                f"{project_score.confidence:.0%}, species "
                f"{', '.join(project_score.species) or 'unknown'}, acquisition "
                f"{project_score.acquisition_mode or 'unknown'}, evidence fields "
                f"{', '.join(evidence_fields[:8]) or 'none'}."
            )
            if project_score.excluded:
                excluded_projects += 1
                reason = project_score.exclusion_reason or "project excluded by request constraints"
                _record_inspection_outcome(
                    accession,
                    "scientific_exclusion",
                    stage="score_project",
                    reason=reason,
                )
                _report(f"Excluded project {accession}: {reason}")
                continue

            # For maximize / "越多越好", page through ALL project files. A per-project
            # cap only makes sense for curated pilots, not complete harvests.
            harvest_all_files = (
                str(request.quantity_scope or "") == "portfolio"
                or str(request.portfolio_size_preference or "").startswith("maximize")
                or bool(getattr(request, "harvest_all_qualified", False))
            )
            file_fetch_limit: int | None
            if harvest_all_files:
                file_fetch_limit = None
            else:
                file_fetch_limit = max(int(request.max_files_per_project), 100)
            try:
                if file_fetch_limit is None:
                    _report(f"Listing files for {accession} (all pages, no per-project cap).")
                else:
                    _report(f"Listing files for {accession} (limit {file_fetch_limit}).")
                raw_files = pride.list_project_files(accession, max_files=file_fetch_limit)
                total_fetched = len(raw_files)
                is_truncated = (
                    file_fetch_limit is not None and total_fetched >= file_fetch_limit
                )
                _report(
                    f"{accession}: fetched {total_fetched} file record(s)"
                    + (
                        f"; truncated by per-project limit {file_fetch_limit}."
                        if is_truncated
                        else " (complete listing)."
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive network boundary
                failures.append({"stage": "list_project_files", "project": accession, "error": str(exc)})
                _record_inspection_outcome(
                    accession,
                    "inspection_failure",
                    stage="list_project_files",
                    reason="repository_or_network_failure",
                    error=str(exc),
                )
                _report(f"File listing failed for {accession}: {exc}")
                if "hard_repository_request_limit" in str(exc):
                    _report(
                        "Repository request budget exhausted while listing files; "
                        "stopping further project inspection."
                    )
                    break
                continue

            sdrf_rows: list[dict[str, Any]] = []
            sdrf_candidates: list[dict[str, Any]] = []
            sdrf_url: str | None = None
            sdrf_hash: str | None = None
            sdrf_status = "not_found"
            sdrf_errors: list[str] = []
            try:
                _report(f"{accession}: checking SDRF metadata.")
                sdrf_candidates = pride.list_project_files(accession, keyword="sdrf", max_files=5)
            except Exception as exc:  # pragma: no cover - defensive network boundary
                failures.append({"stage": "find_sdrf", "project": accession, "error": str(exc)})
                sdrf_status = "lookup_error"
                sdrf_errors.append(str(exc))
                _report(f"{accession}: SDRF lookup failed: {exc}")
            sdrf_file = detect_sdrf_file([*raw_files, *sdrf_candidates])
            if sdrf_file:
                sdrf_url = PrideClient.first_download_url(sdrf_file)
                if sdrf_url:
                    try:
                        _report(f"{accession}: downloading SDRF text.")
                        sdrf_text = pride.download_text(sdrf_url)
                    except Exception as exc:  # pragma: no cover - defensive network boundary
                        failures.append({"stage": "download_sdrf", "project": accession, "error": str(exc)})
                        sdrf_status = "download_error"
                        sdrf_errors.append(str(exc))
                        _report(f"{accession}: SDRF download failed: {exc}")
                    else:
                        sdrf_hash = sha256(sdrf_text.encode("utf-8")).hexdigest()
                        try:
                            sdrf_rows = load_sdrf_rows(sdrf_text)
                            sdrf_status = "available"
                            _report(f"{accession}: loaded {len(sdrf_rows)} SDRF row(s).")
                        except Exception as exc:  # pragma: no cover - defensive parser boundary
                            failures.append({"stage": "parse_sdrf", "project": accession, "error": str(exc)})
                            sdrf_status = "parse_error"
                            sdrf_errors.append(str(exc))
                            _report(f"{accession}: SDRF parsing failed: {exc}")
                else:
                    sdrf_status = "missing_download_url"
                    sdrf_errors.append("SDRF file record has no public download URL")

            project_features = extract_project_features(project_record, sdrf_rows)
            sdrf_file_index = build_sdrf_file_index(sdrf_rows)
            project = build_discovered_project(
                project_record,
                request,
                project_score,
                features=project_features,
                memory_prior=memory_prior_for_project(review_decisions, accession),
                memory_feedback=memory_feedback_for_candidate(review_decisions, accession),
            )

            scored_files: list[DiscoveredFile] = []
            project_excluded_files = 0
            file_parse_errors: list[str] = []
            file_role_counts: Counter[str] = Counter()
            filter_reason_counts: Counter[str] = Counter()
            for raw_file in raw_files:
                _check_cancel()
                file_name = str(raw_file.get("fileName") or raw_file.get("name") or "")
                role = classify_file_role(file_name)
                file_role_counts[role.role] += 1
                try:
                    matched_sdrf_rows = (
                        select_sdrf_rows_for_file(
                            sdrf_rows,
                            file_name,
                            file_index=sdrf_file_index,
                        )
                        if sdrf_rows and file_name
                        else []
                    )
                    if not sdrf_rows:
                        sdrf_match_status = "no_sdrf"
                    elif matched_sdrf_rows:
                        sdrf_match_status = "matched"
                    else:
                        sdrf_match_status = "no_file_match"
                    file_features = extract_file_features(
                        raw_file,
                        project_features,
                        matched_sdrf_rows,
                    )
                    file_features.evidence.extend(
                        _matched_sdrf_immunopeptide_evidence(matched_sdrf_rows)
                    )
                    scored_file = score_file(
                        raw_file,
                        project,
                        request,
                        features=file_features,
                        memory_prior=memory_prior_for_file(
                            review_decisions,
                            accession,
                            file_name,
                        ),
                        memory_feedback=memory_feedback_for_candidate(
                            review_decisions,
                            accession,
                            file_name,
                        ),
                        sdrf_match_status=sdrf_match_status,
                    )
                except Exception as exc:  # pragma: no cover - defensive parser boundary
                    failures.append(
                        {
                            "stage": "score_file",
                            "project": accession,
                            "file": file_name,
                            "error": str(exc),
                        }
                    )
                    file_parse_errors.append(str(exc))
                    continue
                if scored_file is not None:
                    if scored_file.validity_status == "exclude":
                        excluded_files += 1
                        project_excluded_files += 1
                        for reason in scored_file.validity_reasons:
                            filter_reason_counts[str(reason)] += 1
                    else:
                        scored_files.append(scored_file)
                else:
                    project_excluded_files += 1
                    if role.role in {"raw_acquisition", "converted_peaklist"}:
                        # score_file has one supported-role early return: a
                        # file-level acquisition observation conflicts with the
                        # user's hard acquisition mode.
                        filter_reason_counts[
                            "acquisition_hard_constraint_conflict"
                        ] += 1
                    else:
                        filter_reason_counts[
                            f"unsupported_file_role:{role.role}"
                        ] += 1
            if not scored_files:
                if file_parse_errors:
                    _record_inspection_outcome(
                        accession,
                        "inspection_failure",
                        stage="score_files",
                        reason="parse_failure",
                        error=f"{len(file_parse_errors)} file record(s) could not be parsed",
                        raw_file_count=len(raw_files),
                        excluded_file_count=project_excluded_files,
                        file_role_counts=dict(file_role_counts),
                        filter_reason_counts=dict(filter_reason_counts),
                    )
                    _report(
                        f"{accession}: failed to parse {len(file_parse_errors)} file record(s)."
                    )
                else:
                    _record_inspection_outcome(
                        accession,
                        "no_usable_files",
                        stage="score_files",
                        reason="no usable acquisition/peaklist file candidates after filtering",
                        raw_file_count=len(raw_files),
                        excluded_file_count=project_excluded_files,
                        file_role_counts=dict(file_role_counts),
                        filter_reason_counts=dict(filter_reason_counts),
                    )
                    _report(
                        f"{accession}: no usable acquisition/peaklist file candidates after filtering."
                    )
                continue

            project = project.model_copy(
                update={
                    "file_count": len(scored_files),
                    "sdrf_summary": summarize_sdrf_rows(
                        sdrf_rows,
                        [file.file_name for file in scored_files],
                        source_url=sdrf_url,
                        content_sha256=sdrf_hash,
                        status=sdrf_status,
                        errors=sdrf_errors,
                        file_index=sdrf_file_index,
                    ),
                }
            )
            scored_items.append((project, _sort_files(scored_files)))
            _record_inspection_outcome(
                accession,
                "usable_files",
                stage="score_files",
                raw_file_count=len(raw_files),
                usable_file_count=len(scored_files),
                excluded_file_count=project_excluded_files,
                file_role_counts=dict(file_role_counts),
                filter_reason_counts=dict(filter_reason_counts),
            )
            _report(f"{accession}: kept {len(scored_files)} file candidate(s).")
            if early_stop_on_limits:
                eligible_project_count = len(scored_items)
                eligible_file_count = sum(len(project_files) for _project, project_files in scored_items)
                if eligible_project_count >= request.max_projects and eligible_file_count >= request.max_files:
                    _report(
                        "Reached requested discovery limits "
                        f"({eligible_project_count} project(s), {eligible_file_count} file candidate(s)); "
                        "stopping project inspection early."
                    )
                    break

        _check_cancel()
        _report("Running diversity-aware selection.")
        selected_items = select_diverse_items(_sort_projects(scored_items), request)
        selected_projects = [project for project, _files in selected_items]
        selected_files = [file for _project, files in selected_items for file in files]
        _report(f"Selected {len(selected_projects)} project(s), {len(selected_files)} file(s).")
        diversity = diversity_summary(selected_files)
        validity = validity_summary(selected_files)
        file_context = _file_context_summary(selected_files)
        for candidate in candidates:
            accession = _project_accession(candidate)
            if accession and accession not in inspection_outcomes:
                _record_inspection_outcome(
                    accession,
                    "not_inspected",
                    stage="inspection",
                    reason="inspection stopped before this candidate was processed",
                )
        ordered_inspection_outcomes = [
            inspection_outcomes[accession]
            for candidate in candidates
            if (accession := _project_accession(candidate)) in inspection_outcomes
        ]
        inspection_outcome_counts = dict(
            sorted(Counter(item["category"] for item in ordered_inspection_outcomes).items())
        )

        summary = {
            "repository": request.repository,
            "goal": request.goal,
            "ptm_type": request.ptm_type,
            "query_terms": request.query_terms,
            "modification_scope": None if request.goal == "general" else (request.modification_scope or request.ptm_type),
            "immunopeptide_scope": request.immunopeptide_scope,
            "hla_class": request.hla_class,
            "hla_alleles": request.hla_alleles,
            "immunopeptide_evidence_terms": request.immunopeptide_evidence_terms,
            "immunopeptide_enrichment_methods": request.immunopeptide_enrichment_methods,
            "immunopeptide_metadata_confidence": request.immunopeptide_metadata_confidence,
            "labeling_strategy": request.labeling_strategy,
            "canonical_species": request.canonical_species,
            "organism_taxon_id": request.organism_taxon_id,
            "queries": queries,
            "candidate_projects_seen": len(candidates),
            "eligible_projects_seen": len(scored_items),
            "excluded_projects": excluded_projects,
            "excluded_files": excluded_files,
            "inspection_outcomes": ordered_inspection_outcomes,
            "inspection_outcome_counts": inspection_outcome_counts,
            "selected_projects": len(selected_projects),
            "selected_files": len(selected_files),
            "max_projects": request.max_projects,
            "max_files": request.max_files,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "failures": failures,
            "memory_used": memory is not None,
            "diversity": diversity,
            "species_distribution": diversity["species_distribution"],
            "instrument_family_distribution": diversity["instrument_family_distribution"],
            "fragmentation_method_distribution": diversity["fragmentation_method_distribution"],
            "lc_gradient_distribution": diversity["lc_gradient_distribution"],
            "unknown_counts": diversity["unknown_counts"],
            "validity": validity,
            "validity_status_counts": validity["validity_status_counts"],
            "validity_reason_counts": validity["validity_reason_counts"],
            "file_context": file_context,
            "evidence_level_distribution": file_context["evidence_level_distribution"],
            "sdrf_match_status_distribution": file_context["sdrf_match_status_distribution"],
            "evidence_warning_counts": file_context["evidence_warning_counts"],
        }
        return DatasetManifest(request=request, projects=selected_projects, files=selected_files, summary=summary)
    finally:
        if owns_client:
            pride.close()
