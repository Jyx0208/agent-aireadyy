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
