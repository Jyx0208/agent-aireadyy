from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent.models import AttributeSet


@dataclass(frozen=True)
class EnzymeSpec:
    key: str
    msfragger_name: str
    cut: str
    nocut: str
    sense: str
    dropdown: str
    aliases: tuple[str, ...]


_SPECS: dict[str, EnzymeSpec] = {
    "trypsin": EnzymeSpec(
        key="trypsin",
        msfragger_name="stricttrypsin",
        cut="KR",
        nocut="",
        sense="C",
        dropdown="stricttrypsin",
        aliases=("trypsin", "stricttrypsin", "strict trypsin"),
    ),
    "lysc": EnzymeSpec(
        key="lysc",
        msfragger_name="lysc",
        cut="K",
        nocut="",
        sense="C",
        dropdown="lysc",
        aliases=("lysc", "lys-c", "lys c"),
    ),
    "argc": EnzymeSpec(
        key="argc",
        msfragger_name="Arg-C",
        cut="R",
        nocut="P",
        sense="C",
        dropdown="argc",
        aliases=("argc", "arg-c", "arg c"),
    ),
    "aspn": EnzymeSpec(
        key="aspn",
        msfragger_name="aspn",
        cut="D",
        nocut="",
        sense="N",
        dropdown="aspn",
        aliases=("aspn", "asp-n", "asp n"),
    ),
}


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _spec_mentions(enzyme_value: Any) -> list[tuple[int, str]]:
    normalized = _normalize(enzyme_value)
    if not normalized:
        return []

    mentions: list[tuple[int, str]] = []
    for key, spec in _SPECS.items():
        positions = [
            normalized.find(_normalize(alias))
            for alias in spec.aliases
            if _normalize(alias) and _normalize(alias) in normalized
        ]
        if positions:
            mentions.append((min(positions), key))
    return sorted(mentions)


def canonical_enzyme_keys(enzyme_value: Any) -> list[str]:
    keys: list[str] = []
    for _, key in _spec_mentions(enzyme_value):
        if key not in keys:
            keys.append(key)

    if len(keys) > 1 and "trypsin" in keys:
        return ["trypsin", *(key for key in keys if key != "trypsin")]
    return keys


def enzyme_spec(key: str) -> EnzymeSpec | None:
    return _SPECS.get(key)


def _fragpipe_slot_overrides(spec: EnzymeSpec, slot: int) -> dict[str, Any]:
    return {
        f"msfragger.misc.fragger.enzyme-dropdown-{slot}": spec.dropdown,
        f"msfragger.search_enzyme_name_{slot}": spec.msfragger_name,
        f"msfragger.search_enzyme_cut_{slot}": spec.cut,
        f"msfragger.search_enzyme_nocut_{slot}": spec.nocut,
        f"msfragger.search_enzyme_sense_{slot}": spec.sense,
    }


def fragpipe_enzyme_overrides(enzyme_value: Any) -> dict[str, Any]:
    keys = canonical_enzyme_keys(enzyme_value)
    if not keys or len(keys) > 2:
        return {}

    first = enzyme_spec(keys[0])
    if first is None:
        return {}

    overrides = _fragpipe_slot_overrides(first, 1)
    if len(keys) == 1:
        return overrides

    second = enzyme_spec(keys[1])
    if second is None:
        return {}
    overrides.update(_fragpipe_slot_overrides(second, 2))
    overrides["msfragger.num_enzyme_termini"] = 2
    return overrides


def complete_enzyme_workflow_overrides(attributes: AttributeSet) -> AttributeSet:
    defaults = fragpipe_enzyme_overrides(attributes.enzyme.value)
    if not defaults:
        return attributes

    hints_attr = attributes.search_parameter_hints
    hints = dict(hints_attr.value) if isinstance(hints_attr.value, Mapping) else {}
    existing = hints.get("workflow_parameter_overrides")
    if isinstance(existing, Mapping):
        merged_overrides = {**defaults, **dict(existing)}
    elif existing in (None, "", {}):
        merged_overrides = defaults
    else:
        return attributes

    if existing == merged_overrides:
        return attributes

    hints["workflow_parameter_overrides"] = merged_overrides
    return attributes.model_copy(
        update={"search_parameter_hints": hints_attr.model_copy(update={"value": hints})}
    )
