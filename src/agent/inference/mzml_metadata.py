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
