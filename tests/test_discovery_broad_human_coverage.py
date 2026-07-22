from __future__ import annotations

from agent.discovery.models import DatasetRequest, DiscoveredFile, DiscoveredProject
from agent.discovery.diversity import select_diverse_items
from agent.discovery.scoring import score_project
from agent.pride.client import PrideClient
from agent.web import app as web_app


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def test_search_projects_pages_beyond_first_page(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_get(self, path, *, operation, params=None):
        page = int((params or {}).get("page") or 0)
        calls.append(dict(params or {}))
        if page == 0:
            return _FakeResponse([{"accession": f"PXD{i:06d}"} for i in range(1, 101)])
        if page == 1:
            return _FakeResponse([{"accession": f"PXD{i:06d}"} for i in range(101, 151)])
        return _FakeResponse([])

    monkeypatch.setattr(PrideClient, "_get", fake_get)
    client = PrideClient()
    projects = client.search_projects("human proteomics", page_size=100, max_pages=5)
    assert len(projects) == 150
    assert calls[0]["page"] == 0
    assert calls[1]["page"] == 1
    assert all(call.get("pageSize") == 100 for call in calls)


def test_prepare_broad_human_request_not_immunopeptidomics_and_not_tiny_cap(monkeypatch) -> None:
    # This test targets deterministic request normalization, not a live model.
    monkeypatch.setattr(
        web_app,
        "_run_discovery_goal_parse",
        lambda _body: (_ for _ in ()).throw(RuntimeError("No discovery LLM API key found")),
    )
    prepared = web_app._prepare_expert_pool_discovery_request(
        {
            "prompt": "尽可能多地寻找人类蛋白质组/肽组数据，越多越好",
            "output_language": "zh-CN",
            "scale_mode": "auto",
            "repository": "pride",
        }
    )
    request = prepared["request"]
    assert request["goal"] == "general"
    assert request.get("immunopeptide_scope") in {None, ""}
    assert "immunopeptidomics" not in str(request.get("goal") or "").casefold()
    assert request.get("harvest_all_qualified") is True
    assert request.get("quantity_scope") == "portfolio"
    assert str(request.get("portfolio_size_preference") or "").startswith("maximize")
    # Soft ambition should be large; not a tiny 20/300 hard stop.
    assert int(request["max_projects"]) >= 1000
    assert int(request["max_files_per_project"]) >= 300
    assert int(request["max_candidate_projects"]) >= 2000
    assert "human" in {str(item).casefold() for item in (request.get("species") or [])}
    assert request.get("species_policy") == "include_only"
    assert "repository" in (request.get("hard_constraint_fields") or [])
    assert "goal" not in (request.get("hard_constraint_fields") or [])


def test_maximize_mode_keeps_all_quality_projects_not_only_target_n() -> None:
    request = DatasetRequest(
        repository="pride",
        goal="general",
        quantity_scope="portfolio",
        portfolio_size_preference="maximize_qualified_projects",
        harvest_all_qualified=True,
        max_projects=20,
        max_files=2000,
        max_files_per_project=50,
        species=["human"],
        species_policy="include_only",
        hard_constraint_fields=["repository", "species", "species_policy"],
    )
    items = []
    for i in range(1, 61):
        acc = f"PXD{i:06d}"
        project = DiscoveredProject(
            project_accession=acc,
            project_score=80,
            confidence=0.9,
            species=["human"],
            canonical_species=["human"],
        )
        files = [
            DiscoveredFile(
                project_accession=acc,
                file_name=f"{acc}_{j}.raw",
                file_type=".raw",
                validity_status="valid",
                evidence_level="file",
                species=["human"],
                trust_score=0.8,
                file_score=50,
            )
            for j in range(2)
        ]
        items.append((project, files))
    selected = select_diverse_items(items, request)
    assert len(selected) == 60


def test_pure_mouse_project_excluded_under_human_include_only() -> None:
    request = DatasetRequest(
        repository="pride",
        goal="general",
        species=["human"],
        species_policy="include_only",
        hard_constraint_fields=["repository", "species", "species_policy"],
    )
    mouse_project = {
        "accession": "PXD069965",
        "title": "Mouse TAPBPR functions as an MHC-I peptide exchange catalyst",
        "projectDescription": "Mouse study discussing human TAPBPR comparisons.",
        "organisms": [{"name": "Mus musculus", "taxId": "10090"}],
    }
    score = score_project(mouse_project, request)
    assert score.excluded is True
    assert "10090" in " ".join(score.organism_taxon_id) or "mouse" in {
        item.casefold() for item in score.canonical_species
    }


def test_human_structured_taxid_keeps_human_project() -> None:
    request = DatasetRequest(
        repository="pride",
        goal="general",
        species=["human"],
        species_policy="include_only",
        hard_constraint_fields=["repository", "species", "species_policy"],
    )
    human_project = {
        "accession": "PXD000001",
        "title": "Human plasma proteomics",
        "projectDescription": "Shotgun proteomics of human plasma.",
        "organisms": [{"name": "Homo sapiens", "taxId": "9606"}],
    }
    score = score_project(human_project, request)
    assert score.excluded is False
    assert "human" in {item.casefold() for item in score.canonical_species}
