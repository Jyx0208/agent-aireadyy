from pathlib import Path

from agent.assets.preparer import DockerPwizConverter


def test_docker_pwiz_converter_builds_expected_command(tmp_path: Path):
    source = tmp_path / "sample.raw"
    target = tmp_path / "prepared" / "sample.mzML"
    source.write_bytes(b"raw")

    converter = DockerPwizConverter(image="chambm/pwiz-skyline-i-agree-to-the-vendor-licenses")
    cmd = converter.build_command(source, target)

    assert cmd[:3] == ["docker", "run", "--rm"]
    assert "chambm/pwiz-skyline-i-agree-to-the-vendor-licenses" in cmd
    assert "--mzML" in cmd
    assert "--filter" in cmd
    assert "peakPicking true 1-" in cmd
    assert "/data/sample.raw" in cmd


def test_docker_pwiz_converter_maps_container_runs_path_for_host_docker(monkeypatch, tmp_path: Path):
    container_runs = tmp_path / "container_runs"
    host_runs = tmp_path / "host_runs"
    source = container_runs / "project" / "assets" / "downloads" / "sample.raw"
    target = container_runs / "project" / "assets" / "prepared" / "sample.mzML"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"raw")

    monkeypatch.setenv("AGENT_CONTAINER_RUNS_DIR", str(container_runs))
    monkeypatch.setenv("AGENT_HOST_RUNS_DIR", str(host_runs))

    converter = DockerPwizConverter(image="pwiz")
    cmd = converter.build_command(source, target)

    assert f"{host_runs.resolve() / 'project' / 'assets' / 'downloads'}:/data" in cmd
    assert f"{host_runs.resolve() / 'project' / 'assets' / 'prepared'}:/out" in cmd
