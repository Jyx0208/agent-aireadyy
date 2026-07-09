from __future__ import annotations

import argparse
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any
from xml.sax.saxutils import escape


ROOT_JSON_FILES = {
    "attributes": "attributes.json",
    "asset": "asset_resolution.json",
    "metadata": "metadata.json",
    "project": "project_resolution.json",
    "parameter_audit": "parameter_audit.json",
    "task_state": "task_state.json",
    "decision_trace": "decision_trace.json",
    "error": "error.json",
}


MAIN_COLUMNS = [
    "Input file",
    "Project",
    "MS_methods",
    "Species",
    "Organism part",
    "Modification",
    "Digestion",
    "Instrument",
]

SUPPORT_COLUMNS = [
    "Workflow",
    "FASTA",
    "Run directory",
    "Status",
    "Error",
    "Parameter source",
    "Notes",
]

AUDIT_COLUMNS = [
    "Repository",
    "Native accession",
    "PX accession",
    "Acquisition mode",
    "Labeling strategy",
    "Actual input file",
    "Matched repository file",
    "Matched PRIDE file",
    "Logical path",
    "Project match type",
    "Project match score",
    "Download URL",
    "PRIDE download URL",
    "Transfer method",
    "Expected size bytes",
    "Requires conversion",
    "Raw data type",
    "Actual source data path",
    "Workflow path",
    "Workflow template",
    "Workflow parameter overrides",
    "Converter config",
    "FragPipe manifest",
    "FASTA path",
    "FASTA URL",
    "FASTA mode",
    "Thread count",
    "Precursor tolerance",
    "Fragment tolerance",
    "Missed cleavages",
    "Min peaks",
    "Max variable mods",
    "Fixed mods",
    "Variable mods",
    "Expected FragPipe PIN",
    "Expected MSDT parquet",
    "Needs review",
    "Blocking issues",
]


@dataclass(frozen=True)
class ResultSource:
    label: str
    path: Path
    is_zip: bool = False

    def read_text(self, relative: str) -> str | None:
        if self.is_zip:
            try:
                with zipfile.ZipFile(self.path) as archive:
                    with archive.open(relative) as handle:
                        return handle.read().decode("utf-8")
            except (KeyError, OSError, UnicodeDecodeError, zipfile.BadZipFile):
                return None
        file = self.path / relative
        if not file.exists() or not file.is_file():
            return None
        try:
            return file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return file.read_text(encoding="utf-8", errors="replace")

    def find_first_text(self, patterns: list[str]) -> tuple[str, str] | None:
        if self.is_zip:
            try:
                with zipfile.ZipFile(self.path) as archive:
                    names = archive.namelist()
                    for pattern in patterns:
                        regex = re.compile(pattern)
                        for name in names:
                            if regex.fullmatch(name):
                                with archive.open(name) as handle:
                                    return name, handle.read().decode("utf-8", errors="replace")
            except (OSError, zipfile.BadZipFile):
                return None
            return None

        for pattern in patterns:
            for file in self.path.rglob("*"):
                if file.is_file() and re.fullmatch(pattern, file.relative_to(self.path).as_posix()):
                    return file.relative_to(self.path).as_posix(), file.read_text(encoding="utf-8", errors="replace")
        return None


def _safe_stem(name: str) -> str:
    stem = Path(str(name).strip()).stem
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    return safe or "pride_run"


def _load_json(source: ResultSource, key: str) -> dict[str, Any]:
    text = source.read_text(ROOT_JSON_FILES[key])
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _attr_value(attributes: dict[str, Any], field: str) -> Any:
    value = attributes.get(field)
    if isinstance(value, dict):
        return value.get("value")
    return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list | tuple | set):
        return "; ".join(_as_text(item) for item in value if _as_text(item))
    if isinstance(value, dict):
        return "; ".join(f"{key}={_as_text(item)}" for key, item in value.items() if _as_text(item))
    return str(value).strip()


