from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from math import ceil
from typing import Any, Callable

from agent.discovery.diversity import diversity_summary, select_diverse_items, validity_summary
from agent.discovery.features import extract_file_features, extract_project_features
from agent.discovery.memory import (
    DiscoveryMemory,
    memory_feedback_for_candidate,
    memory_prior_for_file,
    memory_prior_for_project,
)
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject
from agent.discovery.query_builder import build_pride_queries, prepare_pride_search_queries
from agent.discovery.scoring import build_discovered_project, score_file, score_project
from agent.metadata.context import detect_sdrf_file, load_sdrf_rows, select_sdrf_rows_for_file
from agent.pride.client import PrideClient


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
    scored = [(score_project(project, request), project) for project in deduped]
    ranked = sorted(
        scored,
        key=lambda item: (
            item[0].excluded,
            item[0].needs_review,
            -item[0].project_score,
            _project_accession(item[1]),
        ),
    )
    return [project for _score, project in ranked[:limit]]


def _sort_projects(items: list[tuple[DiscoveredProject, list[DiscoveredFile]]]) -> list[tuple[DiscoveredProject, list[DiscoveredFile]]]:
    return sorted(
        items,
        key=lambda item: (
            -item[0].project_score,
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
        # A shallow per-query page systematically hides older but highly relevant projects.
        # Fetch enough metadata to rank candidates globally; project/file inspection remains bounded.
        if candidate_records is None:
            search_page_size = max(
                20,
                min(100, ceil(request.max_candidate_projects / max(1, len(queries)))),
            )
            for query in queries:
                _check_cancel()
                _report(f"Searching PRIDE projects: {query}")
                try:
                    searched_records.extend(pride.search_projects(query, page_size=search_page_size))
                    _report(f"Project search returned {len(searched_records)} raw records so far.")
                except Exception as exc:  # pragma: no cover - defensive network boundary
                    failures.append({"stage": "search_projects", "query": query, "error": str(exc)})
                    _report(f"Project search failed for query '{query}': {exc}")

        candidates = _rank_candidate_projects(
            searched_records, request, request.max_candidate_projects
        )
        _report(f"Deduped to {len(candidates)} candidate project(s).")
        scored_items: list[tuple[DiscoveredProject, list[DiscoveredFile]]] = []
        excluded_projects = 0
        excluded_files = 0

        for candidate in candidates:
            _check_cancel()
            accession = _project_accession(candidate)
            _report(f"Inspecting project {accession}.")
            try:
                project_record = pride.get_project(accession)
            except Exception as exc:  # pragma: no cover - defensive network boundary
                failures.append({"stage": "get_project", "project": accession, "error": str(exc)})
                _report(f"Project metadata failed for {accession}: {exc}")
                continue

            project_score = score_project(project_record, request)
            if project_score.excluded:
                excluded_projects += 1
                _report(f"Excluded project {accession}: acquisition evidence conflicts with request.")
                continue

            file_fetch_limit = max(request.max_files_per_project, request.max_files_per_project * 5)
            try:
                _report(f"Listing files for {accession} (limit {file_fetch_limit}).")
                raw_files = pride.list_project_files(accession, max_files=file_fetch_limit)
                _report(f"{accession}: fetched {len(raw_files)} file record(s).")
            except Exception as exc:  # pragma: no cover - defensive network boundary
                failures.append({"stage": "list_project_files", "project": accession, "error": str(exc)})
                _report(f"File listing failed for {accession}: {exc}")
                continue

            sdrf_rows: list[dict[str, Any]] = []
            sdrf_candidates: list[dict[str, Any]] = []
            try:
                _report(f"{accession}: checking SDRF metadata.")
                sdrf_candidates = pride.list_project_files(accession, keyword="sdrf", max_files=5)
            except Exception as exc:  # pragma: no cover - defensive network boundary
                failures.append({"stage": "find_sdrf", "project": accession, "error": str(exc)})
                _report(f"{accession}: SDRF lookup failed: {exc}")
            sdrf_file = detect_sdrf_file([*raw_files, *sdrf_candidates])
            if sdrf_file:
                sdrf_url = PrideClient.first_download_url(sdrf_file)
                if sdrf_url:
                    try:
                        _report(f"{accession}: downloading SDRF text.")
                        sdrf_rows = load_sdrf_rows(pride.download_text(sdrf_url))
                        _report(f"{accession}: loaded {len(sdrf_rows)} SDRF row(s).")
                    except Exception as exc:  # pragma: no cover - defensive network boundary
                        failures.append({"stage": "download_sdrf", "project": accession, "error": str(exc)})
                        _report(f"{accession}: SDRF download failed: {exc}")

            project_features = extract_project_features(project_record, sdrf_rows)
            project = build_discovered_project(
                project_record,
                request,
                project_score,
                features=project_features,
                memory_prior=memory_prior_for_project(review_decisions, accession),
                memory_feedback=memory_feedback_for_candidate(review_decisions, accession),
            )

            scored_files: list[DiscoveredFile] = []
            for raw_file in raw_files:
                _check_cancel()
                file_name = str(raw_file.get("fileName") or raw_file.get("name") or "")
                matched_sdrf_rows = select_sdrf_rows_for_file(sdrf_rows, file_name) if sdrf_rows and file_name else []
                if not sdrf_rows:
                    sdrf_match_status = "no_sdrf"
                elif matched_sdrf_rows:
                    sdrf_match_status = "matched"
                else:
                    sdrf_match_status = "no_file_match"
                file_features = extract_file_features(raw_file, project_features, matched_sdrf_rows)
                scored_file = score_file(
                    raw_file,
                    project,
                    request,
                    features=file_features,
                    memory_prior=memory_prior_for_file(review_decisions, accession, file_name),
                    memory_feedback=memory_feedback_for_candidate(review_decisions, accession, file_name),
                    sdrf_match_status=sdrf_match_status,
                )
                if scored_file is not None:
                    if scored_file.validity_status == "exclude":
                        excluded_files += 1
                    else:
                        scored_files.append(scored_file)
            if not scored_files:
                _report(f"{accession}: no usable acquisition/peaklist file candidates after filtering.")
                continue

            project = project.model_copy(update={"file_count": len(scored_files)})
            scored_items.append((project, _sort_files(scored_files)))
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
