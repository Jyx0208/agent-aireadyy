from __future__ import annotations

import json
from pathlib import Path

from agent.discovery.manifest import write_dataset_manifest
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile
from agent.discovery.task_profiles import active_task_types
from agent.discovery.task_readiness import annotate_manifest_task_readiness


def _candidate(
    name: str,
    *,
    validity_status: str = "valid",
    species: list[str] | None = None,
    instrument: list[str] | None = None,
    fragmentation: list[str] | None = None,
    size: int | None = 50 * 1024 * 1024,
) -> DiscoveredFile:
    return DiscoveredFile(
        project_accession="PXDTEST001",
        file_name=name,
        download_url=f"https://example.test/{name}",
        file_type=Path(name).suffix or ".mzML",
        file_role="raw_acquisition",
        species=species or ["human"],
        canonical_species=species or ["human"],
        organism_taxon_id=["9606"] if (species or ["human"]) == ["human"] else [],
        acquisition_mode="dda",
        ptm_type="phospho",
        modification_scope="phospho",
        labeling_strategy="label_free",
        instrument_families=instrument or ["orbitrap"],
        fragmentation_methods=fragmentation or ["HCD"],
        lc_gradient_minutes=60.0,
        expected_size_bytes=size,
        file_score=70,
        trust_score=0.9,
        evidence_completeness=0.85,
        validity_status=validity_status,  # type: ignore[arg-type]
        diversity_tags=["species:human", "instrument:orbitrap", "fragmentation:HCD"],
    )


def test_task_ai_readiness_and_data_value_score_complete_candidate_higher(tmp_path: Path) -> None:
    complete = _candidate("complete.mzML")
    incomplete = _candidate(
        "incomplete.raw",
        validity_status="needs_review",
        species=[],
        instrument=[],
        fragmentation=[],
        size=3 * 1024 * 1024 * 1024,
    )
    manifest = DatasetManifest(
        request=DatasetRequest(species=["human"], ptm_type="phospho"),
        files=[complete, incomplete],
    )

    annotated = annotate_manifest_task_readiness(manifest, "fragment_intensity_prediction")
    by_name = {file.file_name: file for file in annotated.files}

    assert by_name["complete.mzML"].task_ai_readiness_score is not None
    assert by_name["complete.mzML"].task_ai_readiness_score > by_name["incomplete.raw"].task_ai_readiness_score
    assert by_name["complete.mzML"].data_value_score > by_name["incomplete.raw"].data_value_score
    assert by_name["complete.mzML"].data_value_action in {"process", "review"}
    assert by_name["incomplete.raw"].task_ai_readiness_band in {"review", "blocked"}
    assert annotated.summary["task_ai_readiness_v2"]["scored_files"] == 2
    assert "data_value_v1" in annotated.summary

    paths = write_dataset_manifest(annotated, tmp_path / "manifest")
    assert paths["task_ai_readiness_matrix_json"].exists()
    matrix = json.loads(paths["task_ai_readiness_matrix_json"].read_text(encoding="utf-8"))
    assert matrix["matrix_mode"] == "all_active_tasks"
    assert set(matrix["task_types"]) == set(active_task_types())
    assert len(matrix["rows"]) == len(annotated.files) * len(active_task_types())
    assert paths["data_value_ranking_csv"].exists()
    ranking = json.loads(paths["data_value_ranking_json"].read_text(encoding="utf-8"))
    assert ranking["rows"][0]["file_name"] == "complete.mzML"


def test_tmt_labeling_warns_but_keeps_fragment_readiness() -> None:
    file = _candidate("tmt.mzML")
    file = file.model_copy(update={"labeling_strategy": "TMT"})
    manifest = DatasetManifest(
        request=DatasetRequest(species=["human"], ptm_type="acetyl", labeling_strategy="TMT"),
        files=[file],
    )

    annotated = annotate_manifest_task_readiness(manifest, "fragment_intensity_prediction")
    scored = annotated.files[0]

    assert scored.task_ai_readiness_band in {"ready", "weak_ready"}
    assert any("isobaric_labeling" in warning for warning in scored.task_ai_readiness_warnings)


def test_unrequested_tmt_is_weak_but_allowed() -> None:
    file = _candidate("tmt_unrequested.mzML").model_copy(update={"labeling_strategy": "TMT"})
    manifest = DatasetManifest(
        request=DatasetRequest(species=["human"], ptm_type="phospho", labeling_strategy="label_free"),
        files=[file],
    )

    annotated = annotate_manifest_task_readiness(manifest, "fragment_intensity_prediction")
    scored = annotated.files[0]

    assert scored.task_ai_readiness_band == "weak_ready"
    assert scored.data_value_action in {"review", "find_alternative"}
    assert "labeling_weak_for_task" in scored.task_ai_readiness_reasons
    assert "isobaric_labeling_not_first_choice_for_task" in scored.task_ai_readiness_warnings
