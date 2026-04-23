from __future__ import annotations

from pathlib import Path

from agent.models import AttributeSet, DdaExecutionPlan, ProjectResolution


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _detect_raw_type(source_data_path: Path) -> str:
    suffix = source_data_path.suffix.lower()
    if suffix == ".mzml":
        return "mzml"
    if suffix == ".d":
        return "tims"
    raise ValueError(f"Unsupported source data path for MSDT-Converter: {source_data_path}")


def _species_fasta_name(species: str) -> tuple[str, str]:
    normalized = species.lower()
    multi_species_groups = [
        ("homo sapiens", "human"),
        ("mus musculus", "mouse"),
        ("escherichia coli", "e. coli"),
        ("saccharomyces cerevisiae", "yeast"),
    ]
    if sum(1 for aliases in multi_species_groups if any(alias in normalized for alias in aliases)) > 1:
        return "generic_reference_with_contaminants.fasta", "defaulted"
    if "homo sapiens" in normalized or "human" in normalized:
        return "Homo_sapiens_reviewed.fasta", "inferred"
    if "mus musculus" in normalized or "mouse" in normalized:
        return "Mus_musculus_reviewed.fasta", "inferred"
    return "generic_reference_with_contaminants.fasta", "defaulted"


def _workflow_name(attributes: AttributeSet, raw_data_type: str) -> str:
    label = str(attributes.labeling_strategy.value).lower()
    species = str(attributes.species.value).lower()
    if str(attributes.acquisition_mode.value).upper() != "DDA":
        return ""
    if "human" in species or "homo sapiens" in species:
        if "tmt" in label:
            return "TMT_DDA_human.workflow"
        if "itraq" in label:
            return "iTRAQ_DDA_human.workflow"
        if raw_data_type == "tims":
            return "LFQ_DDA_human_noNQ_tims.workflow"
        return "LFQ_DDA_human_noNQ.workflow"
    if "tmt" in label:
        return "TMT_DDA_generic.workflow"
    if "itraq" in label:
        return "iTRAQ_DDA_generic.workflow"
    if raw_data_type == "tims":
        return "LFQ_DDA_generic_tims.workflow"
    return "LFQ_DDA_generic.workflow"


def _blocking_issues(attributes: AttributeSet, workflow_name: str) -> list[str]:
    issues: list[str] = []
    if str(attributes.acquisition_mode.value).upper() != "DDA":
        issues.append("DIA is not supported for strict MSDT-Converter output in v1.")
    required = {
        "species": attributes.species,
        "instrument_family": attributes.instrument_family,
        "enzyme": attributes.enzyme,
    }
    for name, value in required.items():
        if value.value in (None, "", "unknown"):
            issues.append(f"Missing required attribute: {name}")
        if value.conflict_flag:
            issues.append(f"Conflicting required attribute: {name}")
    if not workflow_name:
        issues.append("No validated FragPipe workflow profile matches the inferred attributes.")
    return issues


def plan_dda_execution(
    task_id: str,
    source_file_name: str,
    source_data_path: str | Path,
    project_resolution: ProjectResolution,
    attributes: AttributeSet,
    output_dir: str | Path,
) -> DdaExecutionPlan:
    source_data_path = Path(source_data_path)
    output_dir = Path(output_dir)
    raw_data_type = _detect_raw_type(source_data_path)
    workspace_root = _workspace_root()
    fasta_file_name, fasta_mode = _species_fasta_name(str(attributes.species.value))
    workflow_name = _workflow_name(attributes, raw_data_type)
    blocking_issues = _blocking_issues(attributes, workflow_name)

    rawspectrum_dir = output_dir / "rawspectrum"
    fragpipe_dir = output_dir / "fragpipe"
    msdt_dir = output_dir / "msdt"
    logs_dir = output_dir / "logs"

    rawspectrum_output_path = rawspectrum_dir / f"{source_data_path.stem}_rawspectrum.parquet"
    manifest_path = fragpipe_dir / "fragpipe-files.fp-manifest"
    expected_pin_path = fragpipe_dir / "exp" / f"{source_data_path.name}_edited.pin"
    converter_config_path = output_dir / "converter_config.json"
    output_paths = {
        "fp_msdt": msdt_dir / f"{source_data_path.stem}_fp_msdt.parquet",
        "ai_ready": output_dir / "ai_ready" / f"{source_data_path.stem}_ai_ready.parquet",
        "run_log": logs_dir / "run.log",
    }

    return DdaExecutionPlan(
        task_id=task_id,
        source_file_name=source_file_name,
        source_data_path=source_data_path,
        raw_data_type=raw_data_type,  # type: ignore[arg-type]
        fasta_path=workspace_root / "profiles" / "fasta" / fasta_file_name,
        fasta_selection_mode=fasta_mode,  # type: ignore[arg-type]
        fragpipe_workflow_path=workspace_root / "profiles" / "fragpipe" / workflow_name,
        manifest_path=manifest_path,
        converter_config_path=converter_config_path,
        rawspectrum_output_path=rawspectrum_output_path,
        fragpipe_workdir=fragpipe_dir,
        expected_pin_path=expected_pin_path,
        expected_pin_glob=str(expected_pin_path),
        output_paths=output_paths,
        needs_review=bool(blocking_issues),
        blocking_issues=blocking_issues,
    )
