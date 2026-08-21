from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest


_TEST_SESSION_ROOT = Path.cwd() / ".test_tmp" / f"session_{uuid.uuid4().hex}"
_TEST_SESSION_RUNS = _TEST_SESSION_ROOT / "runs"

# Test modules import the web application and queue while pytest is still
# collecting tests, before any fixture can run. Establish a session-scoped
# storage boundary first so import-time SQLite/Huey initialization can never
# touch a developer's real runs/ directory.
os.environ["AGENT_RUNS_DIR"] = str(_TEST_SESSION_RUNS)
os.environ["AGENT_OPERATIONS_DIR"] = str(_TEST_SESSION_RUNS / "_operations")
os.environ["AGENT_OPERATIONS_DB"] = str(
    _TEST_SESSION_RUNS / "_operations" / "operations.sqlite"
)
os.environ["AGENT_QUEUE_DB"] = str(
    _TEST_SESSION_RUNS / "_operations" / "queue.sqlite"
)
os.environ["AGENT_OPERATIONS_ARTIFACTS"] = str(
    _TEST_SESSION_RUNS / "_operations" / "artifacts"
)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session, exitstatus
    queue_module = sys.modules.get("agent.operations.queue")
    if queue_module is not None:
        storage = getattr(getattr(queue_module, "huey", None), "storage", None)
        close = getattr(storage, "close", None)
        if callable(close):
            close()
    shutil.rmtree(_TEST_SESSION_ROOT, ignore_errors=True)


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
    isolated_runs = tmp_path / "runs"
    monkeypatch.setenv("AGENT_LLM_CONFIG_PATH", str(tmp_path / "llm_config.json"))
    monkeypatch.setenv("AGENT_RUNS_DIR", str(isolated_runs))
    monkeypatch.setenv(
        "AGENT_OPERATIONS_DIR",
        str(isolated_runs / "_operations"),
    )
    # agent.web.app is imported during test collection, before this fixture can
    # set environment variables. Patch its resolved storage root as well so a
    # test that creates a task can never write into the checkout's real runs/.
    from agent.web import app as web_app
    from agent.operations.runtime import reset_operations_repository_for_tests

    monkeypatch.setattr(web_app, "_runs_dir", isolated_runs)
    reset_operations_repository_for_tests()
    # Unit tests must not silently fall through to a developer's host LLM
    # credentials. Tests that exercise environment-backed configuration set
    # their own values explicitly after this autouse fixture runs.
    for name in ("AGENT_LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield
    reset_operations_repository_for_tests()
