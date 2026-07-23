from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path() -> Path:
    root = Path.cwd() / ".test_tmp"
    root.mkdir(exist_ok=True)
    path = root / f"tmp_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolate_saved_llm_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_LLM_CONFIG_PATH", str(tmp_path / "llm_config.json"))
    # Unit tests must not silently fall through to a developer's host LLM
    # credentials. Tests that exercise environment-backed configuration set
    # their own values explicitly after this autouse fixture runs.
    for name in ("AGENT_LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
