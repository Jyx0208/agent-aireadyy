from pathlib import Path


def test_deploy_script_pushes_main_and_rebuilds_remote_service():
    script = Path("scripts/deploy.ps1")

    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "git push" in text
    assert "HEAD:$Branch" in text
    assert "ssh" in text
    assert "$SUDO docker compose build" in text
    assert "$SUDO docker compose up -d" in text
    assert "safe.directory" in text
    assert 'id -u' in text
    assert 'Normalize remote script newlines' in text
    assert '`r`n' in text
    assert "/opt/pride-agent" in text
    assert "47.253.243.164" in text
    assert '"root"' in text
    assert "sk-" not in text
    assert "api_key" not in text.lower()


def test_one_click_deploy_doc_shows_default_command():
    doc = Path("docs/one-click-deploy.md")

    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert ".\\scripts\\deploy.ps1 -CommitMessage" in text
    assert "47.253.243.164" in text
    assert "/opt/pride-agent" in text
