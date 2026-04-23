from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent.models import AttributeSet


_TOLERANCE_RE = re.compile(r"^\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>ppm|da)?\s*$", re.IGNORECASE)


def _parse_tolerance(value: Any) -> tuple[str, str] | None:
    if value in (None, ""):
        return None
    match = _TOLERANCE_RE.match(str(value))
    if not match:
        return None
    amount = match.group("value")
    unit = (match.group("unit") or "ppm").lower()
    return amount, "1" if unit == "ppm" else "0"


def _upsert_workflow_value(lines: list[str], key: str, value: Any) -> None:
    prefix = f"{key}="
    rendered = f"{key}={value}"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = rendered
            return
    lines.append(rendered)


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
    if missed not in (None, ""):
        overrides["msfragger.allowed_missed_cleavage_1"] = int(missed)
    return overrides


def _enzyme_overrides(attributes: AttributeSet) -> dict[str, Any]:
    enzyme = str(attributes.enzyme.value or "").lower()
    if "lys" in enzyme and "c" in enzyme:
        return {
            "msfragger.search_enzyme_name_1": "lysc",
            "msfragger.search_enzyme_cut_1": "K",
            "msfragger.search_enzyme_nocut_1": "",
            "msfragger.search_enzyme_sense_1": "C",
            "msfragger.misc.fragger.enzyme-dropdown-1": "lysc",
        }
    if "trypsin" in enzyme:
        return {
            "msfragger.search_enzyme_name_1": "stricttrypsin",
            "msfragger.search_enzyme_cut_1": "KR",
            "msfragger.search_enzyme_nocut_1": "",
            "msfragger.search_enzyme_sense_1": "C",
            "msfragger.misc.fragger.enzyme-dropdown-1": "stricttrypsin",
        }
    return {}


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


def materialize_workflow_with_attributes(
    source: Path,
    destination: Path,
    attributes: AttributeSet,
) -> Path:
    lines = source.read_text(encoding="utf-8").splitlines()
    for key, value in _enzyme_overrides(attributes).items():
        _upsert_workflow_value(lines, key, value)
    for key, value in _modification_overrides(attributes).items():
        _upsert_workflow_value(lines, key, value)
    for key, value in _search_hint_overrides(attributes).items():
        _upsert_workflow_value(lines, key, value)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination
