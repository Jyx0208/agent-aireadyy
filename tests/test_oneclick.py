from __future__ import annotations

from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

from agent.oneclick.preflight import run_preflight
from agent.models import ToolchainReport


Usage = namedtuple("Usage", "total used free")


def _toolchain(**overrides):
    defaults = {
        "docker_cli_available": False,
        "docker_daemon_available": False,
        "docker_client_version": None,
        "docker_server_version": None,
        "git_available": True,
        "java_available": True,
        "msconvert_available": False,
        "fragpipe_root": None,
        "msdt_converter_root": None,
        "notes": [],
    }
    defaults.update(overrides)
    return ToolchainReport(**defaults)


def test_preflight_parameter_mode_does_not_block_on_missing_docker_or_msconvert(tmp_path: Path):
    report = run_preflight(
        inputs=["sample.raw"],
        run_mode="parameters",
        repository="pride",
        output_root=tmp_path,
        toolchain_detector=lambda: _toolchain(),
        disk_usage=lambda _path: Usage(total=10 * 1024**3, used=1 * 1024**3, free=9 * 1024**3),
        env={},
    )

    assert report["status"] == "ok"
    assert report["blocking_issues"] == []
    assert any(check["name"] == "toolchain" and check["status"] == "ok" for check in report["checks"])


def test_preflight_full_run_blocks_without_docker_daemon_or_converter(tmp_path: Path):
    report = run_preflight(
        inputs=["sample.raw"],
        run_mode="full",
        repository="pride",
        output_root=tmp_path,
        toolchain_detector=lambda: _toolchain(docker_cli_available=True, docker_daemon_available=False),
        disk_usage=lambda _path: Usage(total=100 * 1024**3, used=1, free=90 * 1024**3),
        env={},
    )

    assert report["status"] == "blocked"
    assert any("Docker daemon" in issue for issue in report["blocking_issues"])
    assert any("msconvert" in issue for issue in report["blocking_issues"])


def test_preflight_iprox_prepare_requires_aspera_credentials(tmp_path: Path):
    report = run_preflight(
        inputs=["IPX000001"],
        run_mode="prepare",
        repository="iprox",
        output_root=tmp_path,
        toolchain_detector=lambda: _toolchain(
            docker_cli_available=True,
            docker_daemon_available=True,
            msconvert_available=True,
        ),
        disk_usage=lambda _path: Usage(total=100 * 1024**3, used=1, free=90 * 1024**3),
        env={},
    )

    assert report["status"] == "blocked"
    assert any("Aspera" in issue for issue in report["blocking_issues"])


def test_preflight_conservative_policy_requires_more_disk_space(tmp_path: Path):
    report = run_preflight(
        inputs=["sample.raw"],
        run_mode="full",
        repository="pride",
        resource_policy="conservative",
        output_root=tmp_path,
        toolchain_detector=lambda: _toolchain(
            docker_cli_available=True,
            docker_daemon_available=True,
            msconvert_available=True,
        ),
        disk_usage=lambda _path: Usage(total=100 * 1024**3, used=95 * 1024**3, free=5 * 1024**3),
        env={},
    )

    assert report["status"] == "blocked"
    assert any("disk" in issue.lower() for issue in report["blocking_issues"])