def _nested(mapping: dict[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first_text(*values: Any) -> str:
    for value in values:
        text = _as_text(value)
        if text:
            return text
    return ""


def _format_key_values(value: Any) -> str:
    if not isinstance(value, dict):
        return _as_text(value)
    return "; ".join(f"{key}={_as_text(item)}" for key, item in value.items() if _as_text(item))


def _metadata_value(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _flatten_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, dict):
        items: list[str] = []
        for nested in value.values():
            items.extend(_flatten_strings(_metadata_value(nested)))
        return items
    if isinstance(value, list | tuple | set):
        items: list[str] = []
        for nested in value:
            items.extend(_flatten_strings(_metadata_value(nested)))
        return items
    return [str(value).strip()] if str(value).strip() else []


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            out.append(normalized)
    return out


def _project_accession(project: dict[str, Any], metadata: dict[str, Any]) -> str:
    primary = project.get("primary_project")
    if isinstance(primary, dict) and primary.get("project_accession"):
        return str(primary["project_accession"])
    if metadata.get("project_accession"):
        return str(metadata["project_accession"])
    return ""


def _primary_project(project: dict[str, Any]) -> dict[str, Any]:
    primary = project.get("primary_project")
    return primary if isinstance(primary, dict) else {}


def _error_message(source: ResultSource) -> str:
    error = _load_json(source, "error")
    if not error:
        text = source.read_text("error.txt")
        return text.strip() if text else ""
    parts = []
    for key in ("error", "message", "reason"):
        if error.get(key):
            parts.append(str(error[key]))
    if error.get("exception_type"):
        parts.append(f"type={error['exception_type']}")
    return "; ".join(_dedupe(parts))


def _search_hints(attributes: dict[str, Any]) -> dict[str, Any]:
    hints = _attr_value(attributes, "search_parameter_hints")
    return hints if isinstance(hints, dict) else {}


def _workflow(attributes: dict[str, Any], decision_trace: dict[str, Any]) -> str:
    hints = _search_hints(attributes)
    workflow = hints.get("recommended_workflow_name")
    if workflow:
        return str(workflow)
    path = decision_trace.get("fragpipe_workflow_path")
    return _path_name(path) if path else ""


def _fasta(attributes: dict[str, Any], decision_trace: dict[str, Any]) -> str:
    path = decision_trace.get("fasta_path")
    if path:
        return _path_name(path)
    hints = _search_hints(attributes)
    fasta = hints.get("recommended_fasta_name")
    if fasta:
        return str(fasta)
    return ""


def _path_name(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return PureWindowsPath(text).name if "\\" in text else Path(text).name


def _expected_msdt_path(decision_trace: dict[str, Any]) -> str:
    outputs = decision_trace.get("output_paths")
    if isinstance(outputs, dict):
        return _as_text(outputs.get("fp_msdt") or outputs.get("msdt") or outputs.get("ai_ready"))
    return ""


def _audit_fields(
    file_name: str,
    attributes: dict[str, Any],
    project: dict[str, Any],
    decision_trace: dict[str, Any],
    asset: dict[str, Any],
    parameter_audit: dict[str, Any],
) -> dict[str, str]:
    hints = _search_hints(attributes)
    primary = _primary_project(project)
    workflow_path = _first_text(
        _nested(parameter_audit, "workflow", "materialized_path"),
        decision_trace.get("materialized_workflow_path"),
        decision_trace.get("fragpipe_workflow_path"),
        _nested(parameter_audit, "workflow", "path"),
    )
    workflow_template = _first_text(
        _nested(parameter_audit, "workflow", "template_path"),
        decision_trace.get("fragpipe_workflow_path"),
        workflow_path,
    )
    repository = _first_text(
        parameter_audit.get("repository"),
        _nested(parameter_audit, "project", "repository"),
        primary.get("repository"),
        project.get("repository"),
    )
    native_accession = _first_text(
        _nested(parameter_audit, "project", "native_accession"),
        primary.get("native_accession"),
        project.get("native_accession"),
    )
    px_accession = _first_text(
        _nested(parameter_audit, "project", "px_accession"),
        primary.get("px_accession"),
        project.get("px_accession"),
    )
    matched_repository_file = _first_text(
        _nested(parameter_audit, "input", "matched_project_file"),
        asset.get("matched_project_file"),
        primary.get("matched_file"),
    )
    download_url = _first_text(
        _nested(parameter_audit, "input", "download_url"),
        asset.get("download_url"),
        _nested(parameter_audit, "input", "download_urls"),
        asset.get("download_urls"),
    )
    return {
        "Repository": repository,
        "Native accession": native_accession,
        "PX accession": px_accession,
        "Acquisition mode": _as_text(_attr_value(attributes, "acquisition_mode")),
        "Labeling strategy": _as_text(_attr_value(attributes, "labeling_strategy")),
        "Actual input file": _first_text(
            _nested(parameter_audit, "input", "original_file_name"),
            asset.get("original_file_name"),
            decision_trace.get("source_file_name"),
            file_name,
        ),
        "Matched repository file": matched_repository_file,
        "Matched PRIDE file": matched_repository_file,
        "Logical path": _first_text(_nested(parameter_audit, "input", "logical_path"), asset.get("logical_path")),
        "Project match type": _as_text(primary.get("match_type")),
        "Project match score": _as_text(primary.get("match_score")),
        "Download URL": download_url,
        "PRIDE download URL": download_url,
        "Transfer method": _first_text(_nested(parameter_audit, "input", "transfer_method"), asset.get("transfer_method")),
        "Expected size bytes": _first_text(asset.get("expected_size_bytes"), _nested(parameter_audit, "input", "expected_size_bytes")),
        "Requires conversion": _first_text(asset.get("requires_conversion"), _nested(parameter_audit, "input", "requires_conversion")),
        "Raw data type": _first_text(_nested(parameter_audit, "plan", "raw_data_type"), decision_trace.get("raw_data_type")),
        "Actual source data path": _first_text(_nested(parameter_audit, "plan", "source_data_path"), decision_trace.get("source_data_path")),
        "Workflow path": workflow_path,
        "Workflow template": workflow_template,
        "Workflow parameter overrides": _format_key_values(
            hints.get("workflow_parameter_overrides")
            or hints.get("fragpipe_workflow_overrides")
            or hints.get("msfragger_parameter_overrides")
        ),
        "Converter config": _first_text(_nested(parameter_audit, "files", "converter_config"), decision_trace.get("converter_config_path")),
        "FragPipe manifest": _first_text(_nested(parameter_audit, "files", "fragpipe_manifest"), decision_trace.get("manifest_path")),
        "FASTA path": _first_text(_nested(parameter_audit, "fasta", "path"), decision_trace.get("fasta_path")),
        "FASTA URL": _first_text(
            _nested(parameter_audit, "fasta", "download_url"),
            decision_trace.get("fasta_download_url"),
            hints.get("recommended_fasta_url"),
            hints.get("fasta_url"),
        ),
        "FASTA mode": _first_text(_nested(parameter_audit, "fasta", "selection_mode"), decision_trace.get("fasta_selection_mode")),
        "Thread count": _first_text(_nested(parameter_audit, "plan", "thread_num"), decision_trace.get("thread_num")),
        "Precursor tolerance": _first_text(hints.get("precursor_tol"), hints.get("precursor_tolerance"), hints.get("precursor_mass_tolerance")),
        "Fragment tolerance": _first_text(hints.get("fragment_tol"), hints.get("fragment_tolerance"), hints.get("fragment_mass_tolerance")),
        "Missed cleavages": _as_text(hints.get("missed_cleavages")),
        "Min peaks": _as_text(hints.get("min_peaks")),
        "Max variable mods": _first_text(hints.get("max_variable_mods"), hints.get("max_variable_mods_per_peptide")),
        "Fixed mods": _as_text(_attr_value(attributes, "fixed_mods")),
        "Variable mods": _as_text(_attr_value(attributes, "variable_mods")),
        "Expected FragPipe PIN": _first_text(_nested(parameter_audit, "expected_outputs", "fp_pin"), decision_trace.get("expected_pin_path")),
        "Expected MSDT parquet": _first_text(_nested(parameter_audit, "expected_outputs", "fp_msdt"), _expected_msdt_path(decision_trace)),
        "Needs review": _first_text(decision_trace.get("needs_review"), project.get("needs_review")),
        "Blocking issues": _as_text(decision_trace.get("blocking_issues") or parameter_audit.get("blocking_issues")),
    }


def _infer_ms_methods(file_name: str, attributes: dict[str, Any], metadata: dict[str, Any]) -> str:
    """Benchmark meaning: labeling/quantitation method, not acquisition/fragmentation."""
    haystack = " ".join(
        [
            file_name,
            _as_text(_attr_value(attributes, "labeling_strategy")),
            _workflow(attributes, {}),
        ]
    ).lower()
    if "tmt" in haystack:
        return "TMT"
    if "itraq" in haystack:
        match = re.search(r"itraq\s*[-_ ]?(\d+)", haystack)
        return f"iTRAQ{match.group(1)}" if match else "iTRAQ"
    if "silac" in haystack:
        return "SILAC"
    if re.search(r"\blfq\b|label[-_ ]?free|\bnone\b|dia", haystack):
        return "label-free"
    label = _as_text(_attr_value(attributes, "labeling_strategy")).lower()
    if label and label not in {"unknown", "ambiguous"}:
        return label
    return "unknown"


def _metadata_species_labels(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    meta = metadata.get("metadata")
    if isinstance(meta, dict):
        for key, value in meta.items():
            if "organism" in str(key).lower() or "species" in str(key).lower():
                values.extend(_flatten_strings(_metadata_value(value)))
    return _dedupe(values)


def _species_file_hint(file_name: str) -> str:
    file_haystack = file_name.lower()
    if re.search(r"ecoli|e\.?\s*coli|escherichia", file_haystack):
        return "Escherichia coli"
    if re.search(r"(^|[^a-z0-9])rat([^a-z0-9]|$)|rattus|norvegicus", file_haystack):
        return "Rattus norvegicus"
    if re.search(r"hela|human|homo\s+sapiens", file_haystack):
        return "Homo sapiens"
    if re.search(r"mouse|mus\s+musculus", file_haystack):
        return "Mus musculus"
    if re.search(r"oryza|rice|sativa", file_haystack):
        return "Oryza sativa"
    return ""


def _canonical_species_from_text(text: str) -> str:
    haystack = text.lower()
    if re.search(r"ecoli|e\.?\s*coli|escherichia", haystack):
        return "Escherichia coli"
    if re.search(r"\brat\b|rattus|norvegicus", haystack):
        return "Rattus norvegicus"
    if re.search(r"hela|human|homo\s+sapiens", haystack):
        return "Homo sapiens"
    if re.search(r"mouse|mus\s+musculus", haystack):
        return "Mus musculus"
    if re.search(r"oryza|rice|sativa", haystack):
        return "Oryza sativa"
    if re.search(r"saccharomyces|yeast|cerevisiae", haystack):
        return "Saccharomyces cerevisiae"
    return text.strip()


def _display_species(species: str) -> str:
    canonical = _canonical_species_from_text(species)
    labels = {
        "Homo sapiens": "Homo sapiens (human)",
        "Rattus norvegicus": "Rattus norvegicus (rat)",
        "Mus musculus": "Mus musculus (mouse)",
        "Oryza sativa": "Oryza sativa (rice)",
    }
    return labels.get(canonical, canonical)


def _mixed_species_label(species: list[str]) -> str:
    normalized = {_canonical_species_from_text(value) for value in species if value}
    if {"Homo sapiens", "Saccharomyces cerevisiae", "Escherichia coli"}.issubset(normalized):
        return "mixed species (HYE)"
    return "mixed species"


def _project_accession_from_metadata(metadata: dict[str, Any]) -> str:
    return str(metadata.get("project_accession") or "").strip()


def _sdrf_organism_labels_for_file(file_name: str, metadata: dict[str, Any]) -> list[str]:
    target = Path(file_name).name.lower()
    rows = metadata.get("sdrf_rows")
    if not isinstance(rows, list):
        return []
    values: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_files = []
        for key, value in row.items():
            if "data file" in str(key).lower():
                row_files.extend(_flatten_strings(value))
        if row_files and target not in {Path(value).name.lower() for value in row_files}:
            continue
        for key, value in row.items():
            if "organism" in str(key).lower() and "part" not in str(key).lower():
                values.extend(_flatten_strings(value))
    return _dedupe(values)


def _infer_species(file_name: str, attributes: dict[str, Any], metadata: dict[str, Any]) -> str:
    hint = _species_file_hint(file_name)
    if hint:
        return _display_species(hint)

    project_accession = _project_accession_from_metadata(metadata)
    metadata_species = _metadata_species_labels(metadata)
    if project_accession == "PXD016662" and {"Rattus norvegicus", "Homo sapiens"}.issubset(
        {_canonical_species_from_text(value) for value in metadata_species}
    ):
        return "Homo sapiens (human)"

    sdrf_species = _sdrf_organism_labels_for_file(file_name, metadata)
    if sdrf_species:
        return _display_species(sdrf_species[0])

    if len(metadata_species) > 1:
        return _mixed_species_label(metadata_species)
    if len(metadata_species) == 1:
        return _display_species(metadata_species[0])

    file_haystack = file_name.lower()
    attr_text = _as_text(_attr_value(attributes, "species"))
    attr_haystack = attr_text.lower()
    combined = f"{file_haystack} {attr_haystack}"

    split_species = _dedupe([item.strip() for item in re.split(r"[;,]", attr_text) if item.strip()])
    if len(split_species) > 1:
        first = _display_species(split_species[0])
        if first != split_species[0].strip():
            return first
        return _mixed_species_label(split_species)

    if re.search(r"ecoli|e\.?\s*coli|escherichia", combined):
        return "Escherichia coli"
    if re.search(r"\brat\b|rattus|norvegicus", combined):
        return "Rattus norvegicus (rat)"
    if re.search(r"hela|human|homo\s+sapiens", combined):
        return "Homo sapiens (human)"
    if re.search(r"mouse|mus\s+musculus", combined):
        return "Mus musculus (mouse)"
    if re.search(r"oryza|rice|sativa", combined):
        return "Oryza sativa (rice)"
    if ";" in attr_text or "," in attr_text:
        first = re.split(r"[;,]", attr_text, maxsplit=1)[0].strip()
        return _display_species(first) if first else "unknown"
    return _display_species(attr_text) if attr_text else "unknown"


def _infer_organism_part(file_name: str, metadata: dict[str, Any]) -> str:
    haystack = file_name.lower()
    file_hints = {
        "Hela cell": r"hela",
        "heart": r"heart",
        "brain": r"brain",
        "liver": r"liver",
        "lung": r"lung",
        "muscle": r"muscle",
        "spleen": r"spleen",
        "pancreas": r"pancreas",
        "duodenum": r"duodenum",
        "jejunum": r"jejunum",
        "ileum": r"ileum",
        "kidney": r"kidney",
        "testis": r"testis",
    }
    file_values = [label for label, pattern in file_hints.items() if re.search(pattern, haystack)]
    if file_values:
        return "; ".join(_dedupe(file_values))

    values: list[str] = []
    sdrf_rows = metadata.get("sdrf_rows")
    if isinstance(sdrf_rows, list):
        for row in sdrf_rows:
            if not isinstance(row, dict):
                continue
            for key, value in row.items():
                normalized_key = str(key).lower()
                if any(token in normalized_key for token in ("organism part", "organism_part", "tissue")):
                    for candidate in _flatten_strings(value):
                        if len(candidate) <= 80 and not re.search(r"\.raw|\.mzml|protocol|experiment|gradient|http|not available", candidate, re.I):
                            values.append(candidate)

    values = _dedupe(values)
    if len(values) > 1:
        return "ambiguous"
    return values[0] if values else "unknown"


def _infer_modification(file_name: str, attributes: dict[str, Any], metadata: dict[str, Any]) -> str:
    values: list[str] = []
    workflow = _workflow(attributes, {})
    haystack = " ".join([file_name, workflow]).lower()
    variable_mods = _as_text(_attr_value(attributes, "variable_mods")).lower()
    if re.search(r"phospho|phos\b|ti[-_ ]?imac|phosphorylation", haystack):
        values.append("Phospho")
    elif re.search(r"phospho|phosphorylation|s\\[79\\.97\\]|t\\[79\\.97\\]|y\\[79\\.97\\]|79\\.966", variable_mods):
        values.append("Phospho")
    if re.search(r"\bubi\b|ubiquitin|glygly|k-gg|di-gly", haystack):
        values.append("Ubi")
    elif re.search(r"glygly|k-gg|di-gly|ubiquitin|114\\.04", variable_mods):
        values.append("Ubi")
    if re.search(r"acetyl|\bact[-_]|^act", haystack):
        values.append("Acetyl")
    if re.search(r"glyco|hexnac", haystack):
        values.append("Glyco")
    if re.search(r"fpop|oxidative labeling|oxidative footprinting", haystack):
        values.append("Oxidation")
    return "; ".join(_dedupe(values)) or "no"


def _organism_part_with_species_fallback(organism_part: str, species: str) -> str:
    if organism_part not in {"", "unknown", "ambiguous"}:
        return organism_part
    return ""


def _project_resolution_error(project: dict[str, Any]) -> str:
    primary = project.get("primary_project")
    if not isinstance(primary, dict):
        return "No PRIDE project could be resolved."
    match_type = str(primary.get("match_type") or "")
    try:
        score = int(primary.get("match_score") or 0)
    except (TypeError, ValueError):
        score = 0
    if match_type not in {"exact", "stem"} or score < 90:
        accession = primary.get("project_accession") or "unknown"
        matched = primary.get("matched_file") or ""
        return f"Non-exact PRIDE project match: {accession}, match_type={match_type}, score={score}, matched_file={matched}"
    if project.get("needs_review"):
        reason = str(project.get("resolution_reason") or "project match needs manual review")
        accession = primary.get("project_accession") or "unknown"
        return f"Ambiguous PRIDE project match: {accession}; {reason}"
    return ""


def _project_display_for_failed_resolution(project: dict[str, Any], metadata: dict[str, Any]) -> str:
    primary = project.get("primary_project")
    if isinstance(primary, dict) and project.get("needs_review") and str(primary.get("match_type") or "") in {"exact", "stem"}:
        return "ambiguous"
    return _project_accession(project, metadata)


def _parse_frg_params(text: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        params[key.strip()] = value.strip()
    return params


def _digestion_from_params(params: dict[str, str]) -> str:
    enzymes: list[str] = []
    for slot in ("1", "2"):
        name = params.get(f"search_enzyme_name_{slot}", "")
        cut = params.get(f"search_enzyme_cut_{slot}", "")
        sense = params.get(f"search_enzyme_sense_{slot}", "")
        if name and name.lower() != "null":
            detail = name
            if cut:
                detail += f" ({cut}"
                if sense:
                    detail += f", {sense}-term"
                detail += ")"
            enzymes.append(detail)
    termini = params.get("num_enzyme_termini")
    missed = params.get("allowed_missed_cleavage_1")
    extras = []
    if termini:
        extras.append(f"termini={termini}")
    if missed:
        extras.append(f"missed={missed}")
    return "; ".join([*enzymes, *extras])


def _format_digestion_for_benchmark(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    compact = re.sub(r"[\s_;,-]+", "", text).lower()
    if "argc" in compact and "trypsin" in compact:
        return "ArgC and Trypsin"
    if compact in {"trypsin", "stricttrypsin"}:
        return "Trypsion"
    if text.lower().startswith("stricttrypsin"):
        return "Trypsion"
    return text.replace("Arg-C", "ArgC")


def _digestion(source: ResultSource, attributes: dict[str, Any]) -> tuple[str, str]:
    found = source.find_first_text([r"fragpipe/fragger\.params"])
    if found is not None:
        _, text = found
        params = _parse_frg_params(text)
        digestion = _digestion_from_params(params)
        if digestion:
            return _format_digestion_for_benchmark(digestion), "fragpipe/fragger.params"
    return _format_digestion_for_benchmark(_as_text(_attr_value(attributes, "enzyme"))), "attributes.json"


def _input_file(source: ResultSource, attributes: dict[str, Any], metadata: dict[str, Any], task_state: dict[str, Any]) -> str:
    if task_state.get("source_file"):
        return str(task_state["source_file"])
    if metadata.get("file_name"):
        return str(metadata["file_name"])
    decision_name = _attr_value(attributes, "source_file_name")
    if decision_name:
        return str(decision_name)
    label = source.label
    return re.sub(r"_results(?:\s*\(\d+\))?$", "", Path(label).stem) + Path(label).suffix


def summarize_source(source: ResultSource) -> dict[str, str]:
    attributes = _load_json(source, "attributes")
    asset = _load_json(source, "asset")
    metadata = _load_json(source, "metadata")
    project = _load_json(source, "project")
    parameter_audit = _load_json(source, "parameter_audit")
    task_state = _load_json(source, "task_state")
    decision_trace = _load_json(source, "decision_trace")
    error_message = _error_message(source)
    resolution_error = _project_resolution_error(project) if project else ""
    if resolution_error and not error_message:
        error_message = resolution_error
    file_name = _input_file(source, attributes, metadata, task_state)
    if resolution_error:
        notes = []
        if not attributes:
            notes.append("missing attributes.json")
        notes.append("project match needs review; parameters suppressed")
        row = {
            "Input file": file_name,
            "Project": _project_display_for_failed_resolution(project, metadata),
            "MS_methods": "",
            "Species": "",
            "Organism part": "",
            "Modification": "",
            "Digestion": "",
            "Instrument": "",
            "Workflow": "",
            "FASTA": "",
            "Run directory": str(source.path),
            "Status": "failed",
            "Error": error_message,
            "Parameter source": "",
            "Notes": "; ".join(notes),
        }
        row.update(_audit_fields(file_name, attributes, project, decision_trace, asset, parameter_audit))
        return row
    digestion, parameter_source = _digestion(source, attributes)
    species = _infer_species(file_name, attributes, metadata)
    organism_part = _organism_part_with_species_fallback(_infer_organism_part(file_name, metadata), species)

    notes: list[str] = []
    if not attributes:
        notes.append("missing attributes.json")
    if not project:
        notes.append("missing project_resolution.json")
    if parameter_source == "attributes.json":
        notes.append("fragger.params not found; digestion from attributes")
    if error_message:
        notes.append("planning failed")

    row = {
        "Input file": file_name,
        "Project": _project_accession(project, metadata),
        "MS_methods": _infer_ms_methods(file_name, attributes, metadata),
        "Species": species,
        "Organism part": organism_part,
        "Modification": _infer_modification(file_name, attributes, metadata),
        "Digestion": digestion,
        "Instrument": _as_text(_attr_value(attributes, "instrument_name")),
        "Workflow": _workflow(attributes, decision_trace),
        "FASTA": _fasta(attributes, decision_trace),
        "Run directory": str(source.path),
        "Status": _as_text(task_state.get("status")) or ("failed" if error_message else ""),
        "Error": error_message,
        "Parameter source": parameter_source,
        "Notes": "; ".join(notes),
    }
    row.update(_audit_fields(file_name, attributes, project, decision_trace, asset, parameter_audit))
    return row


def _latest_run_for_file(runs_root: Path, file_name: str) -> Path | None:
    base = _safe_stem(file_name)
    candidates = [path for path in runs_root.glob(f"{base}*") if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _sources_from_file_list(runs_root: Path, file_list: Path) -> list[ResultSource]:
    sources: list[ResultSource] = []
    for raw in file_list.read_text(encoding="utf-8").splitlines():
        item = raw.strip()
        if not item or item.startswith("#"):
            continue
        run_dir = _latest_run_for_file(runs_root, item)
        if run_dir is None:
            sources.append(ResultSource(label=item, path=runs_root / _safe_stem(item)))
        else:
            sources.append(ResultSource(label=item, path=run_dir))
    return sources


def _discover_sources(runs_root: Path) -> list[ResultSource]:
    sources: list[ResultSource] = []
    if not runs_root.exists():
        return sources
    for path in sorted(runs_root.iterdir(), key=lambda item: item.stat().st_mtime):
        if path.is_dir() and (path / "attributes.json").exists():
            sources.append(ResultSource(label=path.name, path=path))
    return sources


def _sources_from_zips(paths: list[Path]) -> list[ResultSource]:
    sources: list[ResultSource] = []
    for path in paths:
        if path.exists() and path.is_file() and path.suffix.lower() == ".zip":
            sources.append(ResultSource(label=path.name, path=path, is_zip=True))
    return sources


def _xlsx_col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _xlsx_cell(row: int, col: int, value: Any, style: int | None = None) -> str:
    ref = f"{_xlsx_col_name(col)}{row}"
    text = escape(_as_text(value))
    style_attr = f' s="{style}"' if style is not None else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{text}</t></is></c>'


def write_xlsx(rows: list[dict[str, str]], output: Path) -> None:
    columns = [*MAIN_COLUMNS, *SUPPORT_COLUMNS, *AUDIT_COLUMNS]
    sheet_rows = []
    sheet_rows.append(
        '<row r="1">'
        + "".join(_xlsx_cell(1, col_index, column, style=1) for col_index, column in enumerate(columns, start=1))
        + "</row>"
    )
    for row_index, row in enumerate(rows, start=2):
        sheet_rows.append(
            f'<row r="{row_index}">'
            + "".join(_xlsx_cell(row_index, col_index, row.get(column, "")) for col_index, column in enumerate(columns, start=1))
            + "</row>"
        )

    base_widths = [36, 14, 22, 24, 24, 44, 42, 32, 24, 28, 42, 14, 52, 24, 36]
    audit_widths = [18, 18, 34, 34, 18, 18, 62, 20, 18, 16, 58, 58, 58, 58, 58, 58, 44, 62, 18, 14, 20, 20, 16, 12, 18, 32, 32, 58, 58, 14, 58]
    widths = "".join(
        f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>'
        for idx, width in enumerate([*base_widths, *audit_widths], start=1)
    )
    last_col = _xlsx_col_name(len(columns))
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{widths}</cols>"
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        f'<autoFilter ref="A1:{last_col}1"/>'
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Benchmark" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", styles_xml)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export PRIDE agent run summaries to an Excel benchmark table.")
    parser.add_argument("--runs-root", default="runs", type=Path, help="Directory containing run folders.")
    parser.add_argument("--file-list", type=Path, help="Optional text file with one input file per line; preserves this order.")
    parser.add_argument("--zip", dest="zips", action="append", default=[], type=Path, help="Optional downloaded result ZIP. Can be repeated.")
    parser.add_argument("--output", default=Path("benchmark.xlsx"), type=Path, help="Output .xlsx path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sources: list[ResultSource]
    if args.file_list:
        sources = _sources_from_file_list(args.runs_root, args.file_list)
    else:
        sources = _discover_sources(args.runs_root)
    sources.extend(_sources_from_zips(args.zips))

    rows = [summarize_source(source) for source in sources]
    write_xlsx(rows, args.output)
    print(f"Wrote {len(rows)} rows to {args.output}")
    print(f"Generated at {datetime.now().isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
