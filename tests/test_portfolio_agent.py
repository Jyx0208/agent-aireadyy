from agent.discovery.models import DatasetRequest, DiscoveredFile
from agent.discovery.features import extract_file_features, extract_project_features
from agent.discovery.portfolio import (
    PortfolioSpec,
    assess_portfolio_coverage,
    infer_portfolio_spec,
    initialize_portfolio_state,
    select_portfolio_files,
    suggest_recovery_actions,
    update_portfolio_state,
)
from agent.web.app import _clean_dataset_request


def _file(project: str, index: int, **kwargs) -> DiscoveredFile:
    payload = {
        "project_accession": project,
        "file_name": f"{project}_{index}.raw",
        "file_type": "raw",
        "file_accession_or_path": f"{project}_{index}.raw",
        "file_score": 40,
        "confidence": 0.8,
        "trust_score": 0.8,
        "validity_status": "valid",
    }
    payload.update(kwargs)
    return DiscoveredFile(**payload)


def test_coverage_counts_only_observed_distinct_values_and_exposes_unknowns() -> None:
    rows = [
        _file(
            "PXD000001",
            1,
            species=["human"],
            canonical_species=["Homo sapiens"],
            instrument_families=["Orbitrap"],
            acquisition_mode="DDA",
            fragmentation_methods=["HCD"],
            raw_record={"laboratory": "Lab A"},
        ),
        _file(
            "PXD000002",
            1,
            species=["mouse"],
            canonical_species=["Mus musculus"],
            instrument_families=["timsTOF"],
            acquisition_mode="DIA",
            fragmentation_methods=["CID"],
            raw_record={"laboratory": "Lab B"},
        ),
        _file("PXD000003", 1),
    ]
    spec = PortfolioSpec(
        target_projects=3,
        target_files=3,
        min_distinct_labs=3,
        min_distinct_instruments=3,
        min_distinct_organisms=3,
        min_distinct_acquisition_modes=3,
        min_distinct_fragmentation_methods=3,
        hard_dimensions=["projects", "files", "labs", "instruments", "organisms"],
    )
    coverage = assess_portfolio_coverage(rows, spec)
    assert coverage.distinct_projects == 3
    assert coverage.dimension_counts["labs"] == 2
    assert coverage.unknown_counts["labs"] == 1
    assert {gap.dimension for gap in coverage.gaps} >= {
        "labs",
        "instruments",
        "organisms",
        "acquisition_modes",
        "fragmentation_methods",
    }
    assert coverage.hard_gap_count >= 3
    assert not coverage.ready


def test_recovery_actions_prioritize_hard_gaps_and_do_not_hide_unknowns() -> None:
    spec = PortfolioSpec(min_distinct_labs=2, hard_dimensions=["labs"])
    coverage = assess_portfolio_coverage([_file("PXD000001", 1)], spec)
    actions = suggest_recovery_actions(coverage, spec)
    assert actions
    assert actions[0].dimension == "labs"
    assert actions[0].kind == "inspect_metadata"
    assert actions[0].requires_approval is False
    assert any(action.kind == "relax_hard_requirement" and action.requires_approval for action in actions)


def test_freeze_state_is_blocked_until_hard_coverage_is_real() -> None:
    request = DatasetRequest(
        portfolio_spec={
            "target_projects": 2,
            "target_files": 2,
            "min_distinct_labs": 2,
            "hard_dimensions": ["projects", "files", "labs"],
        }
    )
    state = initialize_portfolio_state(request)
    rows = [_file("PXD000001", 1, raw_record={"laboratory": "Only Lab"}), _file("PXD000002", 1)]
    state = update_portfolio_state(state, rows)
    assert state.status == "needs_recovery"
    assert state.coverage is not None
    assert state.coverage.hard_gap_count == 1
    assert state.selected_project_accessions == ["PXD000001", "PXD000002"]


def test_selection_prefers_diversity_and_respects_allowed_extensions() -> None:
    rows = [
        _file("PXD000001", 1, raw_record={"laboratory": "A"}),
        _file("PXD000001", 2, file_name="PXD000001_2.mzML", file_type="mzML", raw_record={"laboratory": "A"}),
        _file("PXD000002", 1, raw_record={"laboratory": "B"}),
    ]
    spec = PortfolioSpec(target_files=2, target_projects=2, allowed_file_extensions=[".raw"])
    selected = select_portfolio_files(rows, spec)
    assert len(selected) == 2
    assert {row.project_accession for row in selected} == {"PXD000001", "PXD000002"}
    assert all(row.file_name.endswith(".raw") for row in selected)


def test_prompt_inference_is_conservative_but_supports_explicit_benchmark_request() -> None:
    payload = infer_portfolio_spec(
        "8 PRIDE projects, 16 files, at least 4 labs, at least 3 instruments, at least 3 species, prefer .raw or .mzML",
    )
    assert payload["target_projects"] == 8
    assert payload["target_files"] == 16
    assert payload["min_distinct_labs"] == 4
    assert payload["min_distinct_instruments"] == 3
    assert payload["min_distinct_organisms"] == 3
    assert payload["preferred_file_extensions"] == [".raw", ".mzml"]
    chinese = infer_portfolio_spec(
        "8 个左右 PRIDE 项目；约 16 个文件；至少 4 个实验室；至少 3 种仪器；至少 3 种采集或碎裂条件；优先 .raw 或 .mzML",
    )
    assert chinese["target_projects"] == 8
    assert chinese["target_files"] == 16
    assert chinese["min_distinct_labs"] == 4
    assert chinese["min_distinct_instruments"] == 3


