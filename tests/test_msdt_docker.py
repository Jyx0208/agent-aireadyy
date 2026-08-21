import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from agent.msdt_converter.docker_runner import DockerMSDTConverterRunner
from agent.models import DdaExecutionPlan, MaterializedTaskBundle
from agent.utils import run_command_streaming


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
        expected_pin_path=task_root / "fragpipe" / "exp" / "WT_5_Lys-c_edited.pin",
        expected_pin_glob=str(task_root / "fragpipe" / "exp" / "WT_5_Lys-c_edited.pin"),
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

    assert cmd[:3] == ["docker", "run", "--rm"]
    assert "--cidfile" in cmd
    assert "--label" in cmd
    assert "-v" in cmd
    assert "-e" in cmd
    assert "TZ=Asia/Shanghai" in cmd
    assert "guomics2017/msdt-converter:v1.3" in cmd
    assert "/workspace/converter_config.docker.json" in cmd


def test_docker_runner_maps_container_runs_path_for_host_docker(monkeypatch, tmp_path: Path):
    container_runs = tmp_path / "container_runs"
    host_runs = tmp_path / "host_runs"
    task_root = container_runs / "task_out"
    task_root.mkdir(parents=True)
    config_path = task_root / "converter_config.docker.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINER_RUNS_DIR", str(container_runs))
    monkeypatch.setenv("AGENT_HOST_RUNS_DIR", str(host_runs))

    runner = DockerMSDTConverterRunner(image="guomics2017/msdt-converter:v1.3")
    cmd = runner.build_command(SimpleNamespace(task_root=task_root, converter_config_path=config_path))

    assert f"{host_runs.resolve() / 'task_out'}:/workspace" in cmd


def test_docker_runner_preserves_windows_host_path_for_nested_docker(monkeypatch, tmp_path: Path):
    container_runs = tmp_path / "container_runs"
    task_root = container_runs / "task_out"
    task_root.mkdir(parents=True)
    config_path = task_root / "converter_config.docker.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINER_RUNS_DIR", str(container_runs))
    monkeypatch.setenv("AGENT_HOST_RUNS_DIR", r"C:\Users\ASUS\Desktop\WestLake\agent-aireadyy_project\agent-aireadyy\runs")

    runner = DockerMSDTConverterRunner(image="guomics2017/msdt-converter:v1.3")
    cmd = runner.build_command(SimpleNamespace(task_root=task_root, converter_config_path=config_path))

    assert r"C:\Users\ASUS\Desktop\WestLake\agent-aireadyy_project\agent-aireadyy\runs\task_out:/workspace" in cmd


def test_docker_runner_can_inherit_agent_container_volumes(monkeypatch, tmp_path: Path):
    task_root = tmp_path / "task_out"
    task_root.mkdir(parents=True)
    config_path = task_root / "converter_config.docker.json"
    config_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AGENT_DOCKER_VOLUMES_FROM", "pride-agent-web")

    runner = DockerMSDTConverterRunner(image="guomics2017/msdt-converter:v1.3")
    cmd = runner.build_command(SimpleNamespace(task_root=task_root, converter_config_path=config_path))

    assert "--volumes-from" in cmd
    assert "pride-agent-web" in cmd
    assert "-w" not in cmd
    assert not any(str(part).endswith(":/workspace") for part in cmd)
    assert config_path.resolve().as_posix() in cmd
    assert "/workspace/converter_config.docker.json" not in cmd


