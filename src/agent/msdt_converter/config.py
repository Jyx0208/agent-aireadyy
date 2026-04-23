from __future__ import annotations

from agent.models import DdaExecutionPlan


def build_converter_config(plan: DdaExecutionPlan) -> dict:
    is_mzml = plan.raw_data_type == "mzml"
    return {
        "generate_rawspectrum": {
            "need": True,
            "data_type": plan.raw_data_type,
            "data_path": str(plan.source_data_path),
            "output": str(plan.rawspectrum_output_path),
        },
        "generate_sage_search_result": {
            "need": False,
            "workdir": str(plan.fragpipe_workdir.parent / "sage"),
            "fasta": str(plan.fasta_path),
            "data_path": str(plan.source_data_path),
            "config_path": str(plan.fragpipe_workdir.parent / "sage" / "sage_config.json"),
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
                "need_tims": not is_mzml,
                "rawspectrum_path": "" if is_mzml else str(plan.rawspectrum_output_path),
                "sage_search_result_path": "",
                "unify_residue": True,
                "output": "" if is_mzml else str(plan.output_paths["fp_msdt"]),
            },
            "mzml": {
                "need_mzml": is_mzml,
                "need_sage": False,
                "need_fragpipe": True,
                "rawspectrum_path": str(plan.rawspectrum_output_path) if is_mzml else "",
                "sage_search_result_path": "",
                "fp_pin_path": str(plan.expected_pin_path) if is_mzml else "",
                "sage_unify_residue": True,
                "fp_unify_residue": True,
                "sage_output": "",
                "fp_output": str(plan.output_paths["fp_msdt"]) if is_mzml else "",
            },
            "wiff": {
                "need_wiff": False,
                "wiff_mzml_path": "",
                "rawspectrum_path": "",
                "sage_search_result_path": "",
                "unify_residue": True,
                "output": "",
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
