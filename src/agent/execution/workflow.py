from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.inference.enzyme_semantics import fragpipe_enzyme_overrides
from agent.models import AttributeSet


_TOLERANCE_RE = re.compile(r"^\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>ppm|da)?\s*$", re.IGNORECASE)
_WORKFLOW_OVERRIDE_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_WORKFLOW_OVERRIDE_HINT_KEYS = (
    "workflow_parameter_overrides",
    "fragpipe_workflow_overrides",
    "msfragger_parameter_overrides",
)
_ALLOWED_WORKFLOW_OVERRIDE_PREFIXES = ("msfragger.",)
_ALLOWED_WORKFLOW_OVERRIDE_KEYS = frozenset(
    {
        "msfragger.allowed_missed_cleavage_1",
        "msfragger.allowed_missed_cleavage_2",
        "msfragger.calibrate_mass",
        "msfragger.digest_max_length",
        "msfragger.digest_min_length",
        "msfragger.fragment_ion_series",
        "msfragger.fragment_mass_tolerance",
        "msfragger.fragment_mass_units",
        "msfragger.mass_offsets",
        "msfragger.mass_offsets_detailed",
        "msfragger.max_fragment_charge",
        "msfragger.max_variable_mods_combinations",
        "msfragger.max_variable_mods_per_peptide",
        "msfragger.min_fragments_modelling",
        "msfragger.min_matched_fragments",
        "msfragger.min_sequence_matches",
        "msfragger.misc.fragger.digest-mass-hi",
        "msfragger.misc.fragger.digest-mass-lo",
        "msfragger.misc.fragger.enzyme-dropdown-1",
        "msfragger.misc.fragger.enzyme-dropdown-2",
        "msfragger.num_enzyme_termini",
        "msfragger.precursor_mass_lower",
        "msfragger.precursor_mass_mode",
        "msfragger.precursor_mass_units",
        "msfragger.precursor_mass_upper",
        "msfragger.precursor_true_tolerance",
        "msfragger.precursor_true_units",
        "msfragger.search_enzyme_cut_1",
        "msfragger.search_enzyme_cut_2",
        "msfragger.search_enzyme_name_1",
        "msfragger.search_enzyme_name_2",
        "msfragger.search_enzyme_nocut_1",
        "msfragger.search_enzyme_nocut_2",
        "msfragger.search_enzyme_sense_1",
        "msfragger.search_enzyme_sense_2",
    }
)
_NUMERIC_WORKFLOW_OVERRIDE_KEYS = frozenset(
    {
        "msfragger.allowed_missed_cleavage_1",
        "msfragger.allowed_missed_cleavage_2",
        "msfragger.digest_max_length",
        "msfragger.digest_min_length",
        "msfragger.fragment_mass_tolerance",
        "msfragger.max_fragment_charge",
        "msfragger.max_variable_mods_combinations",
        "msfragger.max_variable_mods_per_peptide",
        "msfragger.min_fragments_modelling",
        "msfragger.min_matched_fragments",
        "msfragger.min_sequence_matches",
        "msfragger.misc.fragger.digest-mass-hi",
        "msfragger.misc.fragger.digest-mass-lo",
        "msfragger.num_enzyme_termini",
        "msfragger.precursor_mass_lower",
        "msfragger.precursor_mass_upper",
        "msfragger.precursor_true_tolerance",
    }
)
_UNIT_WORKFLOW_OVERRIDE_KEYS = frozenset(
    {
        "msfragger.fragment_mass_units",
        "msfragger.precursor_mass_units",
        "msfragger.precursor_true_units",
    }
)
_ENZYME_DROPDOWN_ALIASES = {
    "argc": "argc",
    "arg-c": "argc",
    "arg c": "argc",
    "aspn": "aspn",
    "asp-n": "aspn",
    "asp n": "aspn",
    "lysc": "lysc",
    "lys-c": "lysc",
    "lys c": "lysc",
    "stricttrypsin": "stricttrypsin",
    "strict trypsin": "stricttrypsin",
    "trypsin": "stricttrypsin",
}


def _parse_tolerance(value: Any) -> tuple[str, str] | None:
    if value in (None, ""):
        return None
    match = _TOLERANCE_RE.match(str(value))
    if not match:
        return None
    amount = match.group("value")
    unit = (match.group("unit") or "ppm").lower()
    return amount, "1" if unit == "ppm" else "0"