def test_web_request_persists_portfolio_contract_from_natural_language() -> None:
    request = _clean_dataset_request(
        {
            "prompt": "8 个左右 PRIDE 项目，约 16 个文件，至少 4 个实验室，至少 3 种仪器，优先 .raw 或 .mzML",
            "repository": "pride",
            "goal": "general",
        }
    )
    assert request.portfolio_spec["target_projects"] == 8
    assert request.portfolio_spec["target_files"] == 16
    assert request.portfolio_spec["min_distinct_labs"] == 4
    assert "labs" in request.portfolio_spec["hard_dimensions"]


def test_range_parser_and_project_file_bounds_are_preserved() -> None:
    payload = infer_portfolio_spec("8 projects, 16 files, 1-2 files per project")
    assert payload["min_files_per_project"] == 1
    assert payload["max_files_per_project"] == 2
    assert payload["target_files"] == 16


def test_current_unicode_benchmark_prompt_preserves_full_contract() -> None:
    prompt = (
        "\u9009\u62e9\u7ea6 8 \u4e2a PRIDE \u9879\u76ee\uff1b"
        "\u603b\u5171\u7ea6 16 \u4e2a\u6587\u4ef6\uff0c"
        "\u6bcf\u4e2a\u9879\u76ee\u9009\u62e9 1\u20132 \u4e2a\u6587\u4ef6\uff1b"
        "\u81f3\u5c11\u8986\u76d6 4 \u4e2a\u4e0d\u540c\u5b9e\u9a8c\u5ba4\u6216\u673a\u6784\uff1b"
        "\u81f3\u5c11\u8986\u76d6 3 \u4e2a\u4e0d\u540c\u4eea\u5668\u5bb6\u65cf\uff1b"
        "\u81f3\u5c11\u8986\u76d6 3 \u4e2a\u4e0d\u540c\u7269\u79cd\uff1b"
        "\u81f3\u5c11\u8986\u76d6 3 \u79cd\u4e0d\u540c\u7684\u91c7\u96c6\u6216\u788e\u88c2\u6761\u4ef6\uff1b"
        "\u6587\u4ef6\u4f18\u5148\u9009\u62e9 .raw\u3001.mzML \u6216 .mzML.gz"
    )
    payload = infer_portfolio_spec(prompt)
    assert payload["target_projects"] == 8
    assert payload["target_files"] == 16
    assert payload["min_files_per_project"] == 1
    assert payload["max_files_per_project"] == 2
    assert payload["min_distinct_labs"] == 4
    assert payload["min_distinct_instruments"] == 3
    assert payload["min_distinct_organisms"] == 3
    assert payload["min_distinct_acquisition_or_fragmentation"] == 3
    assert "file_extensions" not in payload["hard_dimensions"]
    request = _clean_dataset_request(
        {"prompt": prompt, "repository": "pride", "goal": "general", "quota_flexibility": "fixed"}
    )
    assert request.max_projects == 8
    assert request.max_files == 16
    assert request.max_files_per_project == 2
    assert request.per_project_min_files == 1


def test_selector_never_adds_projects_or_files_beyond_bounds() -> None:
    rows = [
        _file("PXD000001", 1),
        _file("PXD000001", 2),
        _file("PXD000001", 3),
        _file("PXD000002", 1),
        _file("PXD000002", 2),
        _file("PXD000003", 1),
    ]
    spec = PortfolioSpec(
        target_projects=2,
        target_files=4,
        min_files_per_project=1,
        max_files_per_project=2,
    )
    selected = select_portfolio_files(rows, spec)
    assert len(selected) == 4
    assert len({row.project_accession for row in selected}) == 2
    assert max(sum(row.project_accession == project for row in selected) for project in {"PXD000001", "PXD000002"}) <= 2


def test_strict_portfolio_requires_replayable_file_evidence() -> None:
    row = _file("PXD000001", 1, download_url="https://example.test/a.raw", expected_size_bytes=10)
    strict = PortfolioSpec(target_files=1, detail_level="strict")
    assert select_portfolio_files([row], strict) == []
    row = row.model_copy(update={"evidence": [{"field": "download_url", "source": "pride", "text": "url"}]})
    assert select_portfolio_files([row], strict)


def test_laboratory_names_are_extracted_from_explicit_project_and_sdrf_fields() -> None:
    features = extract_project_features(
        {
            "laboratory": "Core A",
            "institutionName": "Institute B",
            "labPIs": [{"affiliation": "Institute C"}],
            "submitters": [{"affiliation": "Institute D"}],
            "instruments": [],
        },
        [{"file name": "a.raw", "comment[laboratory]": "Core C"}],
    )
    assert features.laboratory_names == [
        "Core A",
        "Institute B",
        "Institute C",
        "Institute D",
        "Core C",
    ]


def test_homogeneous_project_instrument_is_explicitly_inherited_to_files() -> None:
    project = {"instruments": [{"name": "Orbitrap Fusion Lumos"}]}
    project_features = extract_project_features(project)
    file_features = extract_file_features(
        {"fileName": "sample.raw"},
        project_features,
        inherit_homogeneous_project_instrument=True,
    )
    assert file_features.instrument_families == ["orbitrap"]
    assert "Orbitrap Fusion Lumos" in file_features.instrument_names
    assert any(item.source == "project_instrument_inherited" for item in file_features.evidence)
