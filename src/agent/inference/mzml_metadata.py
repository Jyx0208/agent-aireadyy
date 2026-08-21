from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class MzMLInstrumentMetadata:
    name: str
    family: str
    evidence: str


@dataclass(frozen=True)
class MzMLSpectrumSummary:
    spectrum_list_count: int | None
    observed_spectra: int
    ms1_count: int
    ms2_count: int
    max_ms_level: int | None
    evidence: str


_MODEL_KEYWORDS = (
    "q exactive",
    "orbitrap fusion",
    "orbitrap lumos",
    "orbitrap exploris",
    "orbitrap eclipse",
    "orbitrap elite",
    "orbitrap velos",
    "orbitrap astral",
    "ltq orbitrap",
    "timsTOF".lower(),
    "tripletof",
    "impact",
    "synapt",
    "tof",
)

_NON_MODEL_NAMES = {
    "mass spectrometer",
    "instrument model",
    "instrument configuration",
    "electrospray ionization",
    "nanoelectrospray",
    "orbitrap",
    "time-of-flight",
    "quadrupole",
    "ion trap",
}


def infer_instrument_family_from_name(name: str) -> str:
    lowered = name.lower()
    if "orbitrap" in lowered or "q exactive" in lowered or "exploris" in lowered or "astral" in lowered:
        return "orbitrap"
    if "tims" in lowered:
        return "tims"
    # Bruker Impact HD/II and Waters-style QTOF instruments are a distinct
    # time-of-flight family even when the repository omits the literal
    # ``QTOF`` token from the CV name.
    if any(token in lowered for token in ("qtof", "q-tof", "impact hd", "impact ii", "synapt")):
        return "qtof"
    if "tof" in lowered:
        return "tof"
    return "unknown"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _open_mzml(path: Path) -> BinaryIO:
    if path.name.lower().endswith(".gz"):
        return gzip.open(path, "rb")
    return path.open("rb")


def _looks_like_instrument_model(name: str) -> bool:
    lowered = name.strip().lower()
    if not lowered or lowered in _NON_MODEL_NAMES:
        return False
    return any(keyword in lowered for keyword in _MODEL_KEYWORDS)


def parse_mzml_instrument(path: str | Path) -> MzMLInstrumentMetadata | None:
    mzml_path = Path(path)
    if not mzml_path.exists() or mzml_path.is_dir():
        return None

    cv_names: list[tuple[str, str]] = []
    try:
        with _open_mzml(mzml_path) as handle:
            for event, elem in ET.iterparse(handle, events=("end",)):
                tag = _local_name(elem.tag)
                if tag == "cvParam":
                    name = str(elem.attrib.get("name") or "").strip()
                    accession = str(elem.attrib.get("accession") or "").strip()
                    if name:
                        cv_names.append((name, accession))
                elif tag == "instrumentConfigurationList":
                    break
                elem.clear()
    except (ET.ParseError, OSError, EOFError):
        return None

    if not cv_names:
        return None

    model_name = ""
    model_accession = ""
    for name, accession in cv_names:
        if _looks_like_instrument_model(name):
            model_name = name
            model_accession = accession
            break
    if not model_name:
        return None

    family = infer_instrument_family_from_name(" ".join(name for name, _ in cv_names))
    evidence = f"{model_accession} {model_name}".strip()
    return MzMLInstrumentMetadata(name=model_name, family=family, evidence=evidence)


def summarize_mzml_spectra(path: str | Path, stop_after_first_ms2: bool = False) -> MzMLSpectrumSummary | None:
    mzml_path = Path(path)
    if not mzml_path.exists() or mzml_path.is_dir():
        return None

    spectrum_list_count: int | None = None
    observed_spectra = 0
    ms1_count = 0
    ms2_count = 0
    max_ms_level: int | None = None
    in_spectrum = False
    current_ms_level: int | None = None
    try:
        with _open_mzml(mzml_path) as handle:
            for event, elem in ET.iterparse(handle, events=("start", "end")):
                tag = _local_name(elem.tag)
                if event == "start" and tag == "spectrumList" and spectrum_list_count is None:
                    raw_count = elem.attrib.get("count")
                    try:
                        spectrum_list_count = int(raw_count) if raw_count is not None else None
                    except ValueError:
                        spectrum_list_count = None
                    if spectrum_list_count == 0:
                        break
                    continue
                if event == "start" and tag == "spectrum":
                    in_spectrum = True
                    current_ms_level = None
                    continue
                if event == "start" and in_spectrum and tag == "cvParam" and elem.attrib.get("accession") == "MS:1000511":
                    try:
                        current_ms_level = int(str(elem.attrib.get("value") or "").strip())
                    except ValueError:
                        current_ms_level = None
                    continue
                if event == "end" and tag == "spectrum":
                    observed_spectra += 1
                    if current_ms_level == 1:
                        ms1_count += 1
                    elif current_ms_level == 2:
                        ms2_count += 1
                    if current_ms_level is not None:
                        max_ms_level = current_ms_level if max_ms_level is None else max(max_ms_level, current_ms_level)
                    in_spectrum = False
                    current_ms_level = None
                    elem.clear()
                    if stop_after_first_ms2 and ms2_count > 0:
                        break
                elif event == "end":
                    elem.clear()
    except (ET.ParseError, OSError, EOFError):
        return None

    evidence = (
        f"spectrumList count={spectrum_list_count if spectrum_list_count is not None else 'unknown'}; "
        f"observed spectra={observed_spectra}; MS1={ms1_count}; MS2={ms2_count}; "
        f"max ms level={max_ms_level if max_ms_level is not None else 'unknown'}"
    )
    return MzMLSpectrumSummary(
        spectrum_list_count=spectrum_list_count,
        observed_spectra=observed_spectra,
        ms1_count=ms1_count,
        ms2_count=ms2_count,
        max_ms_level=max_ms_level,
        evidence=evidence,
    )


def dda_mzml_search_blocking_issue(path: str | Path) -> str | None:
    summary = summarize_mzml_spectra(path)
    if summary is None:
        return "Prepared mzML could not be parsed; cannot verify spectra before FragPipe search."
    total = summary.spectrum_list_count if summary.spectrum_list_count is not None else summary.observed_spectra
    if total == 0 or summary.observed_spectra == 0:
        return (
            "Prepared mzML contains no spectra after conversion. "
            "FragPipe/MSFragger DDA search cannot run on an empty mzML. "
            f"{summary.evidence}"
        )
    if summary.ms2_count == 0:
        return (
            "Prepared mzML contains no MS2 spectra. This is usually MS1-only metabolomics/lipidomics "
            "or another non-bottom-up-proteomics acquisition, not a valid DDA peptide search input. "
            f"{summary.evidence}"
        )
    return None
