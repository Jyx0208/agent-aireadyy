from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import Field

from agent.ai_ready.spectrum_evidence import detect_fragmentation_from_mapping
from agent.models import JsonModel
from agent.utils import write_json


PeaklistSource = Literal["auto", "existing", "msdt", "rawspectrum"]


class AgentRunPeaklistResult(JsonModel):
    status: str
    agent_run_dir: str
    output_dir: str
    source: str
    peaklist_path: str | None = None
    source_parquet: str | None = None
    spectra_in: int = 0
    spectra_written: int = 0
    estimated_output_bytes: int = 0
    output_bytes: int = 0
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    json_path: str
    report_path: str


def generate_agent_run_peaklist(
    *,
    agent_run_dir: str | Path,
    output_dir: str | Path,
    source: PeaklistSource = "auto",
    max_output_mb: int = 2048,
) -> AgentRunPeaklistResult:
    agent_run_dir = Path(agent_run_dir)
    if not agent_run_dir.exists() or not agent_run_dir.is_dir():
        raise ValueError(f"Agent run directory does not exist: {agent_run_dir}")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "agent_run_peaklist_report.json"
    report_path = output_dir / "agent_run_peaklist_report.md"

    normalized_source = _normalize_source(source)
    existing = sorted(agent_run_dir.rglob("*.mgf"))
    if normalized_source in {"auto", "existing"} and existing:
        result = AgentRunPeaklistResult(
            status="completed",
            agent_run_dir=str(agent_run_dir),
            output_dir=str(output_dir),
            source="existing",
            peaklist_path=str(existing[0]),
            output_bytes=_safe_size(existing[0]),
            warnings=[f"existing_mgf_reused:{existing[0]}"] if len(existing) == 1 else [f"existing_mgf_reused:{existing[0]}", f"additional_mgf_count:{len(existing) - 1}"],
            json_path=str(json_path),
            report_path=str(report_path),
        )
        _write_result(result)
        return result
    if normalized_source == "existing":
        result = _blocked_result(
            agent_run_dir=agent_run_dir,
            output_dir=output_dir,
            json_path=json_path,
            report_path=report_path,
            source="existing",
            blockers=["existing_mgf_not_found"],
        )
        _write_result(result)
        return result

    source_path: Path | None = None
    actual_source = normalized_source
    if normalized_source in {"auto", "msdt"}:
        source_path = _first_spectrum_input(agent_run_dir, "msdt")
        actual_source = "msdt"
    if source_path is None and normalized_source in {"auto", "rawspectrum"}:
        source_path = _first_spectrum_input(agent_run_dir, "rawspectrum")
        actual_source = "rawspectrum"
    if source_path is None:
        result = _blocked_result(
            agent_run_dir=agent_run_dir,
            output_dir=output_dir,
            json_path=json_path,
            report_path=report_path,
            source=normalized_source,
            blockers=["source_parquet_not_found"],
        )
        _write_result(result)
        return result

    try:
        frame = _read_spectrum_frame(source_path)
    except Exception as exc:
        result = _blocked_result(
            agent_run_dir=agent_run_dir,
            output_dir=output_dir,
            json_path=json_path,
            report_path=report_path,
            source=actual_source,
            blockers=[f"source_parquet_unreadable:{exc}"],
            source_parquet=source_path,
        )
        _write_result(result)
        return result

    missing = [column for column in ["scan", "mz_array", "intensity_array"] if column not in frame.columns]
    if missing:
        result = _blocked_result(
            agent_run_dir=agent_run_dir,
            output_dir=output_dir,
            json_path=json_path,
            report_path=report_path,
            source=actual_source,
            blockers=[f"missing_column:{column}" for column in missing],
            source_parquet=source_path,
        )
        _write_result(result)
        return result

    rows = _iter_spectra(frame)
    estimate = _estimate_mgf_bytes(rows)
    max_bytes = int(max_output_mb) * 1024 * 1024
    if max_bytes > 0 and estimate > max_bytes:
        result = _blocked_result(
            agent_run_dir=agent_run_dir,
            output_dir=output_dir,
            json_path=json_path,
            report_path=report_path,
            source=actual_source,
            blockers=[f"estimated_output_too_large:{estimate}>{max_bytes}"],
            source_parquet=source_path,
            spectra_in=len(rows),
            estimated_output_bytes=estimate,
        )
        _write_result(result)
        return result

    peaklist_dir = output_dir / "peaklists"
    peaklist_dir.mkdir(parents=True, exist_ok=True)
    peaklist_path = peaklist_dir / f"{_run_stem(agent_run_dir, source_path)}.mgf"
    spectra_written = _write_mgf(peaklist_path, rows, source_stem=_source_stem(source_path))
    output_bytes = _safe_size(peaklist_path)
    warnings: list[str] = []
    if actual_source == "rawspectrum":
        warnings.append("rawspectrum_peaklist_has_weaker_charge_metadata")
    if spectra_written < len(rows):
        warnings.append(f"empty_spectra_skipped:{len(rows) - spectra_written}")
    result = AgentRunPeaklistResult(
        status="completed" if spectra_written else "blocked",
        agent_run_dir=str(agent_run_dir),
        output_dir=str(output_dir),
        source=actual_source,
        peaklist_path=str(peaklist_path) if spectra_written else None,
        source_parquet=str(source_path),
        spectra_in=len(rows),
        spectra_written=spectra_written,
        estimated_output_bytes=estimate,
        output_bytes=output_bytes,
        warnings=warnings,
        blockers=[] if spectra_written else ["no_spectra_written"],
        json_path=str(json_path),
        report_path=str(report_path),
    )
    _write_result(result)
    return result