def _parse_nonnegative_int_hint(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None

    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None:
        return int(numeric) if numeric >= 0 and numeric.is_integer() else None

    matches = [int(match.group(0)) for match in re.finditer(r"\d+", text)]
    if not matches:
        return None
    return max(matches)


def _upsert_workflow_value(lines: list[str], key: str, value: Any) -> None:
    prefix = f"{key}="
    rendered = f"{key}={value}"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = rendered
            return
    lines.append(rendered)


def _configured_fragpipe_ram_gb() -> int | None:
    raw = os.getenv("AGENT_FRAGPIPE_RAM_GB", "").strip()
    if not raw:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return None


def _search_hint_overrides(attributes: AttributeSet) -> dict[str, Any]:
    hints = attributes.search_parameter_hints.value
    if not isinstance(hints, dict):
        return {}

    overrides: dict[str, Any] = {}
    precursor = _parse_tolerance(
        hints.get("precursor_tol")
        or hints.get("precursor_tolerance")
        or hints.get("precursor_mass_tolerance")
    )
    if precursor is not None:
        amount, unit = precursor
        overrides["msfragger.precursor_mass_lower"] = f"-{amount.lstrip('+-')}"
        overrides["msfragger.precursor_mass_upper"] = amount.lstrip("+-")
        overrides["msfragger.precursor_mass_units"] = unit
        overrides["msfragger.precursor_true_tolerance"] = amount.lstrip("+-")
        overrides["msfragger.precursor_true_units"] = unit

    fragment = _parse_tolerance(
        hints.get("fragment_tol")
        or hints.get("fragment_tolerance")
        or hints.get("fragment_mass_tolerance")
    )
    if fragment is not None:
        amount, unit = fragment
        overrides["msfragger.fragment_mass_tolerance"] = amount.lstrip("+-")
        overrides["msfragger.fragment_mass_units"] = unit

    missed = hints.get("missed_cleavages")
    missed_value = _parse_nonnegative_int_hint(missed)
    if missed_value is not None:
        overrides["msfragger.allowed_missed_cleavage_1"] = missed_value
    return overrides


def _render_workflow_override_value(value: Any) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        rendered = value.strip()
        if "\n" in rendered or "\r" in rendered:
            return None
        return rendered
    return None


def _sanitize_workflow_override_value(key: str, rendered: str) -> str | None:
    if key in _NUMERIC_WORKFLOW_OVERRIDE_KEYS:
        parsed = _parse_tolerance(rendered)
        if parsed is None:
            return rendered
        amount, _unit = parsed
        if amount.startswith("+"):
            amount = amount[1:]
        return amount

    if key in _UNIT_WORKFLOW_OVERRIDE_KEYS:
        normalized = rendered.strip().lower()
        if normalized in {"0", "da", "dalton", "daltons"}:
            return "0"
        if normalized in {"1", "ppm"}:
            return "1"
    return rendered


def _iter_workflow_override_items(raw: Any):
    if isinstance(raw, Mapping):
        yield from raw.items()
        return
    if isinstance(raw, list | tuple):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            key = item.get("key") or item.get("name") or item.get("parameter")
            if "value" not in item:
                continue
            yield key, item.get("value")


def _workflow_parameter_overrides(attributes: AttributeSet) -> dict[str, str]:
    hints = attributes.search_parameter_hints.value
    if not isinstance(hints, dict):
        return {}

    overrides: dict[str, str] = {}
    for hint_key in _WORKFLOW_OVERRIDE_HINT_KEYS:
        raw = hints.get(hint_key)
        if raw in (None, ""):
            continue
        for key, value in _iter_workflow_override_items(raw):
            key_text = str(key or "").strip()
            if not key_text:
                continue
            if not _WORKFLOW_OVERRIDE_KEY_RE.match(key_text):
                continue
            if not key_text.startswith(_ALLOWED_WORKFLOW_OVERRIDE_PREFIXES):
                continue
            if key_text not in _ALLOWED_WORKFLOW_OVERRIDE_KEYS:
                continue
            rendered = _render_workflow_override_value(value)
            if rendered is None:
                continue
            rendered = _sanitize_workflow_override_value(key_text, rendered)
            if rendered is None:
                continue
            overrides[key_text] = rendered
    _sync_enzyme_dropdown_overrides(overrides)
    return overrides


def _enzyme_dropdown_value(enzyme_name: Any) -> str | None:
    rendered = _render_workflow_override_value(enzyme_name)
    if not rendered or rendered == "null":
        return None
    normalized = re.sub(r"[^a-z0-9]+", "", rendered.lower())
    for alias, value in _ENZYME_DROPDOWN_ALIASES.items():
        if re.sub(r"[^a-z0-9]+", "", alias.lower()) == normalized:
            return value
    return None


def _sync_enzyme_dropdown_overrides(overrides: dict[str, str]) -> None:
    for slot in ("1", "2"):
        name_key = f"msfragger.search_enzyme_name_{slot}"
        dropdown_key = f"msfragger.misc.fragger.enzyme-dropdown-{slot}"
        if name_key not in overrides or dropdown_key in overrides:
            continue
        dropdown = _enzyme_dropdown_value(overrides[name_key])
        if dropdown is not None:
            overrides[dropdown_key] = dropdown


def _enzyme_overrides(attributes: AttributeSet) -> dict[str, Any]:
    return fragpipe_enzyme_overrides(attributes.enzyme.value)


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return [str(value)]


def _contains_mod(mods: list[str], *patterns: str) -> bool:
    normalized = " | ".join(mod.lower() for mod in mods)
    return all(pattern.lower() in normalized for pattern in patterns)


def _fixed_mods_table(attributes: AttributeSet) -> str:
    fixed_mods = _as_list(attributes.fixed_mods.value)
    cysteine_mass = "57.02146" if _contains_mod(fixed_mods, "c") and _contains_mod(fixed_mods, "57") else "0.0"
    entries = [
        "0.0,C-Term Peptide,true,-1",
        "0.0,N-Term Peptide,true,-1",
        "0.0,C-Term Protein,true,-1",
        "0.0,N-Term Protein,true,-1",
        "0.0,G (glycine),true,-1",
        "0.0,A (alanine),true,-1",
        "0.0,S (serine),true,-1",
        "0.0,P (proline),true,-1",
        "0.0,V (valine),true,-1",
        "0.0,T (threonine),true,-1",
        f"{cysteine_mass},C (cysteine),true,-1",
        "0.0,L (leucine),true,-1",
        "0.0,I (isoleucine),true,-1",
        "0.0,N (asparagine),true,-1",
        "0.0,D (aspartic acid),true,-1",
        "0.0,Q (glutamine),true,-1",
        "0.0,K (lysine),true,-1",
        "0.0,E (glutamic acid),true,-1",
        "0.0,M (methionine),true,-1",
        "0.0,H (histidine),true,-1",
        "0.0,F (phenylalanine),true,-1",
        "0.0,R (arginine),true,-1",
        "0.0,Y (tyrosine),true,-1",
        "0.0,W (tryptophan),true,-1",
        "0.0,B ,true,-1",
        "0.0,J,true,-1",
        "0.0,O,true,-1",
        "0.0,U,true,-1",
        "0.0,X,true,-1",
        "0.0,Z,true,-1",
    ]
    return "; ".join(entries)


def _variable_mods_table(attributes: AttributeSet) -> str | None:
    variable_mods = _as_list(attributes.variable_mods.value)
    if not variable_mods:
        return None

    entries: list[str] = []
    if _contains_mod(variable_mods, "carbamidomethyl", "c"):
        entries.append("57.02146,C,true,3")
    if _contains_mod(variable_mods, "acetyl") or _contains_mod(variable_mods, "n-acetyl"):
        entries.append("42.0106,[^,true,1")
    if _contains_mod(variable_mods, "formyl"):
        entries.append("27.9949,[^,true,1")
    if _contains_mod(variable_mods, "oxidation", "m"):
        entries.append("15.9949,M,true,3")
    if _contains_mod(variable_mods, "deamid"):
        entries.append("0.984016,NQ,true,3")
    if _contains_mod(variable_mods, "pyro"):
        entries.append("-17.0265,nQnC,true,1")

    while len(entries) < 16:
        index = len(entries) + 1
        entries.append(f"0.0,site_{index},false,1")
    return "; ".join(entries[:16])


def _modification_overrides(attributes: AttributeSet) -> dict[str, Any]:
    overrides = {"msfragger.table.fix-mods": _fixed_mods_table(attributes)}
    variable_mods = _variable_mods_table(attributes)
    if variable_mods is not None:
        overrides["msfragger.table.var-mods"] = variable_mods
    return overrides


def _is_isobaric_label_workflow(source: Path) -> bool:
    workflow_name = source.name.lower()
    return "tmt" in workflow_name or "itraq" in workflow_name


def _quantitation_overrides(source: Path) -> dict[str, Any]:
    if not _is_isobaric_label_workflow(source):
        return {}
    return {
        "tmtintegrator.run-tmtintegrator": "false",
    }


def materialize_workflow_with_attributes(
    source: Path,
    destination: Path,
    attributes: AttributeSet,
) -> Path:
    lines = source.read_text(encoding="utf-8").splitlines()
    for key, value in _enzyme_overrides(attributes).items():
        _upsert_workflow_value(lines, key, value)
    if not _is_isobaric_label_workflow(source):
        for key, value in _modification_overrides(attributes).items():
            _upsert_workflow_value(lines, key, value)
    for key, value in _search_hint_overrides(attributes).items():
        _upsert_workflow_value(lines, key, value)
    for key, value in _workflow_parameter_overrides(attributes).items():
        _upsert_workflow_value(lines, key, value)
    for key, value in _quantitation_overrides(source).items():
        _upsert_workflow_value(lines, key, value)
    ram_gb = _configured_fragpipe_ram_gb()
    if ram_gb is not None:
        _upsert_workflow_value(lines, "workflow.ram", ram_gb)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination
