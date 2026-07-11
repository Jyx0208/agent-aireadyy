from __future__ import annotations

import re

from agent.discovery.models import DatasetRequest
from agent.discovery.ontology import (
    immunopeptide_query_terms,
    is_immunopeptidomics_goal,
    labeling_query_terms,
    normalize_ptm_type,
    normalize_species,
    ptm_query_terms,
    species_aliases,
)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


_PRIDE_SEARCH_PHRASES = (
    "homo sapiens",
    "mus musculus",
    "rattus norvegicus",
    "escherichia coli",
    "saccharomyces cerevisiae",
    "data dependent",
    "label free",
    "q exactive",
    "fusion lumos",
    "mass spectrometry",
)
_PRIDE_SEARCH_STOPWORDS = {
    "across",
    "acquisition",
    "and",
    "for",
    "gradient",
    "length",
    "method",
    "selecting",
    "the",
    "when",
    "with",
}


def _pride_query_seed_candidates(query: str) -> list[str]:
    value = " ".join(str(query).strip().split())
    if not value:
        return []
    if re.fullmatch(r"PXD\d+", value, flags=re.IGNORECASE):
        return [value.upper()]

    protected = value
    phrase_values: dict[str, str] = {}
    for index, phrase in enumerate(_PRIDE_SEARCH_PHRASES):
        token = f"PRIDEPHRASE{index}"
        if re.search(rf"\b{re.escape(phrase)}\b", protected, flags=re.IGNORECASE):
            protected = re.sub(rf"\b{re.escape(phrase)}\b", token, protected, flags=re.IGNORECASE)
            phrase_values[token.casefold()] = phrase

    candidates: list[str] = []
    for token in re.findall(r"[A-Za-z0-9+*.-]+", protected):
        key = token.casefold()
        candidate = phrase_values.get(key, token)
        normalized_key = re.sub(r"[-_\s]+", " ", candidate.casefold()).strip()
        if normalized_key in _PRIDE_SEARCH_STOPWORDS:
            continue
        if len(candidate) < 3 and candidate.casefold() not in {"qe"}:
            continue
        candidates.append(candidate)
    return _dedupe(candidates)


def prepare_pride_search_queries(queries: list[str]) -> list[str]:
    """Convert semantic query descriptions into high-recall PRIDE keyword seeds."""
    used: set[str] = set()
    prepared: list[str] = []
    for query in queries:
        for candidate in _pride_query_seed_candidates(query):
            key = re.sub(r"[-_\s]+", " ", candidate.casefold()).strip()
            if key in used:
                continue
            used.add(key)
            prepared.append(candidate)
            break
    return prepared


def build_pride_queries(request: DatasetRequest) -> list[str]:
    queries: list[str] = []

    ptm_type = normalize_ptm_type(request.ptm_type)
    ptm_types = _dedupe(
        [
            normalize_ptm_type(value)
            for value in ([*request.ptm_types] if request.ptm_types else [ptm_type])
            if normalize_ptm_type(value) != "unknown_ptm"
        ]
    )
    if not ptm_types:
        ptm_types = [ptm_type]
    ptm_terms = _dedupe([term for value in ptm_types for term in ptm_query_terms(value)])
    immunopeptide_goal = is_immunopeptidomics_goal(request.goal)
    immunopeptide_terms = list(immunopeptide_query_terms()) if immunopeptide_goal else []
    general_goal = str(request.goal or "").casefold() == "general"
    general_terms = [term for term in request.query_terms if str(term).strip()]
    primary_terms = general_terms if general_goal else immunopeptide_terms if immunopeptide_goal else ptm_terms
    if request.goal == "ptm":
        queries.extend(ptm_terms)
    elif immunopeptide_goal:
        queries.extend(immunopeptide_terms)
    elif general_goal:
        queries.extend(general_terms or ["proteomics", "mass spectrometry proteomics"])

    species_aliases: list[str] = []
    for species in request.species:
        species_aliases.extend(species_aliases_for_query(species))

    for species in species_aliases:
        for term in primary_terms:
            queries.append(f"{species} {term}")

    if request.acquisition_mode == "dda":
        if primary_terms:
            for term in primary_terms[:4]:
                queries.append(f"{term} DDA")
                queries.append(f"{term} data dependent")
            if request.goal == "ptm" and len(ptm_types) > 1:
                for ptm in ptm_types:
                    for term in list(ptm_query_terms(ptm))[:2]:
                        queries.append(f"{term} DDA")
                        queries.append(f"{term} data dependent")
        else:
            queries.append("DDA")
            queries.append("data dependent")
        if general_goal:
            queries.append("DDA proteomics")
            queries.append("data dependent acquisition proteomics")

    for label_term in labeling_query_terms(request.labeling_strategy):
        if label_term.lower() != "lfq":
            queries.append(label_term)
            for term in primary_terms[:6]:
                queries.append(f"{label_term} {term}")

    return _dedupe([query for query in queries if query.strip()])


def species_aliases_for_query(species: str) -> list[str]:
    term = normalize_species(species)
    if term is not None:
        aliases = [term.scientific_name, term.canonical, *term.aliases]
    else:
        aliases = list(species_aliases(species))
    return _dedupe(aliases)[:4]
