from __future__ import annotations

import re
from typing import Any

from agent.models import AttributeSet, DdaExecutionPlan


_TOLERANCE_RE = re.compile(r"^\s*(?P<value>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>ppm|da)?\s*$", re.IGNORECASE)
_MASS_RE = re.compile(r"(?P<mass>[+-]?\d+(?:\.\d+)?)")


def _as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return [str(value)]


def _contains(value: str, *patterns: str) -> bool:
    normalized = value.lower()
    return all(pattern.lower() in normalized for pattern in patterns)


def _parse_mass(value: str, default: float) -> float:
    match = _MASS_RE.search(value)
    if not match:
        return default
    return float(match.group("mass"))


def _parse_tolerance(value: Any, default_value: float, default_unit: str = "ppm") -> dict[str, list[float]]:
    if value in (None, ""):
        return {default_unit: [-default_value, default_value]}
    match = _TOLERANCE_RE.match(str(value))
    if not match:
        return {default_unit: [-default_value, default_value]}
    amount = abs(float(match.group("value")))
    unit = (match.group("unit") or default_unit).lower()
    return {unit: [-amount, amount]}


def _as_int_list(value: Any, default: list[int]) -> list[int]:
    if value in (None, ""):
        return default
    if isinstance(value, list | tuple):
        try:
            return [int(item) for item in value]
        except (TypeError, ValueError):
            return default
    if isinstance(value, str):
        numbers = re.findall(r"-?\d+", value)
        if numbers:
            return [int(item) for item in numbers]
    try:
        return [int(value)]
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        numbers = re.findall(r"-?\d+", str(value))
        return int(numbers[0]) if numbers else default


def _hint_value(attributes: AttributeSet, *keys: str) -> Any:
    hints = attributes.search_parameter_hints.value
    if not isinstance(hints, dict):
        return None
    for key in keys:
        value = hints.get(key)
        if value not in (None, ""):
            return value
    return None


def _enzyme_config(attributes: AttributeSet) -> dict[str, Any]:
    enzyme = str(attributes.enzyme.value or "").lower()
    missed = _hint_value(attributes, "missed_cleavages", "allowed_missed_cleavages")
    missed_cleavages = int(missed) if missed not in (None, "") else 2
    if "lys" in enzyme and "c" in enzyme:
        return {
            "missed_cleavages": missed_cleavages,
            "min_len": 5,
            "max_len": 50,
            "cleave_at": "K",
            "restrict": "P",
            "c_terminal": True,
        }
    return {
        "missed_cleavages": missed_cleavages,
        "min_len": 5,
        "max_len": 50,
        "cleave_at": "KR",
        "restrict": "P",
        "c_terminal": True,
    }


def _static_mods(attributes: AttributeSet) -> dict[str, float]:
    static_mods: dict[str, float] = {}
    for mod in _as_list(attributes.fixed_mods.value):
        if _contains(mod, "carbamidomethyl", "c") or (_contains(mod, "c") and "57" in mod):
            static_mods["C"] = _parse_mass(mod, 57.021464)
        if _contains(mod, "tmt") or _contains(mod, "tandem mass tag"):
            mass = _parse_mass(mod, 229.162932)
            static_mods["^"] = mass
            static_mods["K"] = mass
    return static_mods


def _variable_mods(attributes: AttributeSet) -> dict[str, list[float]]:
    variable_mods: dict[str, list[float]] = {}
    for mod in _as_list(attributes.variable_mods.value):
        if _contains(mod, "oxidation", "m") or (_contains(mod, "m") and "15.99" in mod):
            variable_mods.setdefault("M", []).append(_parse_mass(mod, 15.994915))
        elif _contains(mod, "deamid"):
            variable_mods.setdefault("N", []).append(_parse_mass(mod, 0.984016))
            variable_mods.setdefault("Q", []).append(_parse_mass(mod, 0.984016))
        elif _contains(mod, "acetyl"):
            variable_mods.setdefault("[", []).append(_parse_mass(mod, 42.010565))
        elif _contains(mod, "pyro"):
            variable_mods.setdefault("^Q", []).append(_parse_mass(mod, -17.026549))
    return variable_mods


def _quant_config(attributes: AttributeSet) -> dict[str, Any]:
    label = str(attributes.labeling_strategy.value or "").lower()
    if "tmt" in label:
        channel_count = _hint_value(attributes, "tmt_channel_count", "tmt_channels", "plex")
        channels = _as_int(channel_count, 16)
        supported = {6, 10, 11, 16, 18}
        if channels not in supported:
            channels = 16
        return {"tmt": f"Tmt{channels}"}
    if "label-free" in label or "lfq" in label:
        return {"lfq": True}
    return {}


def build_sage_config(plan: DdaExecutionPlan, attributes: AttributeSet) -> dict[str, Any]:
    precursor = _parse_tolerance(
        _hint_value(attributes, "precursor_tol", "precursor_tolerance", "precursor_mass_tolerance"),
        default_value=20.0,
    )
    fragment = _parse_tolerance(
        _hint_value(attributes, "fragment_tol", "fragment_tolerance", "fragment_mass_tolerance"),
        default_value=20.0,
    )
    config: dict[str, Any] = {
        "database": {
            "fasta": str(plan.fasta_path),
            "enzyme": _enzyme_config(attributes),
            "static_mods": _static_mods(attributes),
            "variable_mods": _variable_mods(attributes),
            "generate_decoys": True,
            "decoy_tag": "rev_",
        },
        "precursor_tol": precursor,
        "fragment_tol": fragment,
        "precursor_charge": _as_int_list(_hint_value(attributes, "precursor_charge", "charge_range"), [2, 4]),
        "isotope_errors": _as_int_list(_hint_value(attributes, "isotope_errors"), [0, 1]),
        "output_directory": str(plan.fragpipe_workdir.parent / "sage"),
        "mzml_paths": [str(plan.source_data_path)],
    }
    optional_integer_keys = {
        "min_peaks": "min_peaks",
        "max_peaks": "max_peaks",
        "min_matched_peaks": "min_matched_peaks",
        "max_variable_mods": "database.max_variable_mods",
    }
    for hint_key, config_key in optional_integer_keys.items():
        value = _hint_value(attributes, hint_key)
        if value in (None, ""):
            continue
        parsed = _as_int(value, 0)
        if config_key.startswith("database."):
            config["database"][config_key.split(".", 1)[1]] = parsed
        else:
            config[config_key] = parsed
    quant = _quant_config(attributes)
    if quant:
        config["quant"] = quant
    return config