def test_docker_runner_passes_fragpipe_java_heap_to_converter_container(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AGENT_FRAGPIPE_RAM_GB", "6")
    task_root = tmp_path / "task_out"
    task_root.mkdir(parents=True)
    config_path = task_root / "converter_config.docker.json"
    config_path.write_text("{}", encoding="utf-8")

    runner = DockerMSDTConverterRunner(image="guomics2017/msdt-converter:v1.3")
    cmd = runner.build_command(SimpleNamespace(task_root=task_root, converter_config_path=config_path))

    assert "-e" in cmd
    assert "_JAVA_OPTIONS=-Xmx6G" in cmd


def test_run_command_streaming_aborts_on_low_psm_marker_quickly():
    started = time.monotonic()
    command = [
        sys.executable,
        "-c",
        (
            "import sys,time;"
            "print('RT regression using 0 PSMs', flush=True);"
            "time.sleep(30)"
        ),
    ]

    try:
        run_command_streaming(
            command,
            abort_predicate=lambda line, _lines: "low_psm_msbooster"
            if "RT regression using 0 PSMs" in line
            else None,
        )
    except subprocess.CalledProcessError as exc:
        elapsed = time.monotonic() - started
        assert elapsed < 5
        assert "agent_watchdog_abort:low_psm_msbooster" in exc.output
    else:
        raise AssertionError("Expected watchdog abort")


def test_run_command_streaming_polls_for_user_cancellation_without_output():
    started = time.monotonic()
    command = [sys.executable, "-c", "import time; time.sleep(30)"]

    try:
        run_command_streaming(
            command,
            poll_abort_predicate=lambda: "user_cancelled"
            if time.monotonic() - started > 0.2
            else None,
        )
    except subprocess.CalledProcessError as exc:
        elapsed = time.monotonic() - started
        assert elapsed < 5
        assert "agent_watchdog_abort:user_cancelled" in exc.output
    else:
        raise AssertionError("Expected user cancellation abort")


def test_docker_runner_low_psm_abort_can_be_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_MSDT_ABORT_ON_LOW_PSM", "0")
    runner = DockerMSDTConverterRunner()

    assert runner._abort_reason_from_output("RT regression using 0 PSMs", []) is None


def test_docker_runner_low_psm_abort_requires_zero_search_evidence_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_MSDT_ABORT_ON_LOW_PSM", raising=False)
    runner = DockerMSDTConverterRunner()

    lines = [
        "MSBooster v1.1.28",
        "0 unique peptides from 0 PSMs",
        "RT regression using 0 PSMs",
    ]
    assert runner._abort_reason_from_output(lines[-1], lines) == "zero_psm_msbooster"


def test_docker_runner_low_psm_abort_marks_rt_zero_as_partial_when_search_has_psms(monkeypatch):
    monkeypatch.delenv("AGENT_MSDT_ABORT_ON_LOW_PSM", raising=False)
    runner = DockerMSDTConverterRunner()

    lines = [
        "MSBooster v1.1.28",
        "138 unique peptides from 144 PSMs",
        "RT regression using 0 PSMs",
    ]
    assert runner._abort_reason_from_output(lines[-1], lines) == "low_psm_msbooster"


def test_docker_runner_low_psm_abort_strict_mode_keeps_immediate_abort(monkeypatch):
    monkeypatch.setenv("AGENT_MSDT_ABORT_ON_LOW_PSM", "strict")
    runner = DockerMSDTConverterRunner()

    assert runner._abort_reason_from_output("RT regression using 0 PSMs", []) == "low_psm_msbooster"


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
        expected_pin_path=task_root / "fragpipe" / "exp" / "WT_5_Lys-c_edited.pin",
        expected_pin_glob=str(task_root / "fragpipe" / "exp" / "WT_5_Lys-c_edited.pin"),
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
    assert data["generate_msdt"]["mzml"]["fp_pin_path"] == "/workspace/fragpipe/exp/WT_5_Lys-c_edited.pin"
    assert data["generate_msdt"]["mzml"]["fp_output"] == "/workspace/msdt/WT_5_Lys-c_fp_msdt.parquet"


def test_docker_runner_writes_tims_sage_config(tmp_path: Path):
    task_root = tmp_path / "task_out"
    task_root.mkdir(parents=True, exist_ok=True)
    source_path = task_root / "input" / "sample.d"
    source_path.mkdir(parents=True)
    workflow_path = task_root / "workflows" / "workflow.workflow"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text("workflow", encoding="utf-8")
    fasta_path = task_root / "fasta" / "reference.fasta"
    fasta_path.parent.mkdir(parents=True, exist_ok=True)
    fasta_path.write_text(">x\nPEPTIDE\n", encoding="utf-8")
    config_path = task_root / "converter_config.docker.json"

    plan = DdaExecutionPlan(
        task_id="task-tims",
        source_file_name="sample.d",
        source_data_path=source_path,
        raw_data_type="tims",
        fasta_path=fasta_path,
        fasta_selection_mode="defaulted",
        fragpipe_workflow_path=workflow_path,
        manifest_path=task_root / "fragpipe" / "fragpipe-files.fp-manifest",
        converter_config_path=config_path,
        rawspectrum_output_path=task_root / "rawspectrum" / "sample_rawspectrum.parquet",
        fragpipe_workdir=task_root / "fragpipe",
        expected_pin_path=task_root / "fragpipe" / "exp" / "sample.d_edited.pin",
        expected_pin_glob=str(task_root / "fragpipe" / "exp" / "sample.d_edited.pin"),
        output_paths={
            "fp_msdt": task_root / "msdt" / "sample_sage_msdt.parquet",
            "ai_ready": task_root / "ai_ready" / "sample_ai_ready.parquet",
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

    data = json.loads(DockerMSDTConverterRunner().write_container_config(bundle).read_text(encoding="utf-8"))

    assert data["generate_sage_search_result"]["need"] is True
    assert data["generate_sage_search_result"]["workdir"] == "/workspace/sage"
    assert data["generate_msdt"]["tims"]["sage_search_result_path"] == "/workspace/sage/sample_search_result.tsv"
    assert data["generate_msdt"]["tims"]["output"] == "/workspace/msdt/sample_sage_msdt.parquet"


def test_docker_runner_writes_wiff2mzml_config(tmp_path: Path):
    task_root = tmp_path / "task_out"
    task_root.mkdir(parents=True, exist_ok=True)
    source_path = task_root / "assets" / "prepared" / "sample.mzML"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("mzml", encoding="utf-8")
    workflow_path = task_root / "workflows" / "workflow.workflow"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text("workflow", encoding="utf-8")
    fasta_path = task_root / "fasta" / "reference.fasta"
    fasta_path.parent.mkdir(parents=True)
    fasta_path.write_text(">x\nPEPTIDE\n", encoding="utf-8")
    config_path = task_root / "converter_config.docker.json"

    plan = DdaExecutionPlan(
        task_id="task-wiff",
        source_file_name="sample.wiff",
        source_data_path=source_path,
        raw_data_type="wiff2mzml",
        fasta_path=fasta_path,
        fasta_selection_mode="defaulted",
        fragpipe_workflow_path=workflow_path,
        manifest_path=task_root / "fragpipe" / "fragpipe-files.fp-manifest",
        converter_config_path=config_path,
        rawspectrum_output_path=task_root / "rawspectrum" / "sample_rawspectrum.parquet",
        fragpipe_workdir=task_root / "fragpipe",
        expected_pin_path=task_root / "fragpipe" / "exp" / "sample.mzML_edited.pin",
        expected_pin_glob=str(task_root / "fragpipe" / "exp" / "sample.mzML_edited.pin"),
        output_paths={
            "fp_msdt": task_root / "msdt" / "sample_sage_msdt.parquet",
            "ai_ready": task_root / "ai_ready" / "sample_ai_ready.parquet",
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

    data = json.loads(DockerMSDTConverterRunner().write_container_config(bundle).read_text(encoding="utf-8"))

    assert data["generate_rawspectrum"]["data_type"] == "wiff2mzml"
    assert data["generate_sage_search_result"]["need"] is True
    assert data["generate_msdt"]["mzml"]["need_fragpipe"] is False
    assert data["generate_msdt"]["wiff"]["need_wiff"] is True
    assert data["generate_msdt"]["wiff"]["wiff_mzml_path"] == "/workspace/assets/prepared/sample.mzML"
    assert data["generate_msdt"]["wiff"]["sage_search_result_path"] == "/workspace/sage/sample_search_result.tsv"
