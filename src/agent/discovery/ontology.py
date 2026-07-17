from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SpeciesTerm:
    canonical: str
    scientific_name: str
    taxon_id: str
    proteome_id: str | None
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class PtmTerm:
    canonical: str
    display_name: str
    aliases: tuple[str, ...]
    query_terms: tuple[str, ...]
    semantic_terms: tuple[str, ...] = ()
    enrichment_methods: tuple[str, ...] = ()
    subtypes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LabelingTerm:
    canonical: str
    display_name: str
    aliases: tuple[str, ...]
    query_terms: tuple[str, ...]


@dataclass(frozen=True)
class SemanticPtmInterpretation:
    canonical: str
    confidence: float
    evidence_terms: tuple[str, ...] = ()
    enrichment_methods: tuple[str, ...] = ()
    subtypes: tuple[str, ...] = ()
    trace: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImmunopeptideTerm:
    canonical: str
    display_name: str
    aliases: tuple[str, ...]
    query_terms: tuple[str, ...]
    semantic_terms: tuple[str, ...] = ()
    enrichment_methods: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticImmunopeptideInterpretation:
    scope: str
    confidence: float
    evidence_terms: tuple[str, ...] = ()
    enrichment_methods: tuple[str, ...] = ()
    hla_classes: tuple[str, ...] = ()
    hla_alleles: tuple[str, ...] = ()
    trace: tuple[str, ...] = ()


SPECIES_TERMS: tuple[SpeciesTerm, ...] = (
    SpeciesTerm(
        canonical="human",
        scientific_name="Homo sapiens",
        taxon_id="9606",
        proteome_id="UP000005640",
        aliases=("human", "homo sapiens", "hela", "hek293", "hct116", "a549", "jurkat", "293t", "9606"),
    ),
    SpeciesTerm(
        canonical="mouse",
        scientific_name="Mus musculus",
        taxon_id="10090",
        proteome_id="UP000000589",
        aliases=("mouse", "murine", "mus musculus", "10090"),
    ),
    SpeciesTerm(
        canonical="rat",
        scientific_name="Rattus norvegicus",
        taxon_id="10116",
        proteome_id="UP000002494",
        aliases=("rat", "rattus", "rattus norvegicus", "10116"),
    ),
    SpeciesTerm(
        canonical="yeast",
        scientific_name="Saccharomyces cerevisiae",
        taxon_id="559292",
        proteome_id="UP000002311",
        aliases=("yeast", "saccharomyces", "saccharomyces cerevisiae", "559292"),
    ),
    SpeciesTerm(
        canonical="e_coli",
        scientific_name="Escherichia coli",
        taxon_id="562",
        proteome_id="UP000000625",
        aliases=("e coli", "e. coli", "ecoli", "escherichia coli", "escherichia", "562"),
    ),
    SpeciesTerm(
        canonical="rice",
        scientific_name="Oryza sativa",
        taxon_id="4530",
        proteome_id="UP000059680",
        aliases=("rice", "oryza", "oryza sativa", "4530", "39947"),
    ),
)


