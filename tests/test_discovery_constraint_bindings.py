from __future__ import annotations

from typing import Any

import pytest

from agent.discovery import constraints as constraint_module
from agent.discovery.constraints import ScientificConstraint


def _normalize(value: list[dict[str, Any]]) -> list[ScientificConstraint]:
    normalize = getattr(constraint_module, "normalize_constraint_bindings", None)
    if not callable(normalize):
        pytest.fail(
            "WAVE 2 RED: normalize_constraint_bindings must reuse ScientificConstraint",
            pytrace=False,
        )
    return normalize(value)


def test_binding_normalization_reuses_scientific_constraint() -> None:
    bindings = _normalize(
        [
            {
                "dimension": "acquisition_mode",
                "value": "dda",
                "strength": "soft",
                "evidence_scope": "assay",
                "source": "accepted_preference",
            },
            {
                "dimension": "labeling_strategy",
                "value": "label_free",
                "strength": "hard",
                "evidence_scope": "assay",
                "source": "user",
            },
        ]
    )

    assert all(isinstance(item, ScientificConstraint) for item in bindings)
    assert [(item.dimension, item.strength, item.scope) for item in bindings] == [
        ("acquisition_mode", "soft", "assay"),
        ("labeling_strategy", "hard", "assay"),
    ]


def test_open_and_spectrum_bindings_are_first_class_scientific_constraints() -> None:
    binding = ScientificConstraint(
        id="open.spectrum_charge",
        label="Spectrum charge remains open",
        dimension="spectrum_charge",
        value=None,
        strength="open",
        scope="spectrum",
        evidence_required=False,
        source="user",
    )

    assert binding.strength == "open"
    assert binding.scope == "spectrum"
