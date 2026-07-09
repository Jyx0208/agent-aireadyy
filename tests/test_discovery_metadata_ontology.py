from __future__ import annotations

from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject
from agent.discovery.ontology import (
    general_query_terms_from_text,
    immunopeptide_query_terms,
    interpret_immunopeptide_metadata,
    interpret_ptm_metadata,
    is_immunopeptidomics_goal,
    normalize_labeling_strategy,
    normalize_ptm_type,
    normalize_species_values,
)
from agent.discovery.query_builder import build_pride_queries
from agent.discovery.scoring import score_project
from agent.discovery.task_readiness import annotate_manifest_task_readiness
from agent.discovery.validity import assess_project_validity
from agent.discovery.value_scoring import annotate_manifest_value_scores


def test_metadata_ontology_normalizes_core_species_ptm_and_labeling() -> None:
    canonical, taxon_ids = normalize_species_values(["Homo sapiens", "rat", "E. coli", "Oryza sativa"])

    assert canonical == ["human", "rat", "e_coli", "rice"]
    assert taxon_ids == ["9606", "10116", "562", "4530"]
    assert normalize_ptm_type("GlyGly") == "ubiquitin"
    assert normalize_ptm_type("lysine acetylation") == "acetyl"
    assert normalize_labeling_strategy("TMT16") == "TMT"
    assert normalize_labeling_strategy("itraq8") == "iTRAQ"


def test_dataset_request_defaults_to_general_open_species() -> None:
    request = DatasetRequest()

    assert request.goal == "general"
    assert request.ptm_type == "unknown_ptm"
    assert request.species == []
    assert request.species_policy == "open"


def test_general_query_terms_preserve_hla_and_drug_context_without_special_target() -> None:
    terms = general_query_terms_from_text(
        "Find small DDA HLA ligandome and drug treatment proteomics datasets with kinase inhibitor context. Keep species open and prioritize metadata evidence."
    )
    joined = " | ".join(terms).casefold()

    assert "hla ligandome" in joined
    assert "drug treatment" in joined
    assert "kinase inhibitor" in joined
    assert "species open" not in joined
    assert "prioritize metadata" not in joined
    assert any("dda" in item.casefold() or "data dependent" in item.casefold() for item in terms)


def test_semantic_ptm_interpretation_normalizes_phospho_context_and_methods() -> None:
    text = (
        "Kinase signaling phosphosite localization after phosphotyrosine enrichment "
        "using anti-phosphotyrosine antibody enrichment and Ti4+-IMAC."
    )

    result = interpret_ptm_metadata(text)

    assert result.canonical == "phospho"
    assert result.confidence >= 0.5
    assert "kinase signaling" in result.evidence_terms
    assert "Ti4+-IMAC" in result.enrichment_methods
    assert any("phosphotyrosine" in item for item in result.evidence_terms)


def test_semantic_ptm_interpretation_normalizes_other_ptm_tasks() -> None:
    assert interpret_ptm_metadata("Kac acetylome with acetyl-lysine enrichment").canonical == "acetyl"
    assert interpret_ptm_metadata("K-GG GlyGly ubiquitin remnant profiling").canonical == "ubiquitin"
    assert interpret_ptm_metadata("glycopeptide HILIC lectin enrichment").canonical == "glyco"
    assert interpret_ptm_metadata("methylome Kme Rme profiling").canonical == "methyl"


def test_query_builder_expands_non_phospho_ptm_and_isobaric_labeling_terms() -> None:
    request = DatasetRequest(
        goal="ptm",
        species=["rat"],
        canonical_species=["rat"],
        organism_taxon_id=["10116"],
        ptm_type="ubiquitin",
        modification_scope="ubiquitin",
        labeling_strategy="TMT",
        max_candidate_projects=20,
    )

    queries = build_pride_queries(request)
    joined = " | ".join(queries).casefold()

    assert "rat" in joined or "rattus" in joined
    assert "ubiquitin" in joined or "glygly" in joined
    assert "tmt" in joined
    assert "dia" not in joined


