"""Candidate Evidence Matrix (CEM) for hard scientific conjunction (WP-A)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from agent.discovery.models import DatasetRequest
from agent.models import JsonModel


CellState = Literal["PASS", "FAIL", "UNKNOWN", "CONFLICT", "N/A"]

_MAY_BE_HARD_SOURCES = frozenset({"user", "accepted_recommendation"})
_BUILTIN_HARD_ALIASES = {
    "species": ("species", "organisms", "organism"),
    "acquisition_mode": ("acquisition_mode", "acquisition"),
    "ptm_type": ("ptm_type", "ptm", "modification"),
    "labeling_strategy": ("labeling_strategy", "labeling"),
    "repository": ("repository",),
}


class EvidenceCell(JsonModel):
    requirement_id: str
    state: CellState = "UNKNOWN"
    observed_value: Any = None
    evidence_refs: list[str] = Field(default_factory=list)
    note: str = ""


class CandidateEvidenceRow(JsonModel):
    accession: str
    cells: dict[str, EvidenceCell] = Field(default_factory=dict)
    hard_conjunction_pass: bool = False
    hard_unknown: bool = False
    hard_fail: bool = False
    inspection_backed: bool = False


class CandidateEvidenceMatrix(JsonModel):
    hard_requirement_ids: list[str] = Field(default_factory=list)
    rows: dict[str, CandidateEvidenceRow] = Field(default_factory=dict)
    n_candidates: int = Field(default=0, ge=0)
    n_hard_conjunction_pass: int = Field(default=0, ge=0)
    n_hard_pass_inspected: int = Field(default=0, ge=0)
    n_hard_unknown: int = Field(default=0, ge=0)
    n_hard_fail: int = Field(default=0, ge=0)
    unknown_hard_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    hard_constraint_evidence_gap: float = Field(default=1.0, ge=0.0, le=1.0)
    candidate_level_conjunction_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    provisional: bool = True

    def summary(self) -> dict[str, Any]:
        return {
            "hard_requirement_ids": list(self.hard_requirement_ids),
            "n_candidates": self.n_candidates,
            "n_hard_conjunction_pass": self.n_hard_conjunction_pass,
            "n_hard_pass_inspected": self.n_hard_pass_inspected,
            "n_hard_unknown": self.n_hard_unknown,
            "n_hard_fail": self.n_hard_fail,
            "unknown_hard_rate": self.unknown_hard_rate,
            "hard_constraint_evidence_gap": self.hard_constraint_evidence_gap,
            "candidate_level_conjunction_coverage": self.candidate_level_conjunction_coverage,
            "provisional": self.provisional,
        }


def may_be_hard(*, provenance: str | None = None, source: str | None = None) -> bool:
    """WP-B provenance gate: only user / accepted_recommendation may be hard."""

    for value in (provenance, source):
        if str(value or "").strip().casefold() in _MAY_BE_HARD_SOURCES:
            return True
    return False


def hard_requirement_ids(request: DatasetRequest) -> list[str]:
    """Collect CEM hard rows from hard fields + hard scientific constraints."""

    ids: list[str] = []
    seen: set[str] = set()

    def _add(req_id: str) -> None:
        key = req_id.casefold()
        if key in seen:
            return
        seen.add(key)
        ids.append(req_id)

    for field in request.hard_constraint_fields or []:
        name = str(field or "").strip()
        if not name or name == "repository":
            # Repository is operational, not a per-candidate scientific conjunction row.
            continue
        provenance = (request.constraint_provenance or {}).get(name)
        # Listed hard fields keep hard status; missing provenance is still hard for
        # explicit hard_constraint_fields (product IR). Inferred-only invent is WP-B.
        if provenance is None or may_be_hard(provenance=provenance) or request.is_hard_constraint(name):
            _add(f"field:{name}")

    for constraint in request.scientific_constraints or []:
        if str(getattr(constraint, "strength", "soft") or "soft").casefold() != "hard":
            continue
        source = str(getattr(constraint, "source", "user") or "user")
        if not may_be_hard(source=source):
            continue
        cid = str(getattr(constraint, "id", "") or "").strip() or str(constraint.dimension)
        _add(f"constraint:{cid}")

    return ids


def _normalize_token(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())


def _preview_observed(requirement_id: str, preview: Any, request: DatasetRequest) -> tuple[CellState, Any, list[str]]:
    """Provisional cell from candidate preview / scored fields (search-time)."""

    if requirement_id.startswith("constraint:"):
        # Without structured constraint evaluation at search time, mark UNKNOWN.
        return "UNKNOWN", None, []

    field = requirement_id.split(":", 1)[-1]
    aliases = _BUILTIN_HARD_ALIASES.get(field, (field,))

    observed: Any = None
    refs: list[str] = []
    if field == "species":
        observed = list(getattr(preview, "species", None) or [])
        if observed:
            refs.append("preview.species")
    elif field == "acquisition_mode":
        observed = getattr(preview, "acquisition_mode", None)
        if observed:
            refs.append("preview.acquisition_mode")
    else:
        # Fall back to intent-term hits as weak provisional signal only for UNKNOWN/PASS.
        matched = list(getattr(preview, "matched_intent_terms", None) or [])
        for alias in aliases:
            token = _normalize_token(alias)
            hits = [term for term in matched if token and token in _normalize_token(term)]
            if hits:
                observed = hits
                refs.append("preview.matched_intent_terms")
                break

    if observed in (None, "", [], ()):
        return "UNKNOWN", None, []

    # Compare against request target when available.
    if field == "species" and request.species:
        wanted = {_normalize_token(item) for item in request.species}
        # Also accept common aliases already normalized by scorer into scientific names.
        wanted.update({"homo sapiens" if "human" in wanted else ""})
        wanted.discard("")
        observed_tokens = {_normalize_token(item) for item in observed}
        if observed_tokens & wanted or any(
            any(w in o or o in w for w in wanted) for o in observed_tokens
        ):
            return "PASS", observed, refs
        return "FAIL", observed, refs

    if field == "acquisition_mode" and request.acquisition_mode not in {"", "unknown", "any"}:
        wanted = _normalize_token(request.acquisition_mode)
        got = _normalize_token(observed)
        if wanted in got or got in wanted or (wanted == "dda" and "data dependent" in got):
            return "PASS", observed, refs
        if got and got not in {"unknown", "mixed"}:
            return "FAIL", observed, refs
        return "UNKNOWN", observed, refs

    if field == "ptm_type":
        wanted = _normalize_token(request.ptm_type)
        if wanted in {"", "unknown", "unknown ptm", "any"}:
            return "N/A", observed, refs
        text = _normalize_token(observed if not isinstance(observed, list) else " ".join(map(str, observed)))
        if any(token in text for token in (wanted, "phospho", "phosphorylation") if token):
            # Only PASS when matched terms or observed text supports the request.
            if wanted in text or (wanted.startswith("phospho") and "phospho" in text):
                return "PASS", observed, refs
        return "UNKNOWN", observed, refs

    if field == "labeling_strategy":
        wanted = _normalize_token(request.labeling_strategy)
        if wanted in {"", "unknown", "any"}:
            return "N/A", observed, refs
        text = _normalize_token(observed if not isinstance(observed, list) else " ".join(map(str, observed)))
        if wanted and wanted in text:
            return "PASS", observed, refs
        return "UNKNOWN", observed, refs

    return "UNKNOWN", observed, refs


def build_provisional_cem(
    *,
    request: DatasetRequest,
    previews: list[Any],
    target_projects: int | None = None,
) -> CandidateEvidenceMatrix:
    """Build search-time provisional CEM from ranked previews (not inspection-backed)."""

    requirement_ids = hard_requirement_ids(request)
    rows: dict[str, CandidateEvidenceRow] = {}
    n_pass = 0
    n_unknown = 0
    n_fail = 0

    for preview in previews:
        if getattr(preview, "excluded", False):
            continue
        accession = str(getattr(preview, "project_accession", "") or "").strip().upper()
        if not accession:
            continue
        cells: dict[str, EvidenceCell] = {}
        unknown = False
        fail = False
        for req_id in requirement_ids:
            state, observed, refs = _preview_observed(req_id, preview, request)
            cells[req_id] = EvidenceCell(
                requirement_id=req_id,
                state=state,
                observed_value=observed,
                evidence_refs=refs,
            )
            if state == "UNKNOWN":
                unknown = True
            elif state == "FAIL":
                fail = True
            elif state == "CONFLICT":
                fail = True
        # Conjunction: every hard req PASS or N/A, none FAIL/UNKNOWN/CONFLICT.
        conjunction = bool(requirement_ids) and all(
            cell.state in {"PASS", "N/A"} for cell in cells.values()
        )
        if not requirement_ids:
            # No hard scientific rows => cannot claim hard pass; gap stays open.
            conjunction = False
            unknown = True
        row = CandidateEvidenceRow(
            accession=accession,
            cells=cells,
            hard_conjunction_pass=conjunction,
            hard_unknown=unknown and not fail,
            hard_fail=fail,
            inspection_backed=False,
        )
        rows[accession] = row
        if conjunction:
            n_pass += 1
        elif fail:
            n_fail += 1
        else:
            n_unknown += 1

    n_candidates = len(rows)
    target = max(1, int(target_projects or getattr(request, "max_projects", 1) or 1))
    if n_candidates == 0:
        unknown_rate = 1.0
        gap = 1.0
        coverage = 0.0
    else:
        unknown_rate = n_unknown / n_candidates
        # Gap: fraction not hard-passing (unknown or fail).
        gap = 1.0 - (n_pass / n_candidates)
        coverage = min(1.0, n_pass / target)

    return CandidateEvidenceMatrix(
        hard_requirement_ids=requirement_ids,
        rows=rows,
        n_candidates=n_candidates,
        n_hard_conjunction_pass=n_pass,
        n_hard_pass_inspected=0,  # search-time provisional only
        n_hard_unknown=n_unknown,
        n_hard_fail=n_fail,
        unknown_hard_rate=unknown_rate,
        hard_constraint_evidence_gap=gap,
        candidate_level_conjunction_coverage=coverage,
        provisional=True,
    )


def scientific_stop_ready(
    matrix: CandidateEvidenceMatrix,
    *,
    target_hard_pass_inspected: int,
    unknown_hard_rate_epsilon: float = 0.25,
    corpus_term_coverage: float | None = None,
) -> tuple[bool, str]:
    """Scientific stop may not fire on corpus_term_coverage alone."""

    del corpus_term_coverage  # diagnostic only; intentionally unused
    if matrix.n_hard_pass_inspected < max(0, int(target_hard_pass_inspected)):
        return False, "hard_pass_inspected_below_target"
    if matrix.unknown_hard_rate > unknown_hard_rate_epsilon:
        return False, "unknown_hard_rate_above_epsilon"
    if matrix.n_hard_pass_inspected <= 0:
        return False, "no_inspection_backed_hard_pass"
    return True, "scientific_stop_criteria_met"