IMMUNOPEPTIDE_TERMS: tuple[ImmunopeptideTerm, ...] = (
    ImmunopeptideTerm(
        canonical="immunopeptidomics",
        display_name="Immunopeptidomics / HLA-MHC ligandome",
        aliases=(
            "immunopeptidomics",
            "immunopeptidome",
            "immunopeptide",
            "immunopeptides",
            "hla ligandome",
            "hla ligand",
            "hla ligands",
            "hla peptidome",
            "mhc ligandome",
            "mhc ligand",
            "mhc ligands",
            "mhc peptidome",
            "eluted ligand",
            "eluted ligands",
            "hla-eluted peptide",
            "hla eluted peptide",
            "mhc-eluted peptide",
            "mhc eluted peptide",
            "neoantigen",
            "neoantigens",
            "tumor antigen",
            "tumour antigen",
            "cancer antigen",
            "antigen presentation",
            "antigen processing",
            "免疫肽",
            "免疫肽组",
            "免疫肽组学",
            "免疫肽谱",
            "hla配体组",
            "hla 配体组",
            "mhc配体组",
            "mhc 配体组",
            "抗原呈递",
            "新抗原",
            "肿瘤新抗原",
            "peptidome",
            "nonspecific peptide",
        ),
        query_terms=(
            "immunopeptidomics",
            "immunopeptidome",
            "HLA ligandome",
            "MHC ligandome",
            "HLA peptidome",
            "MHC peptidome",
            "HLA eluted ligand",
            "MHC eluted ligand",
            "HLA class I ligandome",
            "HLA class II ligandome",
            "neoantigen HLA",
            "antigen presentation mass spectrometry",
            "HLA immunoprecipitation",
            "MHC immunoaffinity purification",
        ),
        semantic_terms=(
            "hla-eluted peptide",
            "hla eluted peptide",
            "mhc-eluted peptide",
            "mhc eluted peptide",
            "immuno-peptidomics",
            "immuno peptidomics",
            "antigen presentation",
            "antigen processing",
            "class i immunopeptidome",
            "class ii immunopeptidome",
            "tumor antigen discovery",
            "neoantigen discovery",
        ),
        enrichment_methods=(
            "HLA immunoprecipitation",
            "MHC immunoprecipitation",
            "HLA-IP",
            "MHC-IP",
            "immunoaffinity purification",
            "immunoaffinity chromatography",
            "affinity purification",
            "HLA pull-down",
            "MHC pull-down",
            "pan-HLA",
            "pan HLA",
            "W6/32",
            "anti-HLA",
            "anti HLA",
            "anti-MHC",
            "anti MHC",
            "HLA antibody",
            "MHC antibody",
        ),
        classes=(
            "class I",
            "class II",
            "class 1",
            "class 2",
            "HLA class I",
            "HLA class II",
            "MHC class I",
            "MHC class II",
            "HLA-I",
            "HLA-II",
            "MHC-I",
            "MHC-II",
        ),
    ),
)


PTM_TERMS: tuple[PtmTerm, ...] = (
    PtmTerm(
        canonical="phospho",
        display_name="Phosphorylation",
        aliases=(
            "phospho",
            "phosphorylation",
            "phosphoproteomics",
            "phosphoproteome",
            "phosphopeptide",
            "phosphopeptides",
            "pser",
            "pthr",
            "ptyr",
            "ps",
            "pt",
            "py",
            "sty phosphorylation",
            "phosphosite",
            "phosphosites",
            "tio2",
            "titanium dioxide",
            "imac",
            "ti-imac",
            "fe-imac",
            "ga-imac",
            "ti4+-imac",
            "moac",
            "polymac",
            "titansphere",
        ),
        query_terms=(
            "phosphoproteomics",
            "phosphoproteome",
            "phosphopeptide enrichment",
            "enriched phosphopeptides",
            "phosphosite localization",
            "phosphotyrosine enrichment",
            "anti-phosphotyrosine antibody enrichment",
            "TiO2 phosphopeptide",
            "Ti-IMAC phosphopeptide",
            "Fe-IMAC phosphopeptide",
            "MOAC phosphoproteomics",
            "kinase substrate phosphoproteomics",
        ),
        semantic_terms=(
            "phosphopeptide enrichment",
            "enriched phosphopeptides",
            "kinase substrate",
            "kinase signaling",
            "phosphosite localization",
            "phosphotyrosine enrichment",
            "anti-phosphotyrosine antibody enrichment",
            "metal oxide affinity chromatography",
            "titanium dioxide beads",
            "titanium dioxide",
        ),
        enrichment_methods=(
            "IMAC",
            "TiO2",
            "Ti-IMAC",
            "Fe-IMAC",
            "Ga-IMAC",
            "Ti4+-IMAC",
            "Fe-NTA",
            "MOAC",
            "metal oxide affinity chromatography",
            "PolyMAC",
            "Titansphere",
            "titanium dioxide beads",
            "phosphotyrosine enrichment",
            "phosphotyrosine-containing peptides",
            "anti-phosphotyrosine antibody enrichment",
            "4G10",
            "PT-66",
        ),
        subtypes=("pSer", "pThr", "pTyr", "pS", "pT", "pY", "STY phosphorylation"),
    ),
    PtmTerm(
        canonical="acetyl",
        display_name="Acetylation",
        aliases=("acetyl", "acetylation", "acetylome", "lysine acetylation", "kac", "ac-k", "acetyl-lysine"),
        query_terms=("acetylome", "acetylation", "lysine acetylation", "acetyl proteomics", "acetyl-lysine enrichment"),
        semantic_terms=("acetyl-lysine enrichment", "acetyl lysine enrichment", "lysine acetylome"),
        enrichment_methods=("acetyl-lysine enrichment", "anti-acetyl lysine antibody"),
        subtypes=("Kac", "Ac-K"),
    ),
    PtmTerm(
        canonical="ubiquitin",
        display_name="Ubiquitination",
        aliases=("ubiquitin", "ubiquitinome", "ubiquitination", "ubiquitylation", "glygly", "gly-gly", "di-glycine", "diglycine", "digly", "k-gg", "kgg"),
        query_terms=("ubiquitinome", "ubiquitin", "ubiquitination", "ubiquitylation", "GlyGly", "di-glycine", "K-GG", "ubiquitin remnant profiling"),
        semantic_terms=("ubiquitin remnant profiling", "di-glycine remnant", "gly-gly remnant", "ubiquitin remnant"),
        enrichment_methods=("diGly enrichment", "K-GG enrichment", "ubiquitin remnant antibody"),
        subtypes=("GlyGly", "diGly", "K-GG", "KGG"),
    ),
    PtmTerm(
        canonical="glyco",
        display_name="Glycosylation",
        aliases=("glyco", "glycosylation", "glycoproteomics", "n-glyco", "o-glyco", "hexnac", "glycopeptide", "glycopeptides"),
        query_terms=("glycoproteomics", "glycosylation", "glycopeptide", "N-glyco", "O-glyco", "HexNAc", "HILIC glycopeptide", "lectin enrichment"),
        semantic_terms=("glycopeptide enrichment", "n-linked glycosylation", "o-linked glycosylation", "speg"),
        enrichment_methods=("HILIC", "lectin enrichment", "SPEG", "hydrazide enrichment"),
        subtypes=("N-glyco", "O-glyco", "HexNAc"),
    ),
    PtmTerm(
        canonical="methyl",
        display_name="Methylation",
        aliases=("methyl", "methylation", "methylome", "lysine methylation", "arginine methylation", "kme", "rme"),
        query_terms=("methylation", "methyl proteomics", "methylome", "lysine methylation", "arginine methylation", "Kme", "Rme"),
        semantic_terms=("protein methylome", "methyl-lysine", "methyl-arginine"),
        enrichment_methods=("methyl-lysine antibody", "methyl-arginine antibody"),
        subtypes=("Kme", "Rme"),
    ),
)


