from __future__ import annotations

import json
import zipfile
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_benchmark_excel import (
    ResultSource,
    _fasta,
    _format_digestion_for_benchmark,
    _infer_organism_part,
    _infer_modification,
    _infer_species,
    summarize_source,
    write_xlsx,
)


def _attribute(value):
    return {"value": value, "confidence": 1.0, "source": "test", "evidence_excerpt": ""}


def test_benchmark_species_uses_rat_token_with_underscores():
    attrs = {"species": _attribute("Rattus norvegicus; Homo sapiens")}
    metadata = {"metadata": {"organisms": {"value": ["Rattus norvegicus", "Homo sapiens"]}}}

    assert _infer_species("20191002_EXP1_Evo1_AMV_TMT11prot_Rat_SetC.raw", attrs, metadata) == "Rattus norvegicus (rat)"


def test_benchmark_species_collapses_hye_mixture():
    attrs = {"species": _attribute("Escherichia coli; Homo sapiens; Saccharomyces cerevisiae")}
    metadata = {"metadata": {"organisms": {"value": ["Escherichia coli", "Homo sapiens", "Saccharomyces cerevisiae"]}}}

    assert _infer_species("LFQ_Orbitrap_GP_QC_400_500.raw", attrs, metadata) == "mixed species (HYE)"


def test_benchmark_modification_does_not_treat_msp1ox_as_oxidation():
    attrs = {
        "variable_mods": _attribute(["Oxidation (M)", "Acetyl (K)"]),
        "search_parameter_hints": _attribute({"recommended_workflow_name": "Default.workflow"}),
    }

    assert _infer_modification("ACT-MSP1ox-F2-R3.raw", attrs, {}) == "Acetyl"


def test_benchmark_fasta_prefers_execution_plan_over_llm_hint():
    attrs = {
        "search_parameter_hints": _attribute(
            {
                "recommended_fasta_name": "UniProt_Rattus_norvegicus_Homo_sapiens_2024.fasta",
            }
        )
    }
    decision_trace = {
        "fasta_path": r"benchmark_runs\sample\fasta\uniprot_human_UP000005640.fasta",
    }

    assert _fasta(attrs, decision_trace) == "uniprot_human_UP000005640.fasta"


def test_benchmark_species_uses_human_for_mixed_pride_faims_lfq_file():
    attrs = {"species": _attribute("Rattus norvegicus; Homo sapiens")}
    metadata = {
        "project_accession": "PXD016662",
        "metadata": {"organisms": {"value": ["Rattus norvegicus (rat)", "Homo sapiens (human)"]}},
    }

    assert (
        _infer_species(
            "20190524_EXP1_Evo2_DBJ_LFQprot_SDS_500ng_15000_21min_CV40_01.raw",
            attrs,
            metadata,
        )
        == "Homo sapiens (human)"
    )


def test_benchmark_species_uses_first_sdrf_organism_for_multi_component_qc_sample():
    attrs = {"species": _attribute("Escherichia coli; Homo sapiens; Saccharomyces cerevisiae")}
    metadata = {
        "sdrf_rows": [
            {
                "comment[data file]": "LFQ_Orbitrap_GP_QC_400_500.raw",
                "characteristics[organism]": "Escherichia coli",
            },
            {
                "comment[data file]": "LFQ_Orbitrap_GP_QC_400_500.raw",
                "characteristics[organism]": "Homo sapiens",
            },
        ],
    }

    assert _infer_species("LFQ_Orbitrap_GP_QC_400_500.raw", attrs, metadata) == "Escherichia coli"


def test_benchmark_digestion_uses_standard_labels():
    assert _format_digestion_for_benchmark("Trypsin") == "Trypsion"
    assert _format_digestion_for_benchmark("Arg-C;Trypsin") == "ArgC and Trypsin"


def test_benchmark_organism_part_does_not_infer_cells_from_ecoli_species_token():
    assert _infer_organism_part("LFQ_timsTOFPro_PASEF_Ecoli_01.d", {}) == "unknown"
    metadata = {
        "sdrf_rows": [
            {
                "comment[data file]": "LFQ_timsTOFPro_PASEF_Ecoli_01.d",
                "characteristics[organism part]": "not available",
                "characteristics[cell line]": "DH5 alpha",
            }
        ]
    }
    assert _infer_organism_part("LFQ_timsTOFPro_PASEF_Ecoli_01.d", metadata) == "unknown"


