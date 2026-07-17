from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from agent.discovery.diversity import diversity_summary, select_diverse_items, validity_summary
from agent.discovery.features import extract_file_features, extract_project_features
from agent.discovery.memory import DiscoveryMemory
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject
from agent.discovery.pride_discovery import discover_pride_dataset
from agent.discovery.query_builder import build_pride_queries
from agent.discovery.scoring import build_discovered_project, score_file, score_project
from agent.metadata.canonical import CanonicalFile, CanonicalMetadataValue, CanonicalProject
from agent.pride.client import PrideClient
from agent.repositories.registry import RepositoryRegistry


def _dedupe_text(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _metadata_values(items: Iterable[CanonicalMetadataValue]) -> list[dict[str, str]]:
    return [{"name": item.value, "value": item.value, "source": item.source} for item in items if item.value]


def _canonical_project_to_record(project: CanonicalProject) -> dict[str, Any]:
    return {
        "accession": project.primary_accession,
        "projectAccession": project.px_accession or project.primary_accession,
        "nativeAccession": project.native_accession or project.primary_accession,
        "repository": project.repository,
        "title": project.title,
        "projectDescription": project.description,
        "sampleProcessingProtocol": project.sample_processing_protocol.value if project.sample_processing_protocol else None,
        "dataProcessingProtocol": project.data_processing_protocol.value if project.data_processing_protocol else None,
        "organisms": _metadata_values(project.organisms),
        "instruments": _metadata_values(project.instruments),
        "experimentTypes": _metadata_values(project.experiment_types),
        "keywords": project.keywords,
        "publicationDate": project.publication_date,
        "raw_metadata": project.raw_metadata,
    }


def _canonical_file_to_record(file: CanonicalFile) -> dict[str, Any]:
    public_locations = [{"value": url} for url in file.download_urls]
    return {
        "fileName": file.file_name,
        "name": file.file_name,
        "fileAccession": file.logical_path or file.file_name,
        "path": file.logical_path or file.file_name,
        "filePath": file.logical_path or file.file_name,
        "logicalPath": file.logical_path,
        "fileCategory": file.file_category,
        "fileFormat": file.file_format,
        "fileSizeBytes": file.size_bytes,
        "size": file.size_bytes,
        "checksum": file.checksum,
        "download_url": file.download_urls[0] if file.download_urls else None,
        "publicFileLocations": public_locations,
        "transferMethod": file.transfer_method,
        "rawRecord": file.raw_record,
    }


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


def _repository_audit_entry(
    *,
    repository: str,
    support_status: str,
    candidate_projects_seen: int = 0,
    eligible_projects_seen: int = 0,
    selected_projects: int = 0,
    selected_files: int = 0,
    failures: list[dict[str, Any]] | None = None,
    next_step: str = "",
) -> dict[str, Any]:
    failures = failures or []
    first_failure = failures[0] if failures else {}
    blocker = str(first_failure.get("error") or "") if isinstance(first_failure, dict) else ""
    status = "completed" if selected_files > 0 else "blocked" if support_status == "blocked" or blocker else "no_selected_files"
    return {
        "repository": repository,
        "status": status,
        "support_status": support_status,
        "candidate_projects_seen": int(candidate_projects_seen or 0),
        "eligible_projects_seen": int(eligible_projects_seen or 0),
        "selected_projects": int(selected_projects or 0),
        "selected_files": int(selected_files or 0),
        "blocker": blocker,
        "next_step": next_step or _repository_next_step(status=status, blocker=blocker, repository=repository),
    }


def _repository_audit_from_summary(summary: dict[str, Any], *, repository: str | None = None) -> dict[str, Any]:
    repo = repository or str(summary.get("repository") or summary.get("requested_repository") or "unknown")
    return _repository_audit_entry(
        repository=repo,
        support_status=str(summary.get("repository_support_status") or "unknown"),
        candidate_projects_seen=int(summary.get("candidate_projects_seen") or 0),
        eligible_projects_seen=int(summary.get("eligible_projects_seen") or 0),
        selected_projects=int(summary.get("selected_projects") or 0),
        selected_files=int(summary.get("selected_files") or 0),
        failures=[failure for failure in (summary.get("failures") or []) if isinstance(failure, dict)],
        next_step=str(summary.get("next_step") or ""),
    )


def _repository_next_step(*, status: str, blocker: str, repository: str) -> str:
    if status == "completed":
        return "send_selected_to_batch_or_ai_ready_build"
    if blocker == "iprox_index_missing" or repository == "iprox":
        return "refresh_iprox_index_or_set_agent_iprox_index_xlsx"
    if blocker:
        return "review_repository_discovery_failure"
    return "relax_query_or_try_repository_smoke"


def _repository_blocked_manifest(request: DatasetRequest, repository: str, *, error: str, next_step: str, notes: list[str]) -> DatasetManifest:
    summary = {
        "repository": repository,
        "requested_repository": request.repository,
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
        "queries": [],
        "candidate_projects_seen": 0,
        "eligible_projects_seen": 0,
        "excluded_projects": 0,
        "excluded_files": 0,
        "selected_projects": 0,
        "selected_files": 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "failures": [{"stage": "repository_discovery", "repository": repository, "error": error}],
        "repository_support_status": "blocked",
        "next_step": next_step,
        "notes": notes,
    }
    summary["repository_audit"] = [
        _repository_audit_from_summary(summary, repository=repository)
    ]
    return DatasetManifest(request=request, projects=[], files=[], summary=summary)


def _discover_adapter_dataset(
    request: DatasetRequest,
    *,
    repository: str,
    registry: RepositoryRegistry | None = None,
    queries: list[str] | None = None,
    report: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    early_stop_on_limits: bool = False,
) -> DatasetManifest:
    registry = registry or RepositoryRegistry()
    adapter = registry.get(repository)
    queries = queries or build_pride_queries(request)
    failures: list[dict[str, str]] = []
    candidates: list[CanonicalProject] = []
    seen_projects: set[str] = set()

    def _report(message: str) -> None:
        if report is not None:
            report(message)

    def _check_cancel() -> None:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Discovery cancelled.")

    if not hasattr(adapter, "search_projects"):
        return _repository_blocked_manifest(
            request,
            repository,
            error="repository_adapter_search_projects_missing",
            next_step="run_repository_smoke_with_known_accession_or_file",
            notes=[f"{repository} adapter cannot search projects yet."],
        )

    try:
        for query in queries:
            _check_cancel()
            _report(f"Searching {repository} projects: {query}")
            try:
                raw_projects = adapter.search_projects(query, limit=request.max_candidate_projects)  # type: ignore[attr-defined]
            except FileNotFoundError as exc:
                return _repository_blocked_manifest(
                    request,
                    repository,
                    error="iprox_index_missing" if repository == "iprox" else str(exc),
                    next_step="refresh_iprox_index_or_set_agent_iprox_index_xlsx",
                    notes=["Run refresh-iprox-index or set AGENT_IPROX_INDEX_DIR / AGENT_IPROX_INDEX_XLSX."],
                )
            except Exception as exc:  # pragma: no cover - defensive network boundary
                failures.append({"stage": "search_projects", "repository": repository, "query": query, "error": str(exc)})
                _report(f"{repository} project search failed for query '{query}': {exc}")
                continue
            for project in raw_projects:
                key = project.primary_accession
                if not key or key in seen_projects:
                    continue
                seen_projects.add(key)
                candidates.append(project)
                if len(candidates) >= request.max_candidate_projects:
                    break
            if len(candidates) >= request.max_candidate_projects:
                break

        scored_items: list[tuple[DiscoveredProject, list[DiscoveredFile]]] = []
        excluded_projects = 0
        excluded_files = 0

        for candidate in candidates:
            _check_cancel()
            accession = candidate.primary_accession
            _report(f"Inspecting {repository} project {accession}.")
            try:
                project = adapter.get_project(accession)
                project_record = _canonical_project_to_record(project)
            except Exception as exc:  # pragma: no cover - defensive adapter boundary
                failures.append({"stage": "get_project", "repository": repository, "project": accession, "error": str(exc)})
                continue
            project_score = score_project(project_record, request)
            if project_score.excluded:
                excluded_projects += 1
                continue
            try:
                raw_files = [_canonical_file_to_record(file) for file in adapter.list_project_files(project)]
            except Exception as exc:  # pragma: no cover - defensive adapter boundary
                failures.append({"stage": "list_project_files", "repository": repository, "project": accession, "error": str(exc)})
                continue
            _report(f"{repository} {accession}: fetched {len(raw_files)} file record(s).")
            project_features = extract_project_features(project_record, [])
            discovered_project = build_discovered_project(project_record, request, project_score, features=project_features)

            scored_files: list[DiscoveredFile] = []
            for raw_file in raw_files:
                _check_cancel()
                file_features = extract_file_features(raw_file, project_features, [])
                scored_file = score_file(
                    raw_file,
                    discovered_project,
                    request,
                    features=file_features,
                    sdrf_match_status="not_checked",
                )
                if scored_file is not None:
                    if scored_file.validity_status == "exclude":
                        excluded_files += 1
                    else:
                        scored_files.append(scored_file)
            if not scored_files:
                _report(f"{repository} {accession}: no usable acquisition/peaklist file candidates after filtering.")
                continue
            discovered_project = discovered_project.model_copy(update={"file_count": len(scored_files)})
            scored_items.append((discovered_project, _sort_files(scored_files)))
            if early_stop_on_limits:
                eligible_project_count = len(scored_items)
                eligible_file_count = sum(len(project_files) for _project, project_files in scored_items)
                if eligible_project_count >= request.max_projects and eligible_file_count >= request.max_files:
                    break

        selected_items = select_diverse_items(_sort_projects(scored_items), request)
        selected_projects = [project for project, _files in selected_items]
        selected_files = [file for _project, files in selected_items for file in files]
        diversity = diversity_summary(selected_files)
        validity = validity_summary(selected_files)
        file_context = _file_context_summary(selected_files)
        summary = {
            "repository": repository,
            "requested_repository": request.repository,
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
            "memory_used": False,
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
            "repository_support_status": "remote_discovery_v1",
        }
        summary["repository_audit"] = [
            _repository_audit_from_summary(summary, repository=repository)
        ]
        return DatasetManifest(request=request, projects=selected_projects, files=selected_files, summary=summary)
    except InterruptedError:
        raise


def _merge_auto_manifests(request: DatasetRequest, manifests: list[DatasetManifest]) -> DatasetManifest:
    projects: list[DiscoveredProject] = []
    files: list[DiscoveredFile] = []
    seen_projects: set[tuple[str, str]] = set()
    seen_files: set[tuple[str, str, str]] = set()
    failures: list[dict[str, Any]] = []
    repositories_attempted: list[str] = []
    repository_audit: list[dict[str, Any]] = []
    for manifest in manifests:
        repo = str(manifest.summary.get("repository") or manifest.request.repository)
        repositories_attempted.append(repo)
        failures.extend(manifest.summary.get("failures") or [])
        audit_rows = manifest.summary.get("repository_audit")
        if isinstance(audit_rows, list) and audit_rows:
            repository_audit.extend([row for row in audit_rows if isinstance(row, dict)])
        else:
            repository_audit.append(_repository_audit_from_summary(manifest.summary, repository=repo))
        for project in manifest.projects:
            key = (project.repository, project.project_accession)
            if key not in seen_projects:
                seen_projects.add(key)
                projects.append(project)
        for file in manifest.files:
            key = (file.repository, file.project_accession, file.file_accession_or_path)
            if key not in seen_files:
                seen_files.add(key)
                files.append(file)
    files = sorted(files, key=lambda item: (-item.trust_score, -item.file_score, item.repository, item.file_name.casefold()))[: request.max_files]
    selected_project_keys = {(file.repository, file.project_accession) for file in files}
    projects = [project for project in projects if (project.repository, project.project_accession) in selected_project_keys][: request.max_projects]
    diversity = diversity_summary(files)
    validity = validity_summary(files)
    file_context = _file_context_summary(files)
    summary = {
        "repository": "auto",
        "requested_repository": "auto",
        "repositories_attempted": repositories_attempted,
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
        "queries": [query for manifest in manifests for query in (manifest.summary.get("queries") or [])],
        "candidate_projects_seen": sum(int(manifest.summary.get("candidate_projects_seen") or 0) for manifest in manifests),
        "eligible_projects_seen": sum(int(manifest.summary.get("eligible_projects_seen") or 0) for manifest in manifests),
        "excluded_projects": sum(int(manifest.summary.get("excluded_projects") or 0) for manifest in manifests),
        "excluded_files": sum(int(manifest.summary.get("excluded_files") or 0) for manifest in manifests),
        "selected_projects": len(projects),
        "selected_files": len(files),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "failures": failures,
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
        "repository_support_status": "auto_remote_discovery_v1",
        "repository_audit": repository_audit,
        "repository_counts": dict(sorted(Counter(file.repository for file in files).items())),
    }
    return DatasetManifest(request=request, projects=projects, files=files, summary=summary)


def discover_repository_dataset(
    request: DatasetRequest,
    *,
    client: PrideClient | None = None,
    memory: DiscoveryMemory | None = None,
    queries: list[str] | None = None,
    report: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    early_stop_on_limits: bool = False,
    registry: RepositoryRegistry | None = None,
) -> DatasetManifest:
    """Repository-aware discovery entry point."""
    repository = request.repository
    if repository == "pride":
        return discover_pride_dataset(
            request,
            client=client,
            memory=memory,
            queries=queries,
            report=report,
            should_cancel=should_cancel,
            early_stop_on_limits=early_stop_on_limits,
        )
    if repository == "auto":
        manifests: list[DatasetManifest] = []
        for repo in ("pride", "massive", "iprox"):
            repo_request = request.model_copy(update={"repository": repo})
            try:
                manifests.append(
                    discover_repository_dataset(
                        repo_request,
                        client=client if repo == "pride" else None,
                        memory=memory if repo == "pride" else None,
                        queries=queries,
                        report=report,
                        should_cancel=should_cancel,
                        early_stop_on_limits=early_stop_on_limits,
                        registry=registry,
                    )
                )
            except InterruptedError:
                raise
            except Exception as exc:  # pragma: no cover - defensive auto merge boundary
                manifests.append(
                    _repository_blocked_manifest(
                        repo_request,
                        repo,
                        error=str(exc),
                        next_step="review_repository_discovery_failure",
                        notes=[f"{repo} discovery failed during auto merge."],
                    )
                )
        return _merge_auto_manifests(request, manifests)
    return _discover_adapter_dataset(
        request,
        repository=repository,
        registry=registry,
        queries=queries,
        report=report,
        should_cancel=should_cancel,
        early_stop_on_limits=early_stop_on_limits,
    )
