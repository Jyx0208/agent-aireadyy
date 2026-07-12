from pathlib import Path


def test_deploy_script_pushes_main_and_rebuilds_remote_service():
    script = Path("scripts/deploy.ps1")

    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "git push" in text
    assert "HEAD:$Branch" in text
    assert "ssh" in text
    assert "RemoteCommandLines" in text
    assert "docker compose build" in text
    assert "docker compose up -d" in text
    assert "safe.directory" in text
    assert "timedatectl set-timezone Asia/Shanghai" in text
    assert "container time:" in text
    assert 'id -u' in text
    assert "cmd.exe" in text
    assert "bash -s" in text
    assert "UTF8Encoding" in text
    assert "WriteAllText" in text
    assert "bash -lc" not in text
    assert "QuotedRemoteCommand" not in text
    assert "| ssh" not in text
    assert "/opt/pride-agent" in text
    assert "47.253.243.164" in text
    assert '"root"' in text
    assert "sk-" not in text
    assert "api_key" not in text.lower()


def test_container_and_web_app_use_china_timezone():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    web_app = Path("src/agent/web/app.py").read_text(encoding="utf-8")

    assert "TZ=${TZ:-Asia/Shanghai}" in compose
    assert "ENV TZ=Asia/Shanghai" in dockerfile
    assert "tzdata" in dockerfile
    assert "COPY scripts/ scripts/" in dockerfile
    assert '-e ".[agents-sdk,dev,web]"' in dockerfile
    assert 'os.getenv("TZ", "Asia/Shanghai")' in web_app
    assert "ZoneInfoNotFoundError" in web_app
    assert 'timezone(timedelta(hours=8), "CST")' in web_app
    assert "datetime.now(UTC).strftime" not in web_app
    assert "datetime.now(UTC).isoformat" not in web_app


def test_one_click_deploy_doc_shows_default_command():
    doc = Path("docs/one-click-deploy.md")

    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert ".\\scripts\\deploy.ps1 -CommitMessage" in text
    assert "47.253.243.164" in text
    assert "/opt/pride-agent" in text


def test_deploy_files_expose_dynamic_discovery_limits() -> None:
    env_text = Path(".env.example").read_text(encoding="utf-8")
    compose_text = Path("docker-compose.yml").read_text(encoding="utf-8")
    for name in (
        "AGENT_DISCOVERY_MODE",
        "AGENT_MAX_MODEL_TURNS",
        "AGENT_MAX_TOOL_CALLS",
        "AGENT_MAX_QUERY_UNITS",
        "AGENT_MAX_REPOSITORY_REQUESTS",
        "AGENT_MAX_ELAPSED_SECONDS",
        "AGENT_BUDGET_AGENT_MAX_TURNS",
    ):
        assert name in env_text
        assert name in compose_text


def test_deploy_defaults_to_deepseek_v4_pro() -> None:
    env_text = Path(".env.example").read_text(encoding="utf-8")
    compose_text = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "AGENT_LLM_MODEL=deepseek-v4-pro" in env_text
    assert "AGENT_LLM_MODEL=${AGENT_LLM_MODEL:-deepseek-v4-pro}" in compose_text
