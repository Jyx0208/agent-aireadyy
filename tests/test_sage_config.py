from pathlib import Path

from agent.models import AttributeSet, AttributeValue, DdaExecutionPlan
from agent.msdt_converter.sage_config import build_sage_config


def _plan(tmp_path: Path) -> DdaExecutionPlan:
    return DdaExecutionPlan(
        task_id="task-001",
        source_file_name="sample.raw",
        source_data_path=tmp_path / "sample.mzML",
        raw_data_type="mzml",
        fasta_path=tmp_path / "reference.fasta",
        fasta_selection_mode="defaulted",
        fragpipe_workflow_path=tmp_path / "workflow.workflow",
        manifest_path=tmp_path / "fragpipe" / "fragpipe-files.fp-manifest",
        converter_config_path=tmp_path / "converter_config.json",
        rawspectrum_output_path=tmp_path / "rawspectrum" / "sample_rawspectrum.parquet",
        fragpipe_workdir=tmp_path / "fragpipe",
        expected_pin_path=tmp_path / "fragpipe" / "exp" / "sample.mzML_edited.pin",
        expected_pin_glob=str(tmp_path / "fragpipe" / "exp" / "sample.mzML_edited.pin"),
        output_paths={"fp_msdt": tmp_path / "msdt" / "sample_fp_msdt.parquet"},
    )


def test_build_sage_config_uses_inferred_enzyme_mods_tolerance_and_labeling(tmp_path: Path):
    attributes = AttributeSet(
        acquisition_mode=AttributeValue(value="DDA", confidence=1.0, source="sdrf", evidence_excerpt="DDA"),
        species=AttributeValue(value="Homo sapiens", confidence=1.0, source="sdrf", evidence_excerpt="human"),
        instrument_name=AttributeValue(value="Orbitrap Fusion Lumos", confidence=1.0, source="sdrf", evidence_excerpt="instrument"),
        instrument_family=AttributeValue(value="orbitrap", confidence=1.0, source="rule", evidence_excerpt="instrument"),
        enzyme=AttributeValue(value="Lys-C", confidence=1.0, source="sdrf", evidence_excerpt="Lys-C"),
        labeling_strategy=AttributeValue(value="TMT", confidence=1.0, source="sdrf", evidence_excerpt="TMT"),
        fixed_mods=AttributeValue(value=["Carbamidomethyl (C) 57.02146", "TMT (K) 229.16293"], confidence=1.0, source="sdrf", evidence_excerpt="mods"),
        variable_mods=AttributeValue(value=["Oxidation (M) 15.9949", "Acetyl (Protein N-term) 42.0106"], confidence=1.0, source="sdrf", evidence_excerpt="mods"),
        fractionation_hint=AttributeValue(value=None, confidence=0.0, source="none", evidence_excerpt=""),
        search_parameter_hints=AttributeValue(
            value={"precursor_tol": "10 ppm", "fragment_tol": "0.5 Da", "missed_cleavages": 1},
            confidence=1.0,
            source="sdrf",
            evidence_excerpt="search params",
        ),
    )

    config = build_sage_config(_plan(tmp_path), attributes)

    assert config["database"]["enzyme"]["cleave_at"] == "K"
    assert config["database"]["enzyme"]["missed_cleavages"] == 1
    assert config["database"]["static_mods"]["C"] == 57.02146
    assert config["database"]["static_mods"]["K"] == 229.16293
    assert config["database"]["variable_mods"]["M"] == [15.9949]
    assert config["database"]["variable_mods"]["["] == [42.0106]
    assert config["precursor_tol"] == {"ppm": [-10.0, 10.0]}
    assert config["fragment_tol"] == {"da": [-0.5, 0.5]}
    assert config["quant"] == {"tmt": "Tmt16"}


def test_build_sage_config_uses_llm_extended_search_hints(tmp_path: Path):
    attributes = AttributeSet(
        acquisition_mode=AttributeValue(value="DDA", confidence=1.0, source="llm_confirmed", evidence_excerpt="DDA"),
        species=AttributeValue(value="Homo sapiens", confidence=1.0, source="llm_confirmed", evidence_excerpt="human"),
        instrument_name=AttributeValue(value="Orbitrap Eclipse", confidence=1.0, source="llm_confirmed", evidence_excerpt="instrument"),
        instrument_family=AttributeValue(value="orbitrap", confidence=1.0, source="llm_confirmed", evidence_excerpt="instrument"),
        enzyme=AttributeValue(value="Trypsin", confidence=1.0, source="llm_confirmed", evidence_excerpt="Trypsin"),
        labeling_strategy=AttributeValue(value="TMT", confidence=1.0, source="llm_confirmed", evidence_excerpt="TMT18"),
        fixed_mods=AttributeValue(value=["Carbamidomethyl (C) 57.02146"], confidence=1.0, source="llm_confirmed", evidence_excerpt="mods"),
        variable_mods=AttributeValue(value=["Oxidation (M) 15.9949"], confidence=1.0, source="llm_confirmed", evidence_excerpt="mods"),
        fractionation_hint=AttributeValue(value=None, confidence=0.0, source="none", evidence_excerpt=""),
        search_parameter_hints=AttributeValue(
            value={
                "precursor_charge": [2, 6],
                "isotope_errors": [-1, 3],
                "min_peaks": 12,
                "max_peaks": 200,
                "min_matched_peaks": 5,
                "max_variable_mods": 3,
                "tmt_channel_count": 18,
                "data_family": "mzml",
            },
            confidence=1.0,
            source="llm_confirmed",
            evidence_excerpt="extended hints",
        ),
    )

    config = build_sage_config(_plan(tmp_path), attributes)

    assert config["precursor_charge"] == [2, 6]
    assert config["isotope_errors"] == [-1, 3]
    assert config["min_peaks"] == 12
    assert config["max_peaks"] == 200
    assert config["min_matched_peaks"] == 5
    assert config["database"]["max_variable_mods"] == 3
    assert config["quant"] == {"tmt": "Tmt18"}