_HLA_ALLELE_RE = re.compile(r"\bHLA-[A-Z0-9]+(?:\*[0-9]{2}(?::[0-9]{2}){0,2})\b", re.IGNORECASE)


LABELING_TERMS: tuple[LabelingTerm, ...] = (
    LabelingTerm(
        canonical="label_free",
        display_name="Label-free",
        aliases=("label-free", "label free", "label_free", "lfq", "unlabeled", "unlabelled"),
        query_terms=("label-free", "label free", "LFQ"),
    ),
    LabelingTerm(
        canonical="TMT",
        display_name="TMT",
        aliases=(
            "tmt",
            "tmt6",
            "tmt6plex",
            "tmt10",
            "tmt10plex",
            "tmt11",
            "tmt11plex",
            "tmt16",
            "tmt16plex",
            "tmt18",
            "tmt18plex",
            "tandem mass tag",
            "tandem mass tags",
        ),
        query_terms=("TMT", "TMT10", "TMT16", "tandem mass tag"),
    ),
    LabelingTerm(
        canonical="iTRAQ",
        display_name="iTRAQ",
        aliases=("itraq", "itraq4", "itraq4plex", "itraq8", "itraq8plex", "isobaric tags for relative and absolute quantitation"),
        query_terms=("iTRAQ", "iTRAQ4", "iTRAQ8"),
    ),
)


def _token_pattern(token: str) -> re.Pattern[str]:
    folded = token.casefold()
    if re.fullmatch(r"[a-z0-9_]+", folded):
        return re.compile(rf"(?<![a-z0-9]){re.escape(folded)}(?![a-z0-9])")
    return re.compile(re.escape(folded))


def token_in_text(text: str, token: str) -> bool:
    return bool(_token_pattern(token).search(str(text or "").casefold()))