def test_tmt_and_itraq_are_labeling_evidence_not_acquisition_conflicts() -> None:
    request = DatasetRequest(species=["human"], ptm_type="acetyl", labeling_strategy="TMT")
    project_record = {
        "accession": "PXDTEST001",
        "title": "Human TMT acetylome DDA",
        "projectDescription": "Homo sapiens lysine acetylation dataset acquired by DDA HCD with TMT16.",
        "organisms": [{"name": "Homo sapiens"}],
        "experimentTypes": [{"name": "shotgun proteomics"}],
        "keywords": ["TMT16", "acetylation", "DDA"],
    }

    score = score_project(project_record, request)
    project = DiscoveredProject(
        project_accession="PXDTEST001",
        project_title="Human TMT acetylome DDA",
        species=score.species,
        canonical_species=score.canonical_species,
        organism_taxon_id=score.organism_taxon_id,
        acquisition_mode=score.acquisition_mode,
        ptm_type=score.ptm_type,
        modification_scope=score.modification_scope,
        labeling_strategy=score.labeling_strategy,
        project_score=score.project_score,
        evidence=score.evidence,
    )
    decision = assess_project_validity(project, request)

    assert not score.excluded
    assert score.labeling_strategy == "TMT"
    assert score.ptm_type == "acetyl"
    assert all("unsupported_acquisition" not in reason for reason in decision.reasons)
    assert "missing_labeling_strategy_evidence" not in decision.reasons


def test_species_policy_open_keeps_other_species_as_diversity() -> None:
    request = DatasetRequest(species=["mouse"], species_policy="open", ptm_type="phospho")
    project = DiscoveredProject(
        project_accession="PXDTESTSPECIES",
        project_title="Human phosphoproteomics project",
        species=["Homo sapiens"],
        canonical_species=["human"],
        organism_taxon_id=["9606"],
        acquisition_mode="dda",
        ptm_type="phospho",
        modification_scope="phospho",
        labeling_strategy="label_free",
        project_score=70,
        evidence_completeness=0.8,
    )

    decision = assess_project_validity(project, request)

    assert decision.status != "exclude"
    assert "species_hard_constraint_conflict" not in decision.reasons
    assert "species_open_diversity_evidence" in decision.reasons


def test_species_preference_open_does_not_reward_nonmatching_species_diversity() -> None:
    request = DatasetRequest(species=["human"], species_policy="open", goal="general")

    def _file(name: str, species: str, canonical: str) -> DiscoveredFile:
        return DiscoveredFile(
            project_accession=f"PXD{name}",
            project_title=f"{species} HLA DDA project",
            file_name=f"{name}.mzML",
            download_url=f"https://example.test/{name}.mzML",
            file_type=".mzml",
            file_role="raw_acquisition",
            species=[species],
            canonical_species=[canonical],
            organism_taxon_id=["9606" if canonical == "human" else "10090"],
            acquisition_mode="dda",
            immunopeptide_scope="immunopeptidomics",
            immunopeptide_evidence_terms=["HLA ligandome"],
            immunopeptide_metadata_confidence=0.8,
            labeling_strategy="label_free",
            instrument_families=["orbitrap"],
            fragmentation_methods=["HCD"],
            lc_gradient_minutes=60,
            validity_status="valid",
            task_readiness_status="ready",
            file_score=80,
            trust_score=0.8,
            label_source_status="requires_downstream_generation",
            spectra_requirement_status="satisfied",
            metadata_requirement_status="satisfied",
            ai_ready_target_schema="denovo_train.parquet",
            diversity_tags=[f"species:{canonical}", "instrument:orbitrap", "fragmentation:HCD"],
        )

    scored = annotate_manifest_value_scores(
        DatasetManifest(
            request=request,
            files=[
                _file("human", "Homo sapiens", "human"),
                _file("mouse", "Mus musculus", "mouse"),
            ],
        )
    )
    human_file, mouse_file = scored.files

    assert "species_preference_match" in human_file.task_ai_readiness_reasons
    assert "species_open_diversity_gain" not in mouse_file.task_ai_readiness_reasons
    assert "species_preference_not_matched" in mouse_file.task_ai_readiness_warnings
    assert float(mouse_file.data_value_score or 0) <= float(human_file.data_value_score or 0)


def test_species_policy_include_only_and_exclude_are_hard_constraints() -> None:
    human_project = DiscoveredProject(
        project_accession="PXDTESTSPECIES2",
        project_title="Human project",
        species=["Homo sapiens"],
        canonical_species=["human"],
        organism_taxon_id=["9606"],
        acquisition_mode="dda",
        ptm_type="phospho",
        modification_scope="phospho",
        labeling_strategy="label_free",
        project_score=70,
        evidence_completeness=0.8,
    )

    include_decision = assess_project_validity(
        human_project,
        DatasetRequest(species=["mouse"], species_policy="include_only", ptm_type="phospho"),
    )
    exclude_decision = assess_project_validity(
        human_project,
        DatasetRequest(species=["human"], species_policy="exclude", ptm_type="phospho"),
    )

    assert include_decision.status in {"needs_review", "exclude"}
    assert "species_hard_constraint_conflict" in include_decision.reasons
    assert exclude_decision.status == "exclude"
    assert "species_hard_constraint_conflict" in exclude_decision.reasons


