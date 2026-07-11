from __future__ import annotations

import importlib.util
from pathlib import Path


def _compiler_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "compile_discovery_blind_judgments.py"
    spec = importlib.util.spec_from_file_location("blind_judgment_compiler", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compiler_maps_blind_candidate_ids_to_variant_accessions() -> None:
    compiler = _compiler_module()
    reviewed = {
        "candidates": [
            {
                "candidate_id": "candidate_a",
                "grade": 3,
                "review_notes": "Direct match.",
                "reviewer_id": "reviewer_1",
            },
            {
                "candidate_id": "candidate_b",
                "grade": 1,
                "review_notes": "Related but too broad.",
                "reviewer_id": "reviewer_1",
            },
        ]
    }
    key = {
        "candidates": [
            {
                "candidate_id": "candidate_a",
                "scenario_id": "immunopeptidomics",
                "variant_id": "clear",
                "project_accession": "PXD000001",
            },
            {
                "candidate_id": "candidate_b",
                "scenario_id": "immunopeptidomics",
                "variant_id": "ambiguous",
                "project_accession": "PXD000001",
            },
        ]
    }

    compiled = compiler.compile_blind_judgments(reviewed, key)

    assert compiled["variant_relevance_judgments"] == {
        "immunopeptidomics": {
            "clear": {"PXD000001": 3},
            "ambiguous": {"PXD000001": 1},
        }
    }
    assert compiled["review_summary"]["complete"] is True


def test_compiler_rejects_ungraded_candidates_by_default() -> None:
    compiler = _compiler_module()
    reviewed = {"candidates": [{"candidate_id": "candidate_a", "grade": None}]}
    key = {
        "candidates": [
            {
                "candidate_id": "candidate_a",
                "scenario_id": "scenario",
                "variant_id": "clear",
                "project_accession": "PXD000001",
            }
        ]
    }

    try:
        compiler.compile_blind_judgments(reviewed, key)
    except ValueError as exc:
        assert "not graded" in str(exc)
    else:
        raise AssertionError("expected incomplete review to fail")
