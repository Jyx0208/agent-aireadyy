from __future__ import annotations

import re

from agent.discovery.models import DatasetRequest
from agent.models import JsonModel
from pydantic import Field
from agent.discovery.ontology import (
    immunopeptide_query_terms,
    is_immunopeptidomics_goal,
    labeling_query_terms,
    normalize_ptm_type,
    normalize_species,
    ptm_query_terms,
    species_aliases,
)



BudgetRole = str  # primary_theme | theme_synonym | secondary_theme | filter_only | exact_accession | rescue_filter_fused

# Soft safety cap on must_exhaust primary units (R-SYNONYMLOOP). Full synonym
# family may still be retained as theme_synonym for agent-led expand under budget.
PRIMARY_THEME_CORE_SOFT_CAP = 5
PRIMARY_THEME_CORE_SOFT_CAP_MIN = 3


class ThemeQueryUnit(JsonModel):
    """One theme-axis query string for deep PRIDE recall."""

    text: str = Field(min_length=1, max_length=240)
    role: str = Field(default="primary_theme", min_length=1, max_length=64)
    family_rank: int = Field(default=0, ge=0, le=10_000)
    priority: int = Field(default=100, ge=0, le=1000)
    intent_dimension: str = Field(default="scientific_theme", min_length=1, max_length=120)
    suggested_min_pages: int | None = Field(default=None, ge=1, le=100)
    must_exhaust: bool = True


class FilterSpec(JsonModel):
    """Post-pool filters (not equal deep-search seeds)."""

    species: list[str] = Field(default_factory=list)
    acquisition_mode: str | None = None
    labeling_strategy: str | None = None
    labeling_hard: bool = False
    task_type: str | None = None
    hard_constraint_fields: list[str] = Field(default_factory=list)
    # Soft suitability (e.g. PSM scoring) — grade/rank only, never hard drop.
    soft_preferences: list[str] = Field(default_factory=list)


class ThemeSearchPlan(JsonModel):
    """IR partition: primary theme family for recall + filters for inspection."""

    primary_theme_id: str = "general"
    primary_family: list[ThemeQueryUnit] = Field(default_factory=list)
    secondary_family: list[ThemeQueryUnit] = Field(default_factory=list)
    filters: FilterSpec = Field(default_factory=FilterSpec)
    rescue_policy: str = "confirm_capped"
    multi_theme_policy: str = "single"
    primary_seed_soft_cap: int = Field(default=PRIMARY_THEME_CORE_SOFT_CAP, ge=1, le=50)


def _primary_theme_id(request: DatasetRequest) -> str:
    if is_immunopeptidomics_goal(request.goal):
        return "immunopeptidomics"
    if str(request.goal or "").casefold() == "ptm":
        return "ptm"
    if str(request.goal or "").casefold() == "general":
        return "general"
    return str(request.goal or "general").casefold() or "general"


def _theme_terms_for_request(request: DatasetRequest) -> list[str]:
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
    if request.goal == "ptm":
        return ptm_terms
    if immunopeptide_goal:
        return immunopeptide_terms
    if general_goal:
        broad_defaults = [
            "proteomics",
            "shotgun proteomics",
            "mass spectrometry proteomics",
            "label free quantitation",
            "TMT proteomics",
            "DIA proteomics",
            "phosphoproteomics",
            "plasma proteomics",
            "affinity purification mass spectrometry",
        ]
        # Species is a filter, not a search seed — do not inject "human proteomics" peers.
        return _dedupe(general_terms or broad_defaults)
    return _dedupe([*general_terms, *ptm_terms, *immunopeptide_terms])


def build_theme_search_plan(
    request: DatasetRequest,
    *,
    primary_seed_soft_cap: int | None = None,
) -> ThemeSearchPlan:
    """Partition DatasetRequest into deep theme family + post-pool FilterSpec.

    Species / acquisition / labeling / task stay on FilterSpec (post-pool).
    Only scientific-theme synonym family enters primary_family for deep recall.
    """

    theme_id = _primary_theme_id(request)
    terms = _theme_terms_for_request(request)
    soft_cap = int(
        PRIMARY_THEME_CORE_SOFT_CAP
        if primary_seed_soft_cap is None
        else max(PRIMARY_THEME_CORE_SOFT_CAP_MIN, primary_seed_soft_cap)
    )
    family: list[ThemeQueryUnit] = []
    for index, term in enumerate(terms):
        text = str(term).strip()
        if not text:
            continue
        # Core plate gets must_exhaust primary_theme; overflow stays in family as
        # theme_synonym (agent may expand under budget; not required for exhaust).
        is_core = index < soft_cap
        family.append(
            ThemeQueryUnit(
                text=text,
                role="primary_theme" if is_core else "theme_synonym",
                family_rank=index,
                priority=max(0, 100 - index),
                intent_dimension="scientific_theme",
                suggested_min_pages=3 if is_core else None,
                must_exhaust=is_core,
            )
        )
    soft_prefs: list[str] = []
    # DatasetRequest has no task_type field; soft suitability comes from
    # success_criteria / free-text preferences (PSM scoring, etc.).
    for criterion in list(getattr(request, "success_criteria", None) or []):
        value = str(criterion).strip()
        if value:
            soft_prefs.append(value)
    for term in list(getattr(request, "query_terms", None) or []):
        value = str(term).strip()
        if not value:
            continue
        low = value.casefold()
        if any(token in low for token in ("psm", "scoring", "peaklist", "suitab")):
            soft_prefs.append(value)
    task = str(getattr(request, "task_type", None) or "").strip()
    if task:
        soft_prefs.append(task)
    soft_prefs = _dedupe(soft_prefs)
    filters = FilterSpec(
        species=[str(s).strip() for s in (request.species or []) if str(s).strip()],
        acquisition_mode=(
            str(request.acquisition_mode).strip()
            if getattr(request, "acquisition_mode", None)
            else None
        ),
        labeling_strategy=(
            str(request.labeling_strategy).strip()
            if getattr(request, "labeling_strategy", None)
            else None
        ),
        labeling_hard=bool(getattr(request, "labeling_hard", False)),
        task_type=task or None,
        hard_constraint_fields=list(request.hard_constraint_fields or []),
        soft_preferences=soft_prefs,
    )
    return ThemeSearchPlan(
        primary_theme_id=theme_id,
        primary_family=family,
        secondary_family=[],
        filters=filters,
        rescue_policy="confirm_capped",
        multi_theme_policy="single",
        primary_seed_soft_cap=soft_cap,
    )