def normalize_species(value: str | None) -> SpeciesTerm | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    for term in SPECIES_TERMS:
        if text == term.canonical or text == term.scientific_name.casefold() or text == term.taxon_id:
            return term
        if any(text == alias.casefold() for alias in term.aliases):
            return term
        if any(token_in_text(text, alias) for alias in term.aliases):
            return term
    return None


def normalize_species_values(values: Iterable[str]) -> tuple[list[str], list[str]]:
    canonical: list[str] = []
    taxon_ids: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = normalize_species(value)
        if term is None:
            text = str(value or "").strip()
            if not text:
                continue
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                canonical.append(text)
            continue
        if term.canonical in seen:
            continue
        seen.add(term.canonical)
        canonical.append(term.canonical)
        taxon_ids.append(term.taxon_id)
    return canonical, taxon_ids


def species_aliases(value: str) -> tuple[str, ...]:
    term = normalize_species(value)
    if term is None:
        return (str(value).casefold(),)
    return (*term.aliases, term.scientific_name, term.taxon_id)


def species_from_text(text: str) -> tuple[list[str], list[str]]:
    canonical: list[str] = []
    taxon_ids: list[str] = []
    for term in SPECIES_TERMS:
        if any(token_in_text(text, alias) for alias in term.aliases):
            canonical.append(term.canonical)
            taxon_ids.append(term.taxon_id)
    return sorted(set(canonical)), sorted(set(taxon_ids))


def normalize_ptm_type(value: str | None) -> str:
    text = str(value or "phospho").strip().casefold()
    if not text:
        return "phospho"
    if text in {"unknown", "unknown_ptm", "any", "unspecified"}:
        return "unknown_ptm"
    for term in PTM_TERMS:
        all_terms = (*term.aliases, *term.semantic_terms, *term.enrichment_methods, *term.subtypes)
        if text == term.canonical.casefold() or any(text == alias.casefold() for alias in all_terms):
            return term.canonical
    return text.replace(" ", "_")


def ptm_query_terms(value: str | None) -> tuple[str, ...]:
    canonical = normalize_ptm_type(value)
    for term in PTM_TERMS:
        if term.canonical == canonical:
            return term.query_terms
    return (canonical.replace("_", " "),)


def ptm_aliases(value: str | None) -> tuple[str, ...]:
    canonical = normalize_ptm_type(value)
    for term in PTM_TERMS:
        if term.canonical == canonical:
            return (*term.aliases, *term.semantic_terms, *term.enrichment_methods, *term.subtypes)
    return (canonical.replace("_", " "), canonical)


def ptm_term(value: str | None) -> PtmTerm | None:
    canonical = normalize_ptm_type(value)
    return next((term for term in PTM_TERMS if term.canonical == canonical), None)


def is_immunopeptidomics_goal(value: str | None) -> bool:
    text = str(value or "").strip().casefold().replace("_", " ")
    if not text:
        return False
    if text in {"immunopeptidomics", "immunopeptide", "immunopeptides", "hla", "mhc", "hla ligandome", "mhc ligandome"}:
        return True
    return any(token_in_text(text, token) for term in IMMUNOPEPTIDE_TERMS for token in (*term.aliases, *term.semantic_terms))


def immunopeptide_query_terms() -> tuple[str, ...]:
    terms: list[str] = []
    for term in IMMUNOPEPTIDE_TERMS:
        terms.extend(term.query_terms)
    return tuple(_dedupe_preserve(terms))


def immunopeptide_aliases() -> tuple[str, ...]:
    terms: list[str] = []
    for term in IMMUNOPEPTIDE_TERMS:
        terms.extend((*term.aliases, *term.semantic_terms, *term.enrichment_methods, *term.classes))
    return tuple(_dedupe_preserve(terms))