def test_task_readiness_warns_for_isobaric_labeling_without_blocking_fragment_task() -> None:
    request = DatasetRequest(species=["human"], ptm_type="phospho", labeling_strategy="iTRAQ")
    file = DiscoveredFile(
        project_accession="PXDTEST002",
        project_title="iTRAQ DDA",
        file_name="sample.mzML",
        download_url="https://example.test/sample.mzML",
        file_type=".mzml",
        file_role="raw_acquisition",
        species=["human"],
        canonical_species=["human"],
        organism_taxon_id=["9606"],
        acquisition_mode="dda",
        ptm_type="phospho",
        modification_scope="phospho",
        labeling_strategy="iTRAQ",
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
        charge_state_available=True,
        validity_status="valid",
    )
    manifest = DatasetManifest(request=request, files=[file])

    annotated = annotate_manifest_task_readiness(manifest, "fragment_intensity_prediction")
    annotated_file = annotated.files[0]

    assert annotated_file.task_readiness_status in {"ready", "weak_ready"}
    assert "labeling_strategy" not in annotated_file.missing_task_requirements
    assert any(
        "isobaric_labeling_requires_downstream_quality_check:iTRAQ" == reason
        for reason in annotated_file.task_readiness_reasons
    )


def test_immunopeptide_ontology_normalizes_hla_ligandome_context() -> None:
    result = interpret_immunopeptide_metadata(
        "HLA-A*02:01 class I HLA ligandome from W6/32 HLA immunoprecipitation."
    )

    assert is_immunopeptidomics_goal("HLA ligandome immunopeptidomics")
    assert result.scope == "immunopeptidomics"
    assert result.confidence >= 0.5
    assert "class_i" in result.hla_classes
    assert "HLA-A*02:01" in result.hla_alleles
    assert any("W6/32" == term for term in result.enrichment_methods)

    class_ii = interpret_immunopeptide_metadata("MHC class II ligandome from MHC immunoaffinity purification.")
    assert class_ii.hla_classes == ("class_ii",)


def test_query_builder_uses_immunopeptide_terms_without_default_phospho() -> None:
    request = DatasetRequest(
        goal="immunopeptidomics",
        species=["human"],
        ptm_type="unknown_ptm",
        modification_scope="unknown_ptm",
        max_candidate_projects=20,
    )

    queries = build_pride_queries(request)
    joined = " | ".join(queries).casefold()
    immuno_terms = " | ".join(immunopeptide_query_terms()).casefold()

    assert "hla ligandome" in joined or "mhc ligandome" in joined
    assert any(term in joined for term in ["hla", "mhc", "neoantigen"])
    assert "phosphorylation" not in joined
    assert "phosphoproteomics" not in joined
    assert "hla ligandome" in immuno_terms


def test_immunopeptide_project_scoring_and_validity_do_not_require_ptm_evidence() -> None:
    request = DatasetRequest(goal="immunopeptidomics", species=["human"], ptm_type="unknown_ptm")
    project_record = {
        "accession": "PXDIMMU001",
        "title": "Human HLA class I ligandome",
        "projectDescription": "Immunopeptidomics HLA-eluted ligands captured by W6/32 HLA-IP and DDA HCD.",
        "organisms": [{"name": "Homo sapiens"}],
        "experimentTypes": [{"name": "shotgun proteomics"}],
        "keywords": ["HLA ligandome", "immunopeptidomics", "DDA"],
    }

    score = score_project(project_record, request)
    project = DiscoveredProject(
        project_accession="PXDIMMU001",
        project_title="Human HLA class I ligandome",
        species=score.species,
        canonical_species=score.canonical_species,
        organism_taxon_id=score.organism_taxon_id,
        acquisition_mode=score.acquisition_mode,
        ptm_type=score.ptm_type,
        modification_scope=score.modification_scope,
        immunopeptide_scope=score.immunopeptide_scope,
        hla_class=score.hla_class,
        hla_alleles=score.hla_alleles,
        immunopeptide_evidence_terms=score.immunopeptide_evidence_terms,
        immunopeptide_enrichment_methods=score.immunopeptide_enrichment_methods,
        immunopeptide_metadata_confidence=score.immunopeptide_metadata_confidence,
        labeling_strategy=score.labeling_strategy,
        project_score=score.project_score,
        evidence=score.evidence,
    )
    decision = assess_project_validity(project, request)

    assert not score.excluded
    assert score.ptm_type is None
    assert score.immunopeptide_scope == "immunopeptidomics"
    assert "strong_immunopeptide_evidence" in decision.reasons
    assert "weak_ptm_evidence" not in decision.reasons