def theme_family_queries(
    plan: ThemeSearchPlan,
    *,
    must_exhaust_only: bool = False,
    roles: set[str] | None = None,
) -> list[str]:
    """Flatten theme units only (no species/DDA filter peers).

    must_exhaust_only=True returns the soft-capped core plate used for family
    exhaust policy; default returns full primary_family (core + synonyms).
    """

    allowed = roles
    texts: list[str] = []
    for unit in plan.primary_family:
        if must_exhaust_only and not unit.must_exhaust:
            continue
        if allowed is not None and unit.role not in allowed:
            continue
        if str(unit.text).strip():
            texts.append(unit.text)
    return _dedupe(texts)


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


def prepare_pride_search_queries(
    queries: list[str],
    *,
    max_seeds_per_query: int | None = None,
    mode: str = "legacy_compound",
) -> list[str]:
    """Convert semantic query descriptions into high-recall PRIDE keyword seeds.

    mode:
      - legacy_compound (default, WP-A): re-atomize compounds into distinct seeds
        so multi-token agent strings expand fully (never silent first-seed-only).
      - theme_atomic (PTS): keep each theme-family unit intact; do **not**
        re-atomize multi-word theme phrases into species/DDA filter atoms.
        Use this path for ThemeSearchPlan primary_family deep recall.
    """

    normalized_mode = str(mode or "legacy_compound").strip().casefold()
    if normalized_mode not in {"legacy_compound", "theme_atomic"}:
        normalized_mode = "legacy_compound"

    used: set[str] = set()
    prepared: list[str] = []
    for query in queries:
        seeds_for_query = 0
        if normalized_mode == "theme_atomic":
            text = " ".join(str(query).strip().split())
            candidates = [text] if text else []
        else:
            candidates = _pride_query_seed_candidates(query)
        for candidate in candidates:
            if max_seeds_per_query is not None and seeds_for_query >= max_seeds_per_query:
                break
            key = re.sub(r"[-_\s]+", " ", candidate.casefold()).strip()
            if not key or key in used:
                continue
            used.add(key)
            prepared.append(candidate)
            seeds_for_query += 1
    return prepared


def classify_pride_query_strategy(queries: list[str]) -> str:
    normalized = [" ".join(str(query).strip().split()) for query in queries if str(query).strip()]
    prepared = prepare_pride_search_queries(normalized)
    normalized_keys = [re.sub(r"[-_\s]+", " ", value.casefold()).strip() for value in normalized]
    prepared_keys = [re.sub(r"[-_\s]+", " ", value.casefold()).strip() for value in prepared]
    if normalized_keys and normalized_keys == prepared_keys:
        return "atomic_seed"
    return "compound_semantic"


def build_pride_queries(request: DatasetRequest) -> list[str]:
    """Build PRIDE keyword queries for *theme recall*.

    Primary-theme deep search (PTS): species, acquisition, labeling, and task
    are **post-pool filters** on the request/FilterSpec — they are no longer
    expanded as equal shallow search seeds (no species×theme / DDA×theme peers).
    """

    plan = build_theme_search_plan(request)
    # Theme-only portfolio: full synonym family for recall breadth. Soft-cap applies
    # to must_exhaust / page-budget core (ThemeQueryUnit.must_exhaust), not to
    # deleting theme synonyms from the deterministic query list.
    queries = theme_family_queries(plan)
    return _dedupe([query for query in queries if query.strip()])



def species_aliases_for_query(species: str) -> list[str]:
    term = normalize_species(species)
    if term is not None:
        aliases = [term.scientific_name, term.canonical, *term.aliases]
    else:
        aliases = list(species_aliases(species))
    return _dedupe(aliases)[:4]
