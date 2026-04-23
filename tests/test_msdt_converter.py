import json
from pathlib import Path

from agent.decision.dda import plan_dda_execution
from agent.models import AttributeSet, AttributeValue, ProjectResolution
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
