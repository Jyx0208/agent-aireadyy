import json
from pathlib import Path

from agent.decision.dda import plan_dda_execution
from agent.models import AttributeSet, AttributeValue, DdaExecutionPlan, ProjectResolution
from agent.msdt_converter.config import build_converter_config


def _attributes() -> AttributeSet:
    return AttributeSet(
        acquisition_mode=AttributeValue(value="DDA", confidence=1.0, source="sdrf", evidence_excerpt="DDA", conflict_flag=False),
        species=AttributeValue(value="Homo sapiens", confidence=0.9, source="sdrf", evidence_excerpt="human", conflict_flag=False),
        instrument_name=AttributeValue(value="Orbitrap Fusion Lumos", confidence=0.9, source="sdrf", evidence_excerpt="instrument", conflict_flag=False),
        instrument_family=AttributeValue(value="orbitrap", confidence=0.9, source="rule", evidence_excerpt="instrument family", conflict_flag=False),
        enzyme=AttributeValue(value="Trypsin", confidence=0.9, source="sdrf", evidence_excerpt="enzyme", conflict_flag=False),
        labeling_strategy=AttributeValue(value="label-free", confidence=0.8, source="rule", evidence_excerpt="default", conflict_flag=False),
        fixed_mods=AttributeValue(value=["C[57.02]"], confidence=0.7, source="default", evidence_excerpt="mods", conflict_flag=False),
        variable_mods=AttributeValue(value=["M[15.99]"], confidence=0.7, source="default", evidence_excerpt="mods", conflict_flag=False),
        fractionation_hint=AttributeValue(value=None, confidence=0.0, source="none", evidence_excerpt="", conflict_flag=False),
        search_parameter_hints=AttributeValue(value={"precursor_tol": "20ppm"}, confidence=0.6, source="rule", evidence_excerpt="profile", conflict_flag=False),
    )


def test_build_converter_config_matches_expected_sections(tmp_path: Path):
    plan = plan_dda_execution(
        task_id="task-003",
        source_file_name="sample.raw",
        source_data_path="/data/sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=_attributes(),
        output_dir=tmp_path,
    )

    config = build_converter_config(plan)

    assert config["generate_rawspectrum"]["need"] is True
    assert config["generate_fragpipe_search_result"]["need"] is True
    assert config["generate_msdt"]["mzml"]["need_mzml"] is True
    assert config["generate_msdt"]["mzml"]["need_fragpipe"] is True
    assert config["generate_msdt"]["mzml"]["fp_pin_path"].endswith("_edited.pin")

    dumped = tmp_path / "converter_config.json"
    dumped.write_text(json.dumps(config, indent=2), encoding="utf-8")
    loaded = json.loads(dumped.read_text(encoding="utf-8"))
    assert loaded["generate_fragpipe_search_result"]["workflow_path"].endswith(".workflow")


def test_build_converter_config_for_tims_matches_msdt_sage_path_requirements(tmp_path: Path):
    attributes = _attributes()
    attributes.instrument_family = AttributeValue(
        value="tims",
        confidence=0.9,
        source="rule",
        evidence_excerpt="timsTOF",
        conflict_flag=False,
    )
    plan = plan_dda_execution(
        task_id="task-tims",
        source_file_name="sample.d",
        source_data_path=tmp_path / "sample.d",
        project_resolution=ProjectResolution.empty(),
        attributes=attributes,
        output_dir=tmp_path / "out",
    )

    config = build_converter_config(plan)

    assert config["generate_rawspectrum"]["data_type"] == "tims"
    assert config["generate_sage_search_result"]["need"] is True
    assert config["generate_sage_search_result"]["data_path"].endswith("sample.d")
    assert config["generate_msdt"]["tims"]["need_tims"] is True
    sage_result_path = Path(config["generate_msdt"]["tims"]["sage_search_result_path"])
    assert sage_result_path.parent.name == "sage"
    assert sage_result_path.name == "sample_search_result.tsv"
    assert Path(config["generate_msdt"]["tims"]["output"]).name == "sample_sage_msdt.parquet"
    assert config["generate_msdt"]["mzml"]["need_mzml"] is False
    assert config["generate_msdt"]["mzml"]["fp_output"] == ""


def test_build_converter_config_for_wiff2mzml_matches_msdt_wiff_section(tmp_path: Path):
    plan = plan_dda_execution(
        task_id="task-wiff",
        source_file_name="sample.wiff",
        source_data_path=tmp_path / "sample.mzML",
        project_resolution=ProjectResolution.empty(),
        attributes=_attributes(),
        output_dir=tmp_path / "out",
    )

    config = build_converter_config(plan)

    assert config["generate_rawspectrum"]["data_type"] == "wiff2mzml"
    assert config["generate_sage_search_result"]["need"] is True
    assert config["generate_msdt"]["tims"]["need_tims"] is False
    assert config["generate_msdt"]["mzml"]["need_mzml"] is False
    assert config["generate_msdt"]["mzml"]["need_fragpipe"] is False
    assert config["generate_msdt"]["wiff"]["need_wiff"] is True
    assert config["generate_msdt"]["wiff"]["wiff_mzml_path"].endswith("sample.mzML")


def test_build_converter_config_for_mgf_uses_direct_conversion(tmp_path: Path):
    plan = DdaExecutionPlan(
        task_id="task-mgf",
        source_file_name="sample.mgf",
        source_data_path=tmp_path / "sample.mgf",
        raw_data_type="mgf",
        fasta_path=tmp_path / "reference.fasta",
        fasta_selection_mode="defaulted",
        fragpipe_workflow_path=tmp_path / "workflow.workflow",
        manifest_path=tmp_path / "fragpipe" / "fragpipe-files.fp-manifest",
        converter_config_path=tmp_path / "converter_config.json",
        rawspectrum_output_path=tmp_path / "rawspectrum" / "sample_rawspectrum.parquet",
        fragpipe_workdir=tmp_path / "fragpipe",
        expected_pin_path=tmp_path / "fragpipe" / "exp" / "sample_edited.pin",
        expected_pin_glob=str(tmp_path / "fragpipe" / "exp" / "sample_edited.pin"),
        output_paths={"fp_msdt": tmp_path / "msdt" / "sample_mgf_msdt.parquet"},
    )

    config = build_converter_config(plan)

    assert config["generate_rawspectrum"]["need"] is False
    assert config["generate_fragpipe_search_result"]["need"] is False
    assert config["generate_msdt"]["need"] is False
    assert config["convert_2_msdt"]["mgf"]["need"] is True
    assert config["convert_2_msdt"]["mgf"]["mgf_path"].endswith("sample.mgf")
    assert config["convert_2_msdt"]["mgf"]["output_path"].endswith("sample_mgf_msdt.parquet")