def _normalize_source(source: str) -> PeaklistSource:
    value = str(source or "auto").strip().casefold()
    if value not in {"auto", "existing", "msdt", "rawspectrum"}:
        raise ValueError("--source must be one of: auto, existing, msdt, rawspectrum")
    return value  # type: ignore[return-value]


def _first_spectrum_input(agent_run_dir: Path, source: str) -> Path | None:
    preferred = _first_table(agent_run_dir / source)
    if preferred is not None:
        return preferred
    patterns = [f"*{source}*.parquet", f"*{source}*.tsv"]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path for path in agent_run_dir.rglob(pattern) if path.is_file())
    return sorted(candidates)[0] if candidates else None


def _first_table(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    candidates = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.casefold() in {".parquet", ".tsv"}
    )
    return candidates[0] if candidates else None


def _read_spectrum_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.casefold()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".tsv":
        return pd.read_csv(path, sep="\t", dtype=str, low_memory=False)
    raise ValueError(f"Unsupported spectrum table extension: {path.suffix}")


def _iter_spectra(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        scan = _first_int(row.get("scan"))
        mz = _float_list(row.get("mz_array"))
        intensity = _float_list(row.get("intensity_array"))
        fragmentation_method = detect_fragmentation_from_mapping(row.to_dict())
        rows.append(
            {
                "scan": scan,
                "charge": _first_int(row.get("charge", row.get("precursor_charge"))),
                "precursor_mz": _first_float(row.get("precursor_mz")),
                "rt": _first_float(row.get("rt", row.get("retentiontime"))),
                "fragmentation_method": fragmentation_method,
                "mz": mz,
                "intensity": intensity,
            }
        )
    return rows


def _estimate_mgf_bytes(rows: list[dict[str, Any]]) -> int:
    total = 0
    for row in rows:
        total += 180 + 34 * min(len(row.get("mz") or []), len(row.get("intensity") or []))
    return total


def _write_mgf(path: Path, rows: list[dict[str, Any]], *, source_stem: str) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            mz = row.get("mz") or []
            intensity = row.get("intensity") or []
            if not mz or not intensity:
                continue
            scan = _first_int(row.get("scan"))
            charge = _first_int(row.get("charge"))
            title = _mgf_title(source_stem, scan=scan, charge=charge)
            handle.write("BEGIN IONS\n")
            handle.write(f"TITLE={title}\n")
            if scan is not None:
                handle.write(f"SCANS={scan}\n")
            precursor_mz = _first_float(row.get("precursor_mz"))
            if precursor_mz is not None:
                handle.write(f"PEPMASS={precursor_mz:.6f}\n")
            if charge is not None and charge > 0:
                handle.write(f"CHARGE={charge}+\n")
            fragmentation_method = str(row.get("fragmentation_method") or "").strip()
            if fragmentation_method:
                handle.write(f"ACTIVATION={fragmentation_method}\n")
            rt = _first_float(row.get("rt"))
            if rt is not None:
                handle.write(f"RTINSECONDS={rt * 60.0:.6f}\n")
            for mz_value, intensity_value in zip(mz, intensity, strict=False):
                if not math.isfinite(mz_value) or not math.isfinite(intensity_value):
                    continue
                handle.write(f"{mz_value:.6f} {intensity_value:.6f}\n")
            handle.write("END IONS\n\n")
            count += 1
    return count


def _mgf_title(source_stem: str, *, scan: int | None, charge: int | None) -> str:
    if scan is None:
        return source_stem
    scan_padded = f"{scan:05d}"
    if charge is not None and charge > 0:
        return f"{source_stem}.{scan_padded}.{scan_padded}.{charge}"
    return f"{source_stem}.{scan_padded}.{scan_padded}"


def _source_stem(path: Path) -> str:
    stem = path.stem
    for suffix in ["_fp_msdt", "_msdt", "_rawspectrum"]:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _run_stem(agent_run_dir: Path, source_path: Path) -> str:
    source_stem = _source_stem(source_path)
    if source_stem:
        return _safe_stem(source_stem)
    return _safe_stem(agent_run_dir.name)


def _safe_stem(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return text.strip("._-") or "agent_run_peaklist"


def _float_list(value: Any) -> list[float]:
    if value is None:
        return []
    try:
        if pd.isna(value):
            return []
    except Exception:
        pass
    if isinstance(value, str):
        text = value.strip().strip("[]")
        if not text:
            return []
        result: list[float] = []
        for item in re.split(r"[\s,;]+", text):
            if not item:
                continue
            try:
                result.append(float(item))
            except ValueError:
                continue
        return result
    try:
        return [float(item) for item in value]
    except Exception:
        return []


def _first_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _first_int(value: Any) -> int | None:
    number = _first_float(value)
    if number is None or not math.isfinite(number):
        return None
    return int(number)


def _safe_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _blocked_result(
    *,
    agent_run_dir: Path,
    output_dir: Path,
    json_path: Path,
    report_path: Path,
    source: str,
    blockers: list[str],
    source_parquet: Path | None = None,
    spectra_in: int = 0,
    estimated_output_bytes: int = 0,
) -> AgentRunPeaklistResult:
    return AgentRunPeaklistResult(
        status="blocked",
        agent_run_dir=str(agent_run_dir),
        output_dir=str(output_dir),
        source=source,
        source_parquet=str(source_parquet) if source_parquet else None,
        spectra_in=spectra_in,
        estimated_output_bytes=estimated_output_bytes,
        blockers=blockers,
        json_path=str(json_path),
        report_path=str(report_path),
    )


def _write_result(result: AgentRunPeaklistResult) -> None:
    write_json(result.json_path, result.model_dump(mode="json"))
    Path(result.report_path).write_text(_markdown_report(result), encoding="utf-8")


def _markdown_report(result: AgentRunPeaklistResult) -> str:
    lines = [
        "# Agent Run Peaklist Report",
        "",
        f"- Status: `{result.status}`",
        f"- Source: `{result.source}`",
        f"- Agent run dir: `{result.agent_run_dir}`",
        f"- Source parquet: `{result.source_parquet or ''}`",
        f"- Peaklist: `{result.peaklist_path or ''}`",
        f"- Spectra in: {result.spectra_in}",
        f"- Spectra written: {result.spectra_written}",
        f"- Estimated output bytes: {result.estimated_output_bytes}",
        f"- Output bytes: {result.output_bytes}",
        f"- Blockers: {', '.join(result.blockers) if result.blockers else 'None'}",
        f"- Warnings: {', '.join(result.warnings) if result.warnings else 'None'}",
        "",
    ]
    return "\n".join(lines)
