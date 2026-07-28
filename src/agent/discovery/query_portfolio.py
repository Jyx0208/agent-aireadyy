"""Auditable multi-seed query portfolio for PRIDE discovery (WP-A / PTS)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from agent.discovery.query_builder import (
    _pride_query_seed_candidates,
    classify_pride_query_strategy,
    prepare_pride_search_queries,
)
from agent.models import JsonModel


QueryUnitStatus = Literal[
    "executed",
    "skipped_budget",
    "skipped_dedupe",
    "skipped_depth",
    "failed",
    "planned",
]
MAX_REPOSITORY_QUERY_DEPTH = 2000

# PTS budget roles (plan §3.4). Missing/unknown must not silently become primary depth.
PRIMARY_BUDGET_ROLES = frozenset(
    {
        "primary_theme",
        "theme_synonym",
        "secondary_theme",
        "scientific_theme",
    }
)
FILTER_BUDGET_ROLES = frozenset(
    {
        "filter_only",
        "filter",
        "species",
        "acquisition",
        "labeling",
        "soft_suitability",
    }
)
BudgetRole = Literal[
    "primary_theme",
    "theme_synonym",
    "secondary_theme",
    "secondary_axis",
    "filter_only",
    "rescue_filter_fused",
    "exact_accession",
    "general",
    "unknown",
]


class QueryUnit(JsonModel):
    """One approved/planned repository query expanded into atomic seeds."""

    text: str = Field(min_length=1, max_length=240)
    seeds_planned: list[str] = Field(default_factory=list)
    seeds_executed: list[str] = Field(default_factory=list)
    strategy: str = "compound_semantic"
    target_constraint_ids: list[str] = Field(default_factory=list)
    intent_dimension: str = Field(default="general", min_length=1, max_length=120)
    # PTS: role for page budget. Fail closed — missing → not primary (see resolve_budget_role).
    budget_role: str = Field(default="unknown", min_length=1, max_length=64)
    depth: int = Field(default=20, ge=1, le=MAX_REPOSITORY_QUERY_DEPTH)
    status: QueryUnitStatus = "planned"
    yield_counts: dict[str, int] = Field(default_factory=dict)
    not_executed_reason: str | None = None


class QueryPortfolio(JsonModel):
    """Portfolio of query units with seed-level execution audit."""

    units: list[QueryUnit] = Field(default_factory=list)
    executed_seed_count: int = Field(default=0, ge=0)
    skipped_seed_count: int = Field(default=0, ge=0)
    failed_seed_count: int = Field(default=0, ge=0)

    def record_summary(self) -> dict[str, Any]:
        return {
            "unit_count": len(self.units),
            "executed_seed_count": self.executed_seed_count,
            "skipped_seed_count": self.skipped_seed_count,
            "failed_seed_count": self.failed_seed_count,
            "units": [unit.model_dump(mode="json") for unit in self.units],
        }


def resolve_budget_role(
    *,
    budget_role: str | None = None,
    intent_dimension: str | None = None,
    query_text: str | None = None,
) -> str:
    """Map explicit role / intent / text into a budget_role.

    Fail closed: unknown roles are **not** treated as primary_theme depth.
    Explicit filter / species / acquisition intents become filter_only.
    """

    explicit = " ".join(str(budget_role or "").strip().casefold().split())
    # Soft placeholders: let intent / accession detection win.
    soft_explicit = explicit in {"", "unknown", "general"}
    if explicit and not soft_explicit:
        if explicit in FILTER_BUDGET_ROLES or explicit.startswith("filter"):
            return "filter_only"
        if explicit in {
            "primary_theme",
            "theme_synonym",
            "secondary_theme",
            "secondary_axis",
            "scientific_theme",
            "rescue_filter_fused",
            "exact_accession",
        }:
            # scientific_theme is an intent alias for primary theme depth
            if explicit == "scientific_theme":
                return "primary_theme"
            return explicit
        # Unknown explicit label → not primary
        return "unknown"

    intent = " ".join(str(intent_dimension or "").strip().casefold().split())
    if intent in FILTER_BUDGET_ROLES or intent in {
        "species_filter",
        "acquisition_filter",
        "hard_filter",
        "soft_filter",
    }:
        return "filter_only"
    if intent in {"scientific_theme", "primary_theme", "theme", "theme_synonym"}:
        return "primary_theme" if intent != "theme_synonym" else "theme_synonym"
    if intent in {"secondary_theme", "secondary_axis"}:
        return intent
    if intent == "exact_accession" or (
        query_text and re.fullmatch(r"PXD\d+", str(query_text).strip(), flags=re.IGNORECASE)
    ):
        return "exact_accession"
    if intent in {"general", "cell model", "disease context", ""}:
        # Legacy agent queries without PTS roles: searchable general (not filter).
        # Still not elevated to primary_theme so allocator can prefer explicit primaries.
        return "general" if intent else "unknown"
    if intent:
        # Named but non-filter intent → general depth, not automatic primary
        return "general"
    if explicit == "general":
        return "general"


def is_filter_budget_role(role: str | None) -> bool:
    value = " ".join(str(role or "").casefold().split())
    return value in FILTER_BUDGET_ROLES or value.startswith("filter")


def is_primary_budget_role(role: str | None) -> bool:
    value = " ".join(str(role or "").casefold().split())
    return value in PRIMARY_BUDGET_ROLES



_ACQUISITION_SEED_TOKENS = frozenset({
    "dda", "dia", "data-dependent", "data dependent", "data-independent", "data independent",
})
_LABELING_SEED_TOKENS = frozenset({
    "tmt", "itraq", "silac", "label-free", "label free", "lfq",
})


def classify_atomic_seed_budget_role(
    seed: str,
    *,
    parent_role: str | None = None,
    filter_tokens: set[str] | None = None,
) -> str:
    """Re-role atomized seeds so bare species/DDA tokens are not deep-search peers.

    Parent compound queries like "immunopeptidomics human DDA" expand into
    immunopeptidomics (primary) + human/DDA (filter_only).
    """

    text = " ".join(str(seed or "").strip().casefold().split())
    if not text:
        return "unknown"
    if re.fullmatch(r"pxd\d+", text, flags=re.IGNORECASE):
        return "exact_accession"
    parent = " ".join(str(parent_role or "").casefold().split())
    if parent in FILTER_BUDGET_ROLES or parent.startswith("filter"):
        return "filter_only"
    tokens = filter_tokens or set()
    if text in tokens or text in _ACQUISITION_SEED_TOKENS or text in _LABELING_SEED_TOKENS:
        return "filter_only"
    # Multi-word seeds that are pure acquisition phrases
    if text in {"data dependent", "data independent", "label free"}:
        return "filter_only"
    if parent in PRIMARY_BUDGET_ROLES or parent in {"scientific_theme", "theme"}:
        return "primary_theme" if parent != "theme_synonym" else "theme_synonym"
    if parent in {"secondary_theme", "secondary_axis"}:
        return parent
    # Bare general agent seed: keep searchable general (not primary elevation)
    return resolve_budget_role(budget_role=parent or None, intent_dimension=None, query_text=seed)


def expand_query_unit_seeds(
    query: str,
    *,
    max_seeds: int | None = None,
    mode: str = "theme_atomic",
) -> list[str]:
    """Prepare one approved repository query without destroying phrase semantics."""

    seeds = prepare_pride_search_queries([query], mode=mode)
    if not seeds:
        text = " ".join(str(query).strip().split())
        seeds = [text] if text else []
    if max_seeds is not None:
        return seeds[: max(0, int(max_seeds))]
    return seeds


def build_query_portfolio_units(
    queries: list[tuple[str, int, str]] | list[Any],
    *,
    target_constraint_ids: list[str] | None = None,
    max_seeds_per_query: int | None = 8,
) -> list[QueryUnit]:
    """Build portfolio units from (text, depth, intent_dimension) tuples or query objects."""

    units: list[QueryUnit] = []
    constraint_ids = list(target_constraint_ids or [])
    for item in queries:
        budget_role_raw: str | None = None
        if hasattr(item, "query"):
            text = str(item.query)
            depth = int(getattr(item, "depth", 20) or 20)
            intent = str(getattr(item, "intent_dimension", "general") or "general")
            budget_role_raw = getattr(item, "budget_role", None)
        else:
            text, depth, intent = item[0], int(item[1]), str(item[2])
            if len(item) > 3:
                budget_role_raw = item[3]
        text = " ".join(text.strip().split())
        if not text:
            continue
        role = resolve_budget_role(
            budget_role=str(budget_role_raw) if budget_role_raw is not None else None,
            intent_dimension=intent,
            query_text=text,
        )
        # filter_only units keep text for audit but do not expand into deep page peers
        if is_filter_budget_role(role):
            seeds = [text]
            strategy = "filter_only"
        else:
            seeds = expand_query_unit_seeds(text, max_seeds=max_seeds_per_query)
            strategy = classify_pride_query_strategy([text])
            if len(seeds) > 1:
                strategy = "compound_semantic"
            elif seeds and " ".join(seeds[0].casefold().split()) == text.casefold():
                strategy = "atomic_seed"
        units.append(
            QueryUnit(
                text=text,
                seeds_planned=seeds,
                seeds_executed=[],
                strategy=strategy,
                target_constraint_ids=list(constraint_ids),
                intent_dimension=intent,
                budget_role=role,
                depth=depth,
                status="planned",
            )
        )
    return units


def portfolio_seed_keys(units: list[QueryUnit]) -> list[str]:
    """Flatten planned seeds with stable dedupe order for budget accounting."""

    seen: set[str] = set()
    keys: list[str] = []
    for unit in units:
        if is_filter_budget_role(unit.budget_role):
            continue
        for seed in unit.seeds_planned or _pride_query_seed_candidates(unit.text):
            key = " ".join(str(seed).casefold().split())
            if not key or key in seen:
                continue
            seen.add(key)
            keys.append(key)
    return keys