def interpret_immunopeptide_metadata(text: str) -> SemanticImmunopeptideInterpretation:
    """Normalize immunopeptidomics / HLA-MHC ligandome metadata evidence."""
    haystack = str(text or "")
    evidence_terms: list[str] = []
    methods: list[str] = []
    hla_classes: list[str] = []
    trace: list[str] = []
    for term in IMMUNOPEPTIDE_TERMS:
        for token in (*term.aliases, *term.query_terms, *term.semantic_terms):
            if token_in_text(haystack, token):
                evidence_terms.append(token)
                trace.append(f"term:{token}->{term.canonical}")
        for token in term.enrichment_methods:
            if token_in_text(haystack, token):
                methods.append(token)
                evidence_terms.append(token)
                trace.append(f"method:{token}->{term.canonical}")
        for token in term.classes:
            if _hla_class_token_in_text(haystack, token):
                normalized_class = _normalize_hla_class(token)
                hla_classes.append(normalized_class)
                evidence_terms.append(token)
                trace.append(f"class:{token}->{normalized_class}")
    alleles = [match.group(0).upper() for match in _HLA_ALLELE_RE.finditer(haystack)]
    for allele in alleles:
        evidence_terms.append(allele)
        trace.append(f"allele:{allele}->hla_allele")
    score = len(set(evidence_terms)) + 0.5 * len(set(methods)) + 0.5 * len(set(hla_classes)) + 0.35 * len(set(alleles))
    if score <= 0:
        return SemanticImmunopeptideInterpretation(
            scope="unknown",
            confidence=0.0,
            trace=("no_semantic_immunopeptide_evidence",),
        )
    confidence = min(1.0, 0.35 + 0.11 * score)
    return SemanticImmunopeptideInterpretation(
        scope="immunopeptidomics",
        confidence=round(confidence, 3),
        evidence_terms=tuple(_dedupe_preserve(evidence_terms)),
        enrichment_methods=tuple(_dedupe_preserve(methods)),
        hla_classes=tuple(_dedupe_preserve(hla_classes)),
        hla_alleles=tuple(_dedupe_preserve(alleles)),
        trace=tuple(_dedupe_preserve(trace)),
    )


def _normalize_hla_class(value: str) -> str:
    text = str(value or "").casefold().replace("-", " ")
    if "ii" in text or "class 2" in text:
        return "class_ii"
    return "class_i"


def _hla_class_token_in_text(text: str, token: str) -> bool:
    folded_token = str(token or "").casefold()
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(folded_token)}(?![a-z0-9])", str(text or "").casefold()))


def interpret_ptm_metadata(text: str, requested: str | None = None) -> SemanticPtmInterpretation:
    """Normalize PTM-related free text into ontology-backed PTM evidence."""
    haystack = str(text or "")
    requested_canonical = normalize_ptm_type(requested) if requested else None
    matches: dict[str, dict[str, object]] = {}
    for term in PTM_TERMS:
        evidence_terms: list[str] = []
        methods: list[str] = []
        subtypes: list[str] = []
        trace: list[str] = []
        for token in (*term.aliases, *term.query_terms, *term.semantic_terms):
            if token_in_text(haystack, token):
                evidence_terms.append(token)
                trace.append(f"term:{token}->{term.canonical}")
        for token in term.enrichment_methods:
            if token_in_text(haystack, token):
                methods.append(token)
                evidence_terms.append(token)
                trace.append(f"enrichment:{token}->{term.canonical}")
        for token in term.subtypes:
            if token_in_text(haystack, token):
                subtypes.append(token)
                evidence_terms.append(token)
                trace.append(f"subtype:{token}->{term.canonical}")
        score = len(set(evidence_terms)) + 0.5 * len(set(methods)) + 0.35 * len(set(subtypes))
        if score > 0:
            matches[term.canonical] = {
                "score": score,
                "evidence_terms": tuple(_dedupe_preserve(evidence_terms)),
                "enrichment_methods": tuple(_dedupe_preserve(methods)),
                "subtypes": tuple(_dedupe_preserve(subtypes)),
                "trace": tuple(_dedupe_preserve(trace)),
            }
    if not matches:
        return SemanticPtmInterpretation(
            canonical=requested_canonical or "unknown_ptm",
            confidence=0.0,
            trace=("no_semantic_ptm_evidence",),
        )
    if requested_canonical in matches:
        canonical = requested_canonical
    else:
        canonical = sorted(matches, key=lambda key: float(matches[key]["score"]), reverse=True)[0]
    payload = matches[canonical]
    confidence = min(1.0, 0.35 + 0.12 * float(payload["score"]))
    return SemanticPtmInterpretation(
        canonical=canonical,
        confidence=round(confidence, 3),
        evidence_terms=payload["evidence_terms"],  # type: ignore[arg-type]
        enrichment_methods=payload["enrichment_methods"],  # type: ignore[arg-type]
        subtypes=payload["subtypes"],  # type: ignore[arg-type]
        trace=payload["trace"],  # type: ignore[arg-type]
    )


