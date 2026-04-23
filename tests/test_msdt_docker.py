import json
from pathlib import Path

from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner
from agent.models import DdaExecutionPlan, MaterializedTaskBundle


def test_docker_runner_builds_expected_command(tmp_path: Path):
    task_root = tmp_path / "task_out"
    task_root.mkdir(parents=True, exist_ok=True)
    config_path = task_root / "converter_config.docker.json"
    config_path.write_text("{}", encoding="utf-8")
    workflow_path = task_root / "workflows" / "workflow.workflow"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text("workflow", encoding="utf-8")
    fasta_path = task_root / "fasta" / "reference.fasta"
    fasta_path.parent.mkdir(parents=True, exist_ok=True)
    fasta_path.write_text(">x\nPEPTIDE\n", encoding="utf-8")

    plan = DdaExecutionPlan(
        task_id="task-001",
        source_file_name="WT_5_Lys-c.raw",
        source_data_path=task_root / "assets" / "prepared" / "WT_5_Lys-c.mzML",
        raw_data_type="mzml",
        fasta_path=fasta_path,
        fasta_selection_mode="defaulted",
        fragpipe_workflow_path=workflow_path,
        manifest_path=task_root / "fragpipe" / "fragpipe-files.fp-manifest",
        converter_config_path=config_path,
        rawspectrum_output_path=task_root / "rawspectrum" / "WT_5_Lys-c_rawspectrum.parquet",
        fragpipe_workdir=task_root / "fragpipe",
        expected_pin_path=task_root / "fragpipe" / "exp" / "WT_5_Lys-c.mzML_edited.pin",
        expected_pin_glob=str(task_root / "fragpipe" / "exp" / "WT_5_Lys-c.mzML_edited.pin"),
        output_paths={
            "fp_msdt": task_root / "msdt" / "WT_5_Lys-c_fp_msdt.parquet",
            "ai_ready": task_root / "ai_ready" / "WT_5_Lys-c_ai_ready.parquet",
            "run_log": task_root / "logs" / "run.log",
        },
        needs_review=False,
    )
    bundle = MaterializedTaskBundle(
        plan=plan,
        converter_config_path=config_path,
        materialized_workflow_path=workflow_path,
        materialized_fasta_path=fasta_path,
        task_root=task_root,
    )

    runner = DockerMSDTConverterRunner(image="guomics2017/msdt-converter:v1.3")
    cmd = runner.build_command(bundle)

    assert cmd[:4] == ["docker", "run", "--rm", "-v"]
    assert "guomics2017/msdt-converter:v1.3" in cmd
    assert "/workspace/converter_config.docker.json" in cmd


def test_docker_runner_writes_container_compatible_config(tmp_path: Path):
    task_root = tmp_path / "task_out"
    task_root.mkdir(parents=True, exist_ok=True)
    workflow_path = task_root / "workflows" / "workflow.workflow"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text("workflow", encoding="utf-8")
    fasta_path = task_root / "fasta" / "reference.fasta"
    fasta_path.parent.mkdir(parents=True, exist_ok=True)
    fasta_path.write_text(">x\nPEPTIDE\n", encoding="utf-8")
    config_path = task_root / "converter_config.docker.json"

    plan = DdaExecutionPlan(
        task_id="task-001",
        source_file_name="WT_5_Lys-c.raw",
        source_data_path=task_root / "assets" / "prepared" / "WT_5_Lys-c.mzML",
        raw_data_type="mzml",
        fasta_path=fasta_path,
        fasta_selection_mode="defaulted",
        fragpipe_workflow_path=workflow_path,
        manifest_path=task_root / "fragpipe" / "fragpipe-files.fp-manifest",
        converter_config_path=config_path,
        rawspectrum_output_path=task_root / "rawspectrum" / "WT_5_Lys-c_rawspectrum.parquet",
        fragpipe_workdir=task_root / "fragpipe",
        expected_pin_path=task_root / "fragpipe" / "exp" / "WT_5_Lys-c.mzML_edited.pin",
        expected_pin_glob=str(task_root / "fragpipe" / "exp" / "WT_5_Lys-c.mzML_edited.pin"),
        output_paths={
            "fp_msdt": task_root / "msdt" / "WT_5_Lys-c_fp_msdt.parquet",
            "ai_ready": task_root / "ai_ready" / "WT_5_Lys-c_ai_ready.parquet",
            "run_log": task_root / "logs" / "run.log",
        },
        needs_review=False,
    )
    bundle = MaterializedTaskBundle(
        plan=plan,
        converter_config_path=config_path,
        materialized_workflow_path=workflow_path,
        materialized_fasta_path=fasta_path,
        task_root=task_root,
    )

    runner = DockerMSDTConverterRunner(image="guomics2017/msdt-converter:v1.3")
    docker_config_path = runner.write_container_config(bundle)
    data = json.loads(docker_config_path.read_text(encoding="utf-8"))

    assert data["generate_rawspectrum"]["data_path"] == "/workspace/assets/prepared/WT_5_Lys-c.mzML"
    assert data["generate_fragpipe_search_result"]["fasta_path"] == "/workspace/fasta/reference.fasta"
    assert data["generate_fragpipe_search_result"]["workflow_path"] == "/workspace/workflows/workflow.workflow"
    assert data["generate_msdt"]["mzml"]["fp_output"] == "/workspace/msdt/WT_5_Lys-c_fp_msdt.parquet"
