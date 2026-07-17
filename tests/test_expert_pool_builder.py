from __future__ import annotations

from agent.web.expert_review.pool_builder import build_blinded_pool_from_discovery


def _record() -> dict:
    return {
        "runtime": "openai_agents",
        "projects": [
            {
                "project_accession": "PXD000001",
                "project_title": "Visible title",
                "project_description": "Visible description",
                "species": ["Homo sapiens"],
                "acquisition_mode": "dda",
                "labeling_strategy": "label_free",
                "instrument_families": ["Orbitrap"],
                "fragmentation_methods": ["HCD"],
                "evidence_completeness": 0.8,
                "source_system": "private",
                "metadata": {"runtime": "workflow", "safe": "ok"},
            },
            {
                "project_accession": "pxd000001",
                "project_title": "Short duplicate",
                "project_description": "duplicate",
            },
            {
                "project_accession": "PXD000002",
                "project_title": "Second",
                "project_description": "Second description",
            },
        ],
        "files": [
            {
                "project_accession": "PXD000001",
                "file_name": "one.raw",
                "file_role": "raw_acquisition",
                "file_type": "RAW",
                "task_readiness_status": "ready",
                "missing_task_requirements": [],
            },
            {
                "project_accession": "PXD000001",
                "file_name": "one.mzid",
                "file_role": "search_result",
                "file_type": "mzIdentML",
                "task_readiness_status": "partial",
                "missing_task_requirements": ["sdrf"],
            },
        ],
    }


def test_build_blinded_pool_allowlists_deduplicates_and_aggregates_files() -> None:
    pool, private_key = build_blinded_pool_from_discovery(
        _record(),
        prompt="Find human label-free DDA proteomics",
        build_id="build-123",
        visible_constraints={"species": ["Homo sapiens"], "runtime": "hidden"},
    )

    assert pool["schema_version"] == "discovery-judgment-pool-blinded/v2"
    assert len(pool["candidates"]) == 2
    first = pool["candidates"][0]
    assert first["project_title"] == "Visible title"
    assert first["selected_file_count"] == 2
    assert first["file_role_counts"] == {"raw_acquisition": 1, "search_result": 1}
    assert first["paired_raw_and_results"] is True
    assert first["missing_task_requirements"] == ["sdrf"]
    assert "project_accession" not in first
    assert "source_system" not in first
    assert "runtime" not in str(pool)
    task = next(iter(pool["tasks"].values()))
    assert task["visible_prompt"] == "Find human label-free DDA proteomics"
    assert task["visible_constraints"] == {"species": ["Homo sapiens"]}

    assert len(private_key["candidates"]) == 2
    assert private_key["candidates"][0]["project_accession"] == "PXD000001"


def test_build_blinded_pool_ids_are_stable_for_same_build() -> None:
    first, _ = build_blinded_pool_from_discovery(_record(), prompt="p", build_id="same")
    second, _ = build_blinded_pool_from_discovery(_record(), prompt="p", build_id="same")
    assert [item["candidate_id"] for item in first["candidates"]] == [
        item["candidate_id"] for item in second["candidates"]
    ]


def test_build_blinded_pool_rejects_empty_projects() -> None:
    try:
        build_blinded_pool_from_discovery({"projects": [], "files": []}, prompt="p", build_id="empty")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "no_candidates" in str(exc)


def test_portfolio_quantity_prompt_does_not_create_per_project_size_penalty() -> None:
    pool, _ = build_blinded_pool_from_discovery(
        _record(),
        prompt="免疫肽数据集，越多越好",
        build_id="portfolio-quantity",
    )

    task = next(iter(pool["tasks"].values()))
    semantics = task["task_semantics"]
    assert semantics["quantity_scope"] == "portfolio"
    assert semantics["portfolio_size_preference"] == "maximize_total_usable_items"
    assert semantics["per_project_minimum"] is None
    assert semantics["penalize_small_project"] is False
    assert pool["candidates"][0]["task_semantics"] == semantics


def test_explicit_per_project_minimum_is_preserved() -> None:
    pool, _ = build_blinded_pool_from_discovery(
        _record(),
        prompt="免疫肽数据集，每个项目至少 20 个文件",
        build_id="per-project-quantity",
    )

    semantics = next(iter(pool["tasks"].values()))["task_semantics"]
    assert semantics["quantity_scope"] == "per_project"
    assert semantics["per_project_minimum"] == {"value": 20, "unit": "files"}
    assert semantics["penalize_small_project"] is True
