from agent.discovery.constraints import (
    ScientificConstraint,
    constraint_may_be_hard,
    may_be_hard,
    normalize_constraint_bindings,
    normalize_constraint_bindings_result,
    normalize_scientific_constraints,
    normalize_scientific_constraints_result,
)
from agent.discovery.ontology import normalize_labeling_strategy, normalize_ptm_type
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile
from agent.discovery.task_readiness import pipeline_eligible_files, task_ready_files


def test_normalize_constraints_result_rejects_invalid_without_accepting():
    result = normalize_scientific_constraints_result(
        [
            {
                "id": "ok1",
                "label": "Human only",
                "dimension": "species",
                "value": "human",
                "strength": "hard",
                "source": "user",
            },
            {"id": "bad", "label": "", "dimension": "species"},
            "not-an-object",
        ]
    )
    assert len(result.accepted) == 1
    assert result.accepted[0].id == "ok1"
    assert len(result.rejected) >= 1
    assert not result.ok


def test_normalize_constraints_non_list_is_rejected():
    result = normalize_scientific_constraints_result({"nope": True})
    assert result.accepted == []
    assert result.rejected
    assert normalize_scientific_constraints({"nope": True}) == []


def test_normalize_constraints_does_not_silent_drop_counts():
    raw = [
        {
            "id": "a",
            "label": "A",
            "dimension": "species",
            "value": "human",
            "source": "user",
        },
        {"id": "b", "label": "", "dimension": "species"},
        {
            "id": "c",
            "label": "C",
            "dimension": "acquisition_mode",
            "value": "dda",
            "source": "user",
        },
    ]
    result = normalize_scientific_constraints_result(raw)
    assert len(result.accepted) + len(result.rejected) == len(raw)


def test_ontology_empty_does_not_invent_concrete_science():
    assert normalize_ptm_type(None) == "unknown_ptm"
    assert normalize_ptm_type("") == "unknown_ptm"
    assert normalize_ptm_type(None) != "phospho"
    assert normalize_labeling_strategy(None) == "unknown"
    assert normalize_labeling_strategy("") == "unknown"
    assert normalize_labeling_strategy(None) != "label_free"


def test_task_ready_files_excludes_weak_ready_but_pipeline_eligible_keeps_l1():
    files = [
        DiscoveredFile(
            project_accession="PXD1",
            file_name="a.raw",
            file_type="raw",
            task_readiness_status="ready",
        ),
        DiscoveredFile(
            project_accession="PXD1",
            file_name="b.raw",
            file_type="raw",
            task_readiness_status="weak_ready",
        ),
    ]
    manifest = DatasetManifest(request=DatasetRequest(), run_id="t", repository="pride", files=files)
    assert [f.file_name for f in task_ready_files(manifest)] == ["a.raw"]
    assert [f.file_name for f in pipeline_eligible_files(manifest)] == ["a.raw", "b.raw"]
    summary = {
        "task_ready_files": sum(1 for f in files if f.task_readiness_status == "ready"),
        "pipeline_eligible_files": sum(
            1 for f in files if f.task_readiness_status in {"ready", "weak_ready"}
        ),
    }
    assert summary["task_ready_files"] == 1
    assert summary["pipeline_eligible_files"] == 2


def test_inferred_provenance_cannot_be_hard():
    assert may_be_hard("user")
    assert may_be_hard("accepted_recommendation")
    assert not may_be_hard("inferred")
    assert not may_be_hard(None)
    assert not may_be_hard("")

    result = normalize_scientific_constraints_result(
        [
            {
                "id": "inf1",
                "label": "Inferred hard",
                "dimension": "species",
                "value": "human",
                "strength": "hard",
                "source": "inferred",
            },
            {
                "id": "miss",
                "label": "Missing source hard",
                "dimension": "species",
                "value": "mouse",
                "strength": "hard",
            },
        ]
    )
    assert result.ok
    assert all(item.strength == "soft" for item in result.accepted)
    assert all(not constraint_may_be_hard(item) for item in result.accepted)


def test_binding_inferred_hard_is_softened_and_invalids_are_audited():
    result = normalize_constraint_bindings_result(
        [
            {
                "dimension": "labeling_strategy",
                "value": "label_free",
                "strength": "hard",
                "source": "system_default",
            },
            {
                "dimension": "acquisition_mode",
                "value": "dda",
                "strength": "hard",
                "source": "user",
            },
            {"value": "no-dimension"},
        ]
    )
    assert len(result.rejected) == 1
    accepted = {item.dimension: item for item in result.accepted}
    assert accepted["labeling_strategy"].strength == "soft"
    assert accepted["acquisition_mode"].strength == "hard"
    # list adapter still returns accepted only
    bindings = normalize_constraint_bindings(
        [
            {
                "dimension": "labeling_strategy",
                "value": "label_free",
                "strength": "hard",
                "source": "system_default",
            }
        ]
    )
    assert bindings[0].strength == "soft"


def test_user_hard_constraint_remains_hard():
    constraint = ScientificConstraint(
        id="u1",
        label="User hard",
        dimension="species",
        value="human",
        strength="hard",
        source="user",
    )
    assert constraint_may_be_hard(constraint)
