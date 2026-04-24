from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from agent.models import AttributeSet, AttributeValue, ProjectContext


_DIA_RE = re.compile(r"\bDIA\b|SWATH|data[- ]independent|sequential window acquisition", re.IGNORECASE)
_DDA_RE = re.compile(r"\bDDA\b|data[- ]dependent|tandem mass tag|itraq|silac", re.IGNORECASE)


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten(v) for v in value.values())
    if isinstance(value, Iterable):
        return " ".join(_flatten(v) for v in value)
    return str(value)


def _metadata_text(context: ProjectContext) -> str:
    chunks = []
    for key in (
        "title",
        "projectDescription",
        "sampleProcessingProtocol",
        "dataProcessingProtocol",
        "organisms",
        "instruments",
        "experimentTypes",
        "keywords",
    ):
        metadata = context.metadata.get(key)
        if metadata:
            chunks.append(_flatten(metadata.value))
    return "\n".join(chunks)


def _first_sdrf_value(rows: list[dict[str, Any]], patterns: tuple[str, ...]) -> str | None:
    for row in rows:
        for key, value in row.items():
            normalized = key.lower()
            if any(pattern in normalized for pattern in patterns) and value:
                return str(value)
    return None


def _attribute(value: Any, confidence: float, source: str, evidence_excerpt: str, conflict: bool = False) -> AttributeValue:
    return AttributeValue(
        value=value,
        confidence=confidence,
        source=source,
        evidence_excerpt=evidence_excerpt,
        conflict_flag=conflict,
    )


def _infer_acquisition_mode(context: ProjectContext) -> AttributeValue:
    sdrf_value = _first_sdrf_value(context.sdrf_rows, ("acquisition method", "data acquisition"))
    if sdrf_value:
        normalized = sdrf_value.upper()
        if "DIA" in normalized:
            return _attribute("DIA", 1.0, "sdrf", sdrf_value)
        if "DDA" in normalized:
            return _attribute("DDA", 1.0, "sdrf", sdrf_value)

    text = _metadata_text(context)
    dia = bool(_DIA_RE.search(text))
    dda = bool(_DDA_RE.search(text))
    if dia and not dda:
        return _attribute("DIA", 0.7, "rule", text[:200])
    if dda and not dia:
        return _attribute("DDA", 0.7, "rule", text[:200])
    if dia and dda:
        return _attribute("ambiguous", 0.4, "rule", text[:200], conflict=True)
    instrument_text = _flatten(context.metadata.get("instruments").value) if context.metadata.get("instruments") else ""
    protocol_text = _flatten(context.metadata.get("sampleProcessingProtocol").value) if context.metadata.get("sampleProcessingProtocol") else ""
    if re.search(r"orbitrap|q exactive|exploris|ltq", instrument_text, re.IGNORECASE) and re.search(
        r"mass spectrometry|lc-ms/ms|trypsin|immunoprecipitation|proteom", text + "\n" + protocol_text, re.IGNORECASE
    ):
        return _attribute("DDA", 0.45, "rule_fallback", (text + "\n" + protocol_text)[:200])
    return _attribute("unknown", 0.0, "none", "")


def _infer_species(context: ProjectContext) -> AttributeValue:
    sdrf_value = _first_sdrf_value(context.sdrf_rows, ("organism",))
    if sdrf_value:
        return _attribute(sdrf_value, 1.0, "sdrf", sdrf_value)

    organisms = context.metadata.get("organisms")
    if organisms and organisms.value:
        if isinstance(organisms.value, list) and organisms.value:
            unique_values = [str(value) for value in dict.fromkeys(organisms.value) if str(value).strip()]
            if len(unique_values) > 1:
                return _attribute(
                    "; ".join(unique_values),
                    0.5,
                    "pride.organisms",
                    _flatten(unique_values),
                    conflict=True,
                )
            return _attribute(organisms.value[0], 0.9, "pride.organisms", str(organisms.value[0]))
        return _attribute(organisms.value, 0.9, "pride.organisms", _flatten(organisms.value))
    return _attribute("unknown", 0.0, "none", "")


def _infer_instrument_name(context: ProjectContext) -> AttributeValue:
    sdrf_value = _first_sdrf_value(context.sdrf_rows, ("instrument",))
    if sdrf_value:
        return _attribute(sdrf_value, 1.0, "sdrf", sdrf_value)

    instruments = context.metadata.get("instruments")
    if instruments and instruments.value:
        if isinstance(instruments.value, list) and instruments.value:
            unique_values = [str(value) for value in dict.fromkeys(instruments.value) if str(value).strip()]
            if len(unique_values) > 1:
                return _attribute(
                    "; ".join(unique_values),
                    0.5,
                    "pride.instruments",
                    _flatten(unique_values),
                    conflict=True,
                )
            return _attribute(instruments.value[0], 0.9, "pride.instruments", str(instruments.value[0]))
        return _attribute(instruments.value, 0.9, "pride.instruments", _flatten(instruments.value))
    return _attribute("unknown", 0.0, "none", "")