def _dedupe_preserve(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def normalize_labeling_strategy(value: str | None) -> str:
    text = str(value or "label_free").strip().casefold()
    if not text:
        return "label_free"
    if text in {"any", "unknown"}:
        return "unknown"
    for term in LABELING_TERMS:
        if text == term.canonical.casefold() or any(text == alias.casefold() for alias in term.aliases):
            return term.canonical
    return text.replace(" ", "_")


def labeling_query_terms(value: str | None) -> tuple[str, ...]:
    canonical = normalize_labeling_strategy(value)
    for term in LABELING_TERMS:
        if term.canonical == canonical:
            return term.query_terms
    return ()


def labeling_aliases(value: str | None) -> tuple[str, ...]:
    canonical = normalize_labeling_strategy(value)
    for term in LABELING_TERMS:
        if term.canonical == canonical:
            return term.aliases
    return ()


def labeling_from_text(text: str) -> str | None:
    for term in LABELING_TERMS:
        if any(token_in_text(text, alias) for alias in term.aliases):
            return term.canonical
    return None


_GENERAL_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "or",
    "for",
    "from",
    "with",
    "without",
    "the",
    "of",
    "to",
    "in",
    "on",
    "by",
    "find",
    "search",
    "small",
    "data",
    "dataset",
    "datasets",
    "file",
    "files",
    "file-level",
    "level",
    "project",
    "projects",
    "model",
    "modeling",
    "modelling",
    "training",
    "prediction",
    "species",
    "candidate",
    "candidates",
    "keep",
    "open",
    "prioritize",
    "prioritise",
    "priority",
    "metadata",
    "evidence",
    "constraint",
    "constraints",
    "safe",
    "clean",
    "valid",
    "trust",
    "quality",
    "report",
    "建模",
    "数据",
    "数据集",
    "寻找",
    "检索",
    "用于",
    "小型",
}


def general_query_terms_from_text(text: str, *, max_terms: int = 12) -> tuple[str, ...]:
    """Extract safe, repository-searchable phrases for general discovery.

    This is intentionally conservative: it preserves ontology-backed phrases when
    present, then adds short meaningful n-grams from the user's free text. It is
    not a task-specific classifier, so HLA, drug treatment, disease, or future
    goals can all enter through the same general discovery target.
    """
    raw = str(text or "").strip()
    if not raw:
        return ()
    folded = raw.casefold()
    terms: list[str] = []
    ontology_terms: list[str] = []
    ontology_terms.extend(immunopeptide_query_terms())
    ontology_terms.extend(immunopeptide_aliases())
    for term in PTM_TERMS:
        ontology_terms.extend((*term.query_terms, *term.aliases, *term.semantic_terms, *term.enrichment_methods, *term.subtypes))
    for term in LABELING_TERMS:
        ontology_terms.extend((*term.query_terms, *term.aliases))
    broad_context_terms = (
        "drug treatment",
        "drug treated",
        "drug perturbation",
        "perturbation",
        "inhibitor",
        "kinase inhibitor",
        "compound treatment",
        "disease cohort",
        "cell line",
        "tissue",
        "clinical sample",
        "plasma",
        "serum",
        "dda",
        "data dependent",
        "shotgun proteomics",
    )
    ontology_terms.extend(broad_context_terms)
    for term in _dedupe_preserve(ontology_terms):
        if token_in_text(folded, term):
            terms.append(term)

    ascii_tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9+\-_/]*", raw)
    clean_tokens = [
        token
        for token in ascii_tokens
        if len(token) >= 3 and token.casefold() not in _GENERAL_QUERY_STOPWORDS
    ]
    for width in (3, 2, 1):
        for index in range(0, max(0, len(clean_tokens) - width + 1)):
            phrase = " ".join(clean_tokens[index : index + width])
            if phrase and phrase.casefold() not in _GENERAL_QUERY_STOPWORDS:
                terms.append(phrase)

    cjk_chunks = re.findall(r"[\u3400-\u9fff]{2,}", raw)
    for chunk in cjk_chunks:
        if chunk not in _GENERAL_QUERY_STOPWORDS:
            terms.append(chunk)

    if not terms:
        compact = re.sub(r"\s+", " ", raw).strip()
        if compact:
            terms.append(compact[:120])
    return tuple(_dedupe_preserve(terms)[:max_terms])
