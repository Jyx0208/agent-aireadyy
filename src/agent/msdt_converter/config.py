from __future__ import annotations

from agent.models import DdaExecutionPlan


def _sage_workdir(plan: DdaExecutionPlan):
    return plan.fragpipe_workdir.parent / "sage"


def _sage_search_result_path(plan: DdaExecutionPlan):
    return _sage_workdir(plan) / f"{plan.source_data_path.stem}_search_result.tsv"


def build_converter_config(plan: DdaExecutionPlan) -> dict:
    is_mzml = plan.raw_data_type == "mzml"
    is_tims = plan.raw_data_type == "tims"
    is_wiff = plan.raw_data_type == "wiff2mzml"
    uses_sage_msdt = is_tims or is_wiff
    return {
        "generate_rawspectrum": {
            "need": True,
            "data_type": plan.raw_data_type,
            "data_path": str(plan.source_data_path),
            "output": str(plan.rawspectrum_output_path),
        },
        "generate_sage_search_result": {
            "need": uses_sage_msdt,
            "workdir": str(_sage_workdir(plan)),
            "fasta": str(plan.fasta_path),
            "data_path": str(plan.source_data_path),
            "config_path": str(_sage_workdir(plan) / "sage_config.json"),
        },
        "generate_fragpipe_search_result": {
            "need": True,
            "workdir": str(plan.fragpipe_workdir),
            "data_path": str(plan.source_data_path),
            "fasta_path": str(plan.fasta_path),
            "workflow_path": str(plan.fragpipe_workflow_path),
            "manifest_path": str(plan.manifest_path),
            "thread_num": plan.thread_num,
        },
        "generate_msdt": {
            "need": True,
            "tims": {
                "need_tims": is_tims,
                "rawspectrum_path": str(plan.rawspectrum_output_path) if is_tims else "",
                "sage_search_result_path": str(_sage_search_result_path(plan)) if is_tims else "",
                "unify_residue": True,
                "output": str(plan.output_paths["fp_msdt"]) if is_tims else "",
            },
            "mzml": {
                "need_mzml": is_mzml,
                "need_sage": False,
                "need_fragpipe": is_mzml,
                "rawspectrum_path": str(plan.rawspectrum_output_path) if is_mzml else "",
                "sage_search_result_path": "",
                "fp_pin_path": str(plan.expected_pin_path) if is_mzml else "",
                "sage_unify_residue": True,
                "fp_unify_residue": True,
                "sage_output": "",
                "fp_output": str(plan.output_paths["fp_msdt"]) if is_mzml else "",
            },
            "wiff": {
                "need_wiff": is_wiff,
                "wiff_mzml_path": str(plan.source_data_path) if is_wiff else "",
                "rawspectrum_path": str(plan.rawspectrum_output_path) if is_wiff else "",
                "sage_search_result_path": str(_sage_search_result_path(plan)) if is_wiff else "",
                "unify_residue": True,
                "output": str(plan.output_paths["fp_msdt"]) if is_wiff else "",
            },
        },
        "convert_2_msdt": {
            "mgf": {
                "need": False,
                "mgf_path": "",
                "output_path": "",
                "field_type_dict": {
                    "TITLE": "string",
                    "PEPMASS": "float",
                    "CHARGE": "int",
                    "RTINSECONDS": "float",
                    "INSTRUMENT": "string",
                },
            }
        },
        "msdt_2_mgf": {
            "need": False,
            "msdt_path": "",
            "output_path": "",
        },
    }
