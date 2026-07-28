"""Primary-theme deep search (PTS) — vertical slice tests."""
from __future__ import annotations

from agent.discovery.models import DatasetRequest
from agent.discovery.query_builder import (
    PRIMARY_THEME_CORE_SOFT_CAP,
    build_pride_queries,
    build_theme_search_plan,
    prepare_pride_search_queries,
    theme_family_queries,
)
from agent.discovery.query_portfolio import build_query_portfolio_units
from agent.discovery.search_environment import (
    CandidateSearchAction,
    PrideDiscoverySearchEnvironment,
    RepositoryQuery,
)


def test_theme_plan_immuno_mouse_dda_filters_not_seeds():
    request = DatasetRequest(
        goal="immunopeptidomics",
        species=["mouse"],
        acquisition_mode="dda",
        # Soft suitability (PSM scoring) lives on success_criteria — not a PRIDE seed.
        success_criteria=["psm_scoring"],
        max_projects=20,
        max_candidate_projects=200,
    )
    plan = build_theme_search_plan(request)
    assert plan.primary_theme_id == "immunopeptidomics"
    family = theme_family_queries(plan)
    assert family
    # Full synonym family may exceed soft-cap; only must_exhaust core is capped.
    core_queries = theme_family_queries(plan, must_exhaust_only=True)
    assert 1 <= len(core_queries) <= PRIMARY_THEME_CORE_SOFT_CAP
    joined = " | ".join(family).casefold()
    assert "immunopeptid" in joined or "hla" in joined or "mhc" in joined
    # Filters recorded but not as family peers
    assert plan.filters.species
    assert str(plan.filters.acquisition_mode or "").casefold() == "dda"
    assert "psm_scoring" in (plan.filters.soft_preferences or [])
    # Soft-cap: only a thin core is must_exhaust primary_theme
    core = [u for u in plan.primary_family if u.must_exhaust]
    assert 1 <= len(core) <= PRIMARY_THEME_CORE_SOFT_CAP
    assert all(u.role == "primary_theme" for u in core)
    assert all(u.family_rank == i for i, u in enumerate(plan.primary_family))
    # build_pride_queries is theme-only + soft-capped core
    queries = build_pride_queries(request)
    assert queries
    blob = " ".join(queries).casefold()
    assert "immunopeptid" in blob or "hla" in blob or "ligand" in blob
    assert "mus musculus" not in blob
    assert not any(q.strip().casefold() == "dda" for q in queries)
    assert not any(q.strip().casefold() == "mouse" for q in queries)


def test_theme_plan_chinese_immuno_goal_detects_primary_theme():
    request = DatasetRequest(goal="免疫肽", species=["mouse"], acquisition_mode="dda")
    plan = build_theme_search_plan(request)
    assert plan.primary_theme_id == "immunopeptidomics"
    assert theme_family_queries(plan)


def test_prepare_still_atomizes_agent_compound_strings():
    seeds = prepare_pride_search_queries(["human DDA phospho"])
    assert set(s.casefold() for s in seeds) >= {"human", "dda", "phospho"}


def test_prepare_theme_atomic_keeps_multiword_theme_phrases():
    seeds = prepare_pride_search_queries(
        ["HLA ligandome", "human DDA phospho"],
        mode="theme_atomic",
    )
    assert seeds == ["HLA ligandome", "human DDA phospho"]
    # Must not re-inject filter atoms from theme phrases
    assert "DDA" not in seeds
    assert "human" not in seeds


def test_repository_portfolio_keeps_hla_class_phrases_distinct_and_intact():
    units = build_query_portfolio_units([
        RepositoryQuery(query="HLA class I ligandome", depth=150),
        RepositoryQuery(query="HLA class II ligandome", depth=150),
    ])

    assert units[0].seeds_planned == ["HLA class I ligandome"]
    assert units[1].seeds_planned == ["HLA class II ligandome"]


def test_primary_seed_soft_cap_marks_overflow_as_synonym():
    request = DatasetRequest(goal="immunopeptidomics", species=["mouse"], acquisition_mode="dda")
    plan = build_theme_search_plan(request, primary_seed_soft_cap=3)
    assert plan.primary_seed_soft_cap == 3
    assert sum(1 for u in plan.primary_family if u.must_exhaust) == 3
    assert theme_family_queries(plan, must_exhaust_only=True) == [
        u.text for u in plan.primary_family if u.must_exhaust
    ]
    if len(plan.primary_family) > 3:
        assert any(u.role == "theme_synonym" for u in plan.primary_family)


def test_role_weighted_search_skips_filter_only_and_prefers_primary(tmp_path):
    class Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int | None, int | None]] = []
            self.page_requests = 0

        def search_projects(
            self,
            keyword: str,
            page_size: int = 100,
            page: int = 0,
            *,
            max_pages: int | None = None,
            max_results: int | None = None,
        ):
            pages = max(1, int(max_pages or 1))
            self.calls.append((keyword, page_size, pages))
            self.page_requests += pages
            rows = []
            limit = max_results if max_results is not None else pages * max(1, page_size)
            for i in range(pages):
                if len(rows) >= limit:
                    break
                rows.append(
                    {
                        "accession": f"PXD{self.page_requests:04d}{i:02d}",
                        "title": f"{keyword} study page {i}",
                        "projectDescription": keyword,
                    }
                )
            return rows[:limit]

        def close(self) -> None:
            return None

    client = Client()
    env = PrideDiscoverySearchEnvironment(
        client=client,
        prompt="mouse immunopeptidomics DDA for PSM",
        state_path=tmp_path / "search_state.json",
        request=DatasetRequest(
            goal="immunopeptidomics",
            species=["mouse"],
            acquisition_mode="dda",
            max_candidate_projects=50,
            max_projects=5,
        ),
    )
    action = CandidateSearchAction(
        queries=[
            RepositoryQuery(
                query="immunopeptidomics",
                depth=40,
                intent_dimension="scientific_theme",
                budget_role="primary_theme",
            ),
            RepositoryQuery(
                query="mouse",
                depth=40,
                intent_dimension="species",
                budget_role="filter_only",
            ),
            RepositoryQuery(
                query="DDA",
                depth=40,
                intent_dimension="acquisition",
                budget_role="filter_only",
            ),
        ],
        candidate_limit=50,
        rationale="pts vertical slice",
    )
    env.search_with_request_budget(action, request_budget=20)
    keywords = [k for k, *_rest in client.calls]
    assert any("immunopeptidomics" in k.casefold() for k in keywords)
    assert not any(k.strip().casefold() == "mouse" for k in keywords)
    assert not any(k.strip().casefold() == "dda" for k in keywords)
    # Primary should receive multi-page budget under role-weighted allocation.
    primary_pages = sum(
        int(pages or 0)
        for k, _ps, pages in client.calls
        if "immunopeptidomics" in str(k).casefold()
    )
    assert primary_pages >= 2
    assert client.page_requests >= 2