def test_benchmark_suppresses_parameters_for_non_exact_project_match(tmp_path: Path):
    run_dir = tmp_path / "bad_match"
    run_dir.mkdir()
    (run_dir / "project_resolution.json").write_text(
        json.dumps(
            {
                "primary_project": {
                    "project_accession": "PXD071205",
                    "matched_file": "LFQ_Astral_DIA_Optimized.raw",
                    "match_type": "prefix",
                    "match_score": 70,
                },
                "alternative_projects": [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "attributes.json").write_text(
        json.dumps(
            {
                "instrument_name": _attribute("Orbitrap Astral"),
                "species": _attribute("Homo sapiens"),
                "labeling_strategy": _attribute("label-free"),
            }
        ),
        encoding="utf-8",
    )
    row = summarize_source(ResultSource(label="LFQ_timsTOFPro_PASEF_Ecoli_01.d", path=run_dir))

    assert row["Status"] == "failed"
    assert "Non-exact PRIDE project match" in row["Error"]
    assert row["Instrument"] == ""
    assert row["Species"] == ""


def test_benchmark_suppresses_parameters_for_ambiguous_exact_project_match(tmp_path: Path):
    run_dir = tmp_path / "ambiguous_match"
    run_dir.mkdir()
    (run_dir / "project_resolution.json").write_text(
        json.dumps(
            {
                "primary_project": {
                    "project_accession": "PXD021420",
                    "matched_file": "Control_5.raw",
                    "match_type": "exact",
                    "match_score": 100,
                },
                "alternative_projects": [
                    {
                        "project_accession": "PXD031916",
                        "matched_file": "Control_5.raw",
                        "match_type": "exact",
                        "match_score": 100,
                    }
                ],
                "needs_review": True,
                "resolution_reason": "multiple equally strong project matches require manual review",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "attributes.json").write_text(
        json.dumps(
            {
                "instrument_name": _attribute("Q Exactive HF"),
                "species": _attribute("Homo sapiens; Mus musculus"),
                "labeling_strategy": _attribute("label-free"),
            }
        ),
        encoding="utf-8",
    )
    row = summarize_source(ResultSource(label="Control_5.raw", path=run_dir))

    assert row["Status"] == "failed"
    assert "Ambiguous PRIDE project match" in row["Error"]
    assert row["Project"] == "ambiguous"
    assert row["Instrument"] == ""
    assert row["Species"] == ""


def test_benchmark_export_includes_actual_parameter_audit_fields(tmp_path: Path):
    run_dir = tmp_path / "audit_run"
    run_dir.mkdir()
    (run_dir / "project_resolution.json").write_text(
        json.dumps(
            {
                "primary_project": {
                    "repository": "massive",
                    "project_accession": "PXD000900",
                    "native_accession": "MSV000000001",
                    "px_accession": "PXD000900",
                    "matched_file": "HeLa_ArgC-Try_CID_1.raw",
                    "match_type": "exact",
                    "match_score": 100,
                },
                "needs_review": False,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "repository": "massive",
                "project_accession": "PXD000900",
                "native_accession": "MSV000000001",
                "px_accession": "PXD000900",
                "metadata": {"organisms": {"value": ["Homo sapiens"]}},
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "attributes.json").write_text(
        json.dumps(
            {
                "acquisition_mode": _attribute("DDA"),
                "instrument_name": _attribute("LTQ Orbitrap Velos"),
                "species": _attribute("Homo sapiens"),
                "enzyme": _attribute("Arg-C and Trypsin"),
                "labeling_strategy": _attribute("label-free"),
                "fixed_mods": _attribute(["C[57.02]"]),
                "variable_mods": _attribute(["M[15.99]"]),
                "search_parameter_hints": _attribute(
                    {
                        "recommended_workflow_name": "Default.workflow",
                        "recommended_fasta_name": "uniprot_human_UP000005640.fasta",
                        "recommended_fasta_url": "https://example.test/human.fasta",
                        "precursor_tol": "20ppm",
                        "fragment_tol": "0.6Da",
                        "missed_cleavages": 3,
                        "min_peaks": 5,
                        "max_variable_mods": 4,
                        "workflow_parameter_overrides": {
                            "msfragger.search_enzyme_name_2": "Arg-C",
                            "msfragger.search_enzyme_cut_2": "R",
                        },
                    }
                ),
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "decision_trace.json").write_text(
        json.dumps(
            {
                "source_file_name": "HeLa_ArgC-Try_CID_1.raw",
                "source_data_path": str(run_dir / "assets" / "prepared" / "HeLa_ArgC-Try_CID_1.mzML"),
                "raw_data_type": "mzml",
                "fasta_path": str(run_dir / "fasta" / "uniprot_human_UP000005640.fasta"),
                "fasta_download_url": "https://example.test/human.fasta",
                "fasta_selection_mode": "inferred",
                "fragpipe_workflow_path": str(run_dir / "workflows" / "Default.workflow"),
                "converter_config_path": str(run_dir / "converter_config.json"),
                "manifest_path": str(run_dir / "fragpipe" / "fragpipe-files.fp-manifest"),
                "expected_pin_path": str(run_dir / "fragpipe" / "exp" / "HeLa_ArgC-Try_CID_1_edited.pin"),
                "output_paths": {"fp_msdt": str(run_dir / "msdt" / "HeLa_ArgC-Try_CID_1_fp_msdt.parquet")},
                "thread_num": 2,
                "needs_review": False,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "asset_resolution.json").write_text(
        json.dumps(
            {
                "original_file_name": "HeLa_ArgC-Try_CID_1.raw",
                "matched_project_file": "HeLa_ArgC-Try_CID_1.raw",
                "logical_path": "raw/HeLa_ArgC-Try_CID_1.raw",
                "download_url": "https://ftp.pride.ebi.ac.uk/pride/data/archive/2014/04/PXD000900/HeLa_ArgC-Try_CID_1.raw",
                "download_urls": ["https://ftp.pride.ebi.ac.uk/pride/data/archive/2014/04/PXD000900/HeLa_ArgC-Try_CID_1.raw"],
                "transfer_method": "https",
                "resolved_asset_type": "raw",
                "requires_conversion": True,
                "expected_size_bytes": 1800891778,
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "converter_config.json").write_text(
        json.dumps({"generate_fragpipe_search_result": {"workflow_path": str(run_dir / "workflows" / "Default.workflow")}}),
        encoding="utf-8",
    )

    row = summarize_source(ResultSource(label="HeLa_ArgC-Try_CID_1.raw", path=run_dir))

    assert row["Repository"] == "massive"
    assert row["Native accession"] == "MSV000000001"
    assert row["PX accession"] == "PXD000900"
    assert row["Actual input file"] == "HeLa_ArgC-Try_CID_1.raw"
    assert row["Matched repository file"] == "HeLa_ArgC-Try_CID_1.raw"
    assert row["Matched PRIDE file"] == "HeLa_ArgC-Try_CID_1.raw"
    assert row["Logical path"] == "raw/HeLa_ArgC-Try_CID_1.raw"
    assert row["Transfer method"] == "https"
    assert row["Download URL"].startswith("https://ftp.pride.ebi.ac.uk/")
    assert row["PRIDE download URL"].startswith("https://ftp.pride.ebi.ac.uk/")
    assert row["Raw data type"] == "mzml"
    assert row["Actual source data path"].endswith("HeLa_ArgC-Try_CID_1.mzML")
    assert row["Workflow path"].endswith("Default.workflow")
    assert row["FASTA URL"] == "https://example.test/human.fasta"
    assert row["Thread count"] == "2"
    assert row["Precursor tolerance"] == "20ppm"
    assert row["Fragment tolerance"] == "0.6Da"
    assert row["Missed cleavages"] == "3"
    assert "msfragger.search_enzyme_name_2=Arg-C" in row["Workflow parameter overrides"]


def test_benchmark_excel_writes_audit_columns(tmp_path: Path):
    output = tmp_path / "benchmark.xlsx"

    write_xlsx(
        [
            {
                "Input file": "sample.raw",
                "Project": "PXDTEST",
                "Repository": "pride",
                "Actual input file": "sample.raw",
                "Matched repository file": "sample.raw",
                "Download URL": "https://example.test/sample.raw",
                "Workflow path": "runs/sample/workflows/Default.workflow",
                "Converter config": "runs/sample/converter_config.json",
            }
        ],
        output,
    )

    with zipfile.ZipFile(output) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

    assert "Actual input file" in sheet
    assert "Matched repository file" in sheet
    assert "Download URL" in sheet
    assert "Workflow path" in sheet
    assert "Converter config" in sheet
    assert "runs/sample/workflows/Default.workflow" in sheet