def _infer_instrument_family(name: str) -> AttributeValue:
    lowered = name.lower()
    if "orbitrap" in lowered or "exploris" in lowered or "q exactive" in lowered:
        return _attribute("orbitrap", 0.9, "rule", name)
    if "tims" in lowered or "timsTOF".lower() in lowered:
        return _attribute("tims", 0.9, "rule", name)
    if "tof" in lowered:
        return _attribute("tof", 0.7, "rule", name)
    return _attribute("unknown", 0.0, "none", "")


def _infer_enzyme(context: ProjectContext) -> AttributeValue:
    sdrf_value = _first_sdrf_value(context.sdrf_rows, ("cleavage agent", "enzyme", "protease"))
    if sdrf_value:
        return _attribute(sdrf_value, 1.0, "sdrf", sdrf_value)

    text = _metadata_text(context)
    if re.search(r"\btrypsin\b", text, re.IGNORECASE):
        return _attribute("Trypsin", 0.8, "rule", "Trypsin keyword in metadata")
    if re.search(r"\blys[- ]?c\b", text, re.IGNORECASE):
        return _attribute("Lys-C", 0.8, "rule", "Lys-C keyword in metadata")
    file_name = context.file_name
    if re.search(r"(?:^|[-_ ])lys[-_ ]?c(?:$|[-_ .])", file_name, re.IGNORECASE):
        return _attribute("Lys-C", 0.7, "file_name_rule", file_name)
    if re.search(r"(?:^|[-_ ])trypsin(?:$|[-_ .])|(?:^|[-_ ])tryp(?:$|[-_ .])", file_name, re.IGNORECASE):
        return _attribute("Trypsin", 0.7, "file_name_rule", file_name)
    return _attribute("unknown", 0.0, "none", "")


def _infer_labeling(context: ProjectContext) -> AttributeValue:
    text = _metadata_text(context)
    if re.search(r"\btmt\b|tandem mass tag", text, re.IGNORECASE):
        return _attribute("TMT", 0.8, "rule", "TMT keyword in metadata")
    if re.search(r"\bitraq\b", text, re.IGNORECASE):
        return _attribute("iTRAQ", 0.8, "rule", "iTRAQ keyword in metadata")
    if re.search(r"\bsilac\b", text, re.IGNORECASE):
        return _attribute("SILAC", 0.8, "rule", "SILAC keyword in metadata")
    return _attribute("label-free", 0.7, "default", "no labeling keywords found")


def _infer_fractionation(context: ProjectContext) -> AttributeValue:
    text = _metadata_text(context)
    if re.search(r"fraction|fractionation|high pH|basic pH", text, re.IGNORECASE):
        return _attribute("fractionated", 0.6, "rule", "Fractionation keyword in metadata")
    return _attribute(None, 0.0, "none", "")


def _infer_search_hints(family: str) -> AttributeValue:
    if family == "orbitrap":
        return _attribute({"precursor_tol": "20ppm", "fragment_tol": "20ppm"}, 0.6, "rule", "Orbitrap default profile")
    if family == "tims":
        return _attribute({"precursor_tol": "30ppm", "fragment_tol": "40ppm", "ion_mobility": True}, 0.6, "rule", "TIMS default profile")
    return _attribute({}, 0.0, "none", "")


def _infer_fixed_mods(enzyme: str) -> AttributeValue:
    if enzyme.lower() == "trypsin":
        return _attribute(["C[57.02]"], 0.7, "default", "Trypsin default carbamidomethylation")
    return _attribute([], 0.3, "default", "No default fixed modifications")


def _infer_variable_mods(_: str) -> AttributeValue:
    return _attribute(["M[15.99]"], 0.7, "default", "Default methionine oxidation")


def infer_attributes(context: ProjectContext) -> AttributeSet:
    acquisition_mode = _infer_acquisition_mode(context)
    species = _infer_species(context)
    instrument_name = _infer_instrument_name(context)
    instrument_family = _infer_instrument_family(str(instrument_name.value))
    if instrument_name.conflict_flag:
        instrument_family = _attribute(
            "unknown",
            0.4,
            instrument_name.source,
            instrument_name.evidence_excerpt,
            conflict=True,
        )
    enzyme = _infer_enzyme(context)
    labeling_strategy = _infer_labeling(context)
    fractionation_hint = _infer_fractionation(context)
    search_parameter_hints = _infer_search_hints(str(instrument_family.value))
    fixed_mods = _infer_fixed_mods(str(enzyme.value))
    variable_mods = _infer_variable_mods(str(enzyme.value))

    return AttributeSet(
        acquisition_mode=acquisition_mode,
        species=species,
        instrument_name=instrument_name,
        instrument_family=instrument_family,
        enzyme=enzyme,
        labeling_strategy=labeling_strategy,
        fixed_mods=fixed_mods,
        variable_mods=variable_mods,
        fractionation_hint=fractionation_hint,
        search_parameter_hints=search_parameter_hints,
    )
