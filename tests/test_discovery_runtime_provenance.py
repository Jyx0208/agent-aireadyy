from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent.control_plane import openai_agents
from agent.control_plane.models import (
    AgentBudget,
    AgentRunRecord,
    DiscoveryAuditIssue,
    DiscoveryQualityAudit,
    DiscoveryRepairAction,
    OpenAIAgentsDiscoveryResult,
    RuntimeProvenance,
)
from agent.control_plane.store import AgentRunStore
from agent.discovery.models import DatasetRequest


def _run(run_id: str) -> AgentRunRecord:
    return AgentRunRecord(run_id=run_id, workflow="discovery", status="running")


def _provenance() -> RuntimeProvenance:
    return RuntimeProvenance(
        git_sha="a" * 40,
        git_dirty=True,
        git_diff_sha256="b" * 64,
        git_fingerprint_complete=True,
        untracked_source_file_count=1,
        python_version="3.test",
        package_versions={"openai-agents": "test", "pydantic": "test"},
        loaded_module_paths={
            "agent.control_plane.openai_agents": "src/agent/control_plane/openai_agents.py",
            "agents": None,
            "pydantic": None,
        },
    )


def _audit(run_id: str, *, status: str = "blocked") -> DiscoveryQualityAudit:
    return DiscoveryQualityAudit(
        run_id=run_id,
        status=status,
        ready_for_selection=status == "ready",
        counts={"candidate_projects": 2, "inspected_projects": 1},
        issues=[
            DiscoveryAuditIssue(
                code="inspection_coverage_incomplete",
                severity="warning",
                summary="One candidate still needs inspection.",
                project_accessions=["PXD000002"],
                evidence_refs=["candidate_pool_manifest_path"],
            )
        ],
        repair_actions=[
            DiscoveryRepairAction(
                action="stop_with_limitations",
                reason="No safe automated repair remains.",
            )
        ],
        limitations=["inspection_coverage_incomplete"],
    )


def test_sdk_audit_persists_and_round_trips_latest_snapshot(tmp_path: Path) -> None:
    run_id = "audit_round_trip"
    store = AgentRunStore(tmp_path / "state.sqlite")
    store.save_run(_run(run_id))
    audit = _audit(run_id)

    class FakeService:
        def __init__(self) -> None:
            self.run_id = run_id
            self.store = store

        def audit_discovery_state(self, *, meter_tool: bool = True) -> DiscoveryQualityAudit:
            assert meter_tool is True
            return audit

    context = SimpleNamespace(
        service=FakeService(),
        raise_if_cancelled=lambda: None,
    )
    payload = json.loads(openai_agents.audit_discovery_state(SimpleNamespace(context=context)))

    stored = store.load_run(run_id)
    assert payload == audit.model_dump(mode="json")
    assert stored is not None
    assert stored.latest_discovery_audit == audit

    legacy_payload = _run("legacy_run").model_dump(mode="json")
    legacy_payload.pop("latest_discovery_audit", None)
    assert AgentRunRecord.model_validate(legacy_payload).latest_discovery_audit is None


def test_runner_summary_report_and_result_expose_latest_audit(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    secret = "runtime-provenance-must-not-contain-this-secret"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    audit_calls: list[bool] = []

    class FakeService:
        def __init__(self, **kwargs: Any) -> None:
            self.run_id = kwargs["run_id"]
            self.store = kwargs["store"]
            self.search_environment = kwargs.get("search_environment")

        def audit_discovery_state(self, *, meter_tool: bool = True) -> DiscoveryQualityAudit:
            audit_calls.append(meter_tool)
            return _audit(self.run_id)

        def auto_select_best_manifest(self) -> AgentRunRecord:
            run = self.store.load_run(self.run_id)
            assert run is not None
            return run

    class FakeAgent:
        @classmethod
        def __class_getitem__(cls, _item: Any) -> type[FakeAgent]:
            return cls

        def __init__(self, **_kwargs: Any) -> None:
            pass

    class FakeRunner:
        @staticmethod
        def run_sync(**_kwargs: Any) -> Any:
            return SimpleNamespace(
                final_output="Audited completion.",
                interruptions=[],
                context_wrapper=SimpleNamespace(usage=None),
            )

    sdk = {
        "Agent": FakeAgent,
        "ModelSettings": lambda **kwargs: kwargs,
        "RunConfig": lambda **kwargs: kwargs,
        "Runner": FakeRunner,
        "ToolExecutionConfig": lambda **kwargs: kwargs,
        "function_tool": lambda function: function,
    }
    monkeypatch.setattr(openai_agents, "_load_agents_sdk", lambda: sdk)
    monkeypatch.setattr(openai_agents, "DiscoveryToolService", FakeService)
    monkeypatch.setattr(openai_agents, "create_role_session", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(openai_agents, "configure_local_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(openai_agents, "PublicRunHooks", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(openai_agents, "_runtime_provenance", _provenance)

    result = openai_agents.run_openai_agents_discovery(
        prompt="Find a bounded test dataset.",
        request=DatasetRequest(repository="pride"),
        output_dir=tmp_path / "output",
        state_db=tmp_path / "state.sqlite",
        session_db=tmp_path / "sessions.sqlite",
        run_id="audit_result",
        model=object(),
        search_environment=object(),  # type: ignore[arg-type]
    )

    expected = _audit(result.run_id)
    summary = json.loads(
        Path(result.files["agents_discovery_summary_json"]).read_text(encoding="utf-8")
    )
    report = Path(result.files["agents_discovery_report_md"]).read_text(encoding="utf-8")
    stored = AgentRunStore(result.state_db).load_run(result.run_id)

    assert audit_calls == [False, False]
    assert result.latest_discovery_audit == expected
    assert result.model_dump(mode="json")["latest_discovery_audit"] == expected.model_dump(
        mode="json"
    )
    assert stored is not None
    assert stored.latest_discovery_audit == expected
    assert stored.runtime_provenance == _provenance()
    assert result.runtime_provenance == stored.runtime_provenance
    assert result.sdk_turn_count == stored.sdk_turn_count == 1
    assert summary["latest_discovery_audit"] == expected.model_dump(mode="json")
    assert "## Discovery Quality Audit" in report
    assert '"schema_version": "discovery-quality-audit/v1"' in report

    provenance = summary["runtime_provenance"]
    assert provenance == _provenance().model_dump(mode="json")
    assert set(provenance["package_versions"]) == {"openai-agents", "pydantic"}
    assert set(provenance["loaded_module_paths"]) == {
        "agent.control_plane.openai_agents",
        "agents",
        "pydantic",
    }
    assert summary["model_usage"]["sdk_turns"] == 1
    assert summary["model_usage"]["remaining_model_turn_budget"] == 49
    assert secret not in json.dumps(provenance, ensure_ascii=False)
    if provenance["git_diff_sha256"] is not None:
        assert re.fullmatch(r"[0-9a-f]{64}", provenance["git_diff_sha256"])


def test_runtime_provenance_streams_diff_and_hashes_untracked_source_content(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    source_dir = repo / "src"
    source_dir.mkdir(parents=True)
    (source_dir / "tracked.py").write_text("BASE = True\n", encoding="utf-8")
    real_run = subprocess.run

    def git(*arguments: str) -> None:
        real_run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            capture_output=True,
        )

    git("init")
    git("add", "src/tracked.py")
    git(
        "-c",
        "user.name=Runtime Provenance Test",
        "-c",
        "user.email=runtime@example.invalid",
        "commit",
        "-m",
        "base",
    )
    secret = "OPENAI_API_KEY=do-not-persist-this-value"
    untracked_source = source_dir / "new_agent.py"
    untracked_source.write_text(secret + "\nVALUE = 1\n", encoding="utf-8")
    irrelevant = repo / "notes.bin"
    irrelevant.write_bytes(b"first")

    captured_commands: list[list[str]] = []

    def bounded_run(command: list[str], **kwargs: Any) -> Any:
        captured_commands.append(command)
        assert "diff" not in command, "git diff must be consumed through the streaming path"
        return real_run(command, **kwargs)

    monkeypatch.setattr(openai_agents.subprocess, "run", bounded_run)
    first = openai_agents._runtime_provenance(repo_start=source_dir)
    serialized = first.model_dump_json()

    assert first.git_dirty is True
    assert first.git_fingerprint_complete is False
    assert (
        first.loaded_module_paths["agent.control_plane.openai_agents"]
        == "<repo-root-mismatch>"
    )
    assert first.untracked_source_file_count == 1
    assert first.git_diff_sha256 is not None
    assert re.fullmatch(r"[0-9a-f]{64}", first.git_diff_sha256)
    assert secret not in serialized
    assert captured_commands

    untracked_source.write_text(secret + "\nVALUE = 2\n", encoding="utf-8")
    second = openai_agents._runtime_provenance(repo_start=source_dir)
    assert second.git_diff_sha256 != first.git_diff_sha256

    irrelevant.write_bytes(b"second")
    third = openai_agents._runtime_provenance(repo_start=source_dir)
    assert third.git_diff_sha256 == second.git_diff_sha256


def test_runtime_provenance_uses_only_repo_relative_loaded_module_paths(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    module_path = repo / "src" / "example" / "module.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("", encoding="utf-8")
    external_path = tmp_path / "site-packages" / "external.py"
    external_path.parent.mkdir(parents=True)
    external_path.write_text("", encoding="utf-8")
    monkeypatch.setitem(
        openai_agents.sys.modules,
        "runtime_provenance_repo_module",
        SimpleNamespace(__file__=str(module_path)),
    )
    monkeypatch.setitem(
        openai_agents.sys.modules,
        "runtime_provenance_external_module",
        SimpleNamespace(__file__=str(external_path)),
    )

    assert openai_agents._loaded_module_path(
        "runtime_provenance_repo_module", repo
    ) == "src/example/module.py"
    assert openai_agents._loaded_module_path(
        "runtime_provenance_external_module", repo
    ) is None

    monkeypatch.setattr(openai_agents, "_git_repository_root", lambda _start: None)
    without_git = openai_agents._runtime_provenance(repo_start=repo)
    assert without_git.git_sha is None
    assert without_git.git_dirty is None
    assert without_git.git_diff_sha256 is None
    assert without_git.git_fingerprint_complete is None


def test_runtime_provenance_fingerprints_loaded_agent_modules_and_repo_mismatches(
    monkeypatch: Any,
) -> None:
    repo = openai_agents._git_repository_root(
        Path(openai_agents.__file__).resolve().parent
    )
    assert repo is not None
    monkeypatch.setattr(openai_agents, "_git_repository_root", lambda _start: repo)
    monkeypatch.setattr(
        openai_agents,
        "_repository_fingerprint",
        lambda _root: (False, "c" * 64, True, 0),
    )
    monkeypatch.setattr(openai_agents, "_git_output", lambda *_args, **_kwargs: b"a" * 40)

    baseline = openai_agents._runtime_provenance(repo_start=repo)
    critical_modules = {
        "agent.control_plane.openai_agents",
        "agent.control_plane.discovery",
        "agent.control_plane.models",
        "agent.control_plane.store",
        "agent.discovery.search_environment",
    }

    assert critical_modules <= set(baseline.loaded_module_paths)
    assert all(
        str(baseline.loaded_module_paths[module_name]).startswith("src/agent/")
        for module_name in critical_modules
    )
    assert baseline.git_fingerprint_complete is True
    assert baseline.git_diff_sha256 is not None

    foreign_path = (
        repo.parent
        / "foreign-checkout"
        / "src"
        / "agent"
        / "control_plane"
        / "models.py"
    )
    models_module = openai_agents.sys.modules["agent.control_plane.models"]
    monkeypatch.setattr(models_module, "__file__", str(foreign_path))

    mismatched = openai_agents._runtime_provenance(repo_start=repo)
    serialized = mismatched.model_dump_json()

    assert (
        mismatched.loaded_module_paths["agent.control_plane.models"]
        == "<repo-root-mismatch>"
    )
    assert mismatched.git_fingerprint_complete is False
    assert mismatched.git_diff_sha256 is not None
    assert mismatched.git_diff_sha256 != baseline.git_diff_sha256
    assert str(foreign_path) not in serialized
    assert str(foreign_path.parent) not in serialized


def test_runtime_provenance_typed_field_round_trips_run_and_result(tmp_path: Path) -> None:
    provenance = _provenance()
    store = AgentRunStore(tmp_path / "state.sqlite")
    stored = store.save_run(
        _run("provenance_round_trip").model_copy(
            update={"runtime_provenance": provenance, "sdk_turn_count": 3}
        )
    )
    loaded = store.load_run(stored.run_id)

    assert loaded is not None
    assert loaded.runtime_provenance == provenance
    assert loaded.sdk_turn_count == 3
    assert loaded.remaining_model_turn_budget() == 47

    result = OpenAIAgentsDiscoveryResult(
        status="completed",
        run_id=loaded.run_id,
        output_dir="output",
        state_db=str(store.path),
        runtime_provenance=loaded.runtime_provenance,
        sdk_turn_count=loaded.sdk_turn_count,
    )
    assert result.runtime_provenance == provenance
    assert result.sdk_turn_count == 3


def test_repair_runner_uses_remaining_sdk_turns_without_provider_usage(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    class FakeService:
        def __init__(self, **kwargs: Any) -> None:
            self.run_id = kwargs["run_id"]
            self.store = kwargs["store"]
            self.search_environment = kwargs.get("search_environment")

        def audit_discovery_state(self, *, meter_tool: bool = True) -> DiscoveryQualityAudit:
            return _audit(self.run_id, status="repair_required")

        def auto_select_best_manifest(self) -> AgentRunRecord:
            run = self.store.load_run(self.run_id)
            assert run is not None
            return run

    class FakeAgent:
        @classmethod
        def __class_getitem__(cls, _item: Any) -> type[FakeAgent]:
            return cls

        def __init__(self, **_kwargs: Any) -> None:
            pass

    class FakeRunner:
        max_turns: list[int] = []
        live_turn_counts: list[int] = []

        @classmethod
        def run_sync(cls, **kwargs: Any) -> Any:
            cls.max_turns.append(int(kwargs["max_turns"]))
            turn_count = 3 if len(cls.max_turns) == 1 else 1
            for _ in range(turn_count):
                kwargs["hooks"].sink("sdk_llm_started", {})
            live_run = kwargs["context"].service.store.load_run(
                kwargs["context"].service.run_id
            )
            assert live_run is not None
            cls.live_turn_counts.append(live_run.sdk_turn_count)
            return SimpleNamespace(
                final_output="Runner completed.",
                interruptions=[],
                context_wrapper=SimpleNamespace(usage=None),
                raw_responses=None,
            )

    class FakeHooks:
        def __init__(self, sink: Any, **_kwargs: Any) -> None:
            self.sink = sink

    sdk = {
        "Agent": FakeAgent,
        "ModelSettings": lambda **kwargs: kwargs,
        "RunConfig": lambda **kwargs: kwargs,
        "Runner": FakeRunner,
        "ToolExecutionConfig": lambda **kwargs: kwargs,
        "function_tool": lambda function: function,
    }
    monkeypatch.setattr(openai_agents, "_load_agents_sdk", lambda: sdk)
    monkeypatch.setattr(openai_agents, "DiscoveryToolService", FakeService)
    monkeypatch.setattr(openai_agents, "create_role_session", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(openai_agents, "configure_local_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(openai_agents, "PublicRunHooks", FakeHooks)
    monkeypatch.setattr(openai_agents, "_runtime_provenance", _provenance)

    result = openai_agents.run_openai_agents_discovery(
        prompt="Exercise the bounded repair path.",
        request=DatasetRequest(repository="pride"),
        output_dir=tmp_path / "output",
        state_db=tmp_path / "state.sqlite",
        session_db=tmp_path / "sessions.sqlite",
        run_id="bounded_repair",
        budget=AgentBudget(max_turns=5),
        model=object(),
        search_environment=object(),  # type: ignore[arg-type]
    )

    store = AgentRunStore(result.state_db)
    run = store.load_run(result.run_id)
    repair_event = next(
        event
        for event in store.list_events(result.run_id)
        if event.event_type == "discovery_quality_repair_started"
    )
    stopped_event = next(
        event
        for event in store.list_events(result.run_id)
        if event.event_type == "discovery_quality_repair_stopped"
    )
    summary = json.loads(
        Path(result.files["agents_discovery_summary_json"]).read_text(encoding="utf-8")
    )

    assert FakeRunner.max_turns == [5, 2]
    assert FakeRunner.live_turn_counts == [3, 4]
    assert run is not None
    assert run.model_requests == 0
    assert run.sdk_turn_count == 4
    assert run.remaining_model_turn_budget() == 1
    assert run.stop_reason == "model_turn_budget_insufficient"
    assert run.latest_discovery_audit is not None
    assert run.latest_discovery_audit.status == "blocked"
    assert [
        action.action for action in run.latest_discovery_audit.repair_actions
    ] == ["stop_with_limitations"]
    assert result.sdk_turn_count == 4
    assert repair_event.payload["remaining_turns"] == 2
    assert repair_event.payload["sdk_turn_count"] == 3
    assert stopped_event.payload["reason"] == "model_turn_budget_insufficient"
    assert stopped_event.payload["remaining_turns"] == 1
    assert summary["stop_reason"] == "model_turn_budget_insufficient"
    assert summary["latest_discovery_audit"]["status"] == "blocked"
    assert summary["model_usage"]["sdk_turns"] == 4
    assert summary["model_usage"]["remaining_model_turn_budget"] == 1


def test_runner_stops_with_matching_audit_when_one_turn_cannot_fund_repair(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    auto_select_calls = 0

    class FakeService:
        def __init__(self, **kwargs: Any) -> None:
            self.run_id = kwargs["run_id"]
            self.store = kwargs["store"]
            self.search_environment = kwargs.get("search_environment")

        def audit_discovery_state(self, *, meter_tool: bool = True) -> DiscoveryQualityAudit:
            return _audit(self.run_id, status="repair_required").model_copy(
                update={
                    "repair_actions": [
                        DiscoveryRepairAction(
                            action="rescore_projects",
                            reason="One inspected project still needs a quality score.",
                            project_accessions=["PXD000002"],
                        )
                    ]
                }
            )

        def auto_select_best_manifest(self) -> AgentRunRecord:
            nonlocal auto_select_calls
            auto_select_calls += 1
            run = self.store.load_run(self.run_id)
            assert run is not None
            return run

    class FakeAgent:
        @classmethod
        def __class_getitem__(cls, _item: Any) -> type[FakeAgent]:
            return cls

        def __init__(self, **_kwargs: Any) -> None:
            pass

    class FakeRunner:
        max_turns: list[int] = []

        @classmethod
        def run_sync(cls, **kwargs: Any) -> Any:
            cls.max_turns.append(int(kwargs["max_turns"]))
            for _ in range(4):
                kwargs["hooks"].sink("sdk_llm_started", {})
            return SimpleNamespace(
                final_output="Initial run stopped before repairing quality.",
                interruptions=[],
                context_wrapper=SimpleNamespace(usage=None),
                raw_responses=None,
            )

    class FakeHooks:
        def __init__(self, sink: Any, **_kwargs: Any) -> None:
            self.sink = sink

    sdk = {
        "Agent": FakeAgent,
        "ModelSettings": lambda **kwargs: kwargs,
        "RunConfig": lambda **kwargs: kwargs,
        "Runner": FakeRunner,
        "ToolExecutionConfig": lambda **kwargs: kwargs,
        "function_tool": lambda function: function,
    }
    monkeypatch.setattr(openai_agents, "_load_agents_sdk", lambda: sdk)
    monkeypatch.setattr(openai_agents, "DiscoveryToolService", FakeService)
    monkeypatch.setattr(openai_agents, "create_role_session", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(openai_agents, "configure_local_trace", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(openai_agents, "PublicRunHooks", FakeHooks)
    monkeypatch.setattr(openai_agents, "_runtime_provenance", _provenance)

    result = openai_agents.run_openai_agents_discovery(
        prompt="Expose an unfunded quality repair.",
        request=DatasetRequest(repository="pride"),
        output_dir=tmp_path / "output",
        state_db=tmp_path / "state.sqlite",
        session_db=tmp_path / "sessions.sqlite",
        run_id="one_turn_repair_stop",
        budget=AgentBudget(max_turns=5),
        model=object(),
        search_environment=object(),  # type: ignore[arg-type]
    )

    store = AgentRunStore(result.state_db)
    run = store.load_run(result.run_id)
    events = store.list_events(result.run_id)
    stopped = next(
        event
        for event in events
        if event.event_type == "discovery_quality_repair_stopped"
    )
    summary = json.loads(
        Path(result.files["agents_discovery_summary_json"]).read_text(encoding="utf-8")
    )

    assert FakeRunner.max_turns == [5]
    assert auto_select_calls == 0
    assert not any(
        event.event_type == "discovery_quality_repair_started" for event in events
    )
    assert stopped.payload["reason"] == "model_turn_budget_insufficient"
    assert stopped.payload["remaining_turns"] == 1
    assert stopped.payload["minimum_repair_turns"] == 2
    assert run is not None
    assert run.status == result.status == "blocked"
    assert run.stop_reason == "model_turn_budget_insufficient"
    assert run.blockers == ["model_turn_budget_insufficient"]
    assert result.blockers == run.blockers
    assert run.remaining_model_turn_budget() == 1
    assert run.latest_discovery_audit is not None
    assert run.latest_discovery_audit.status == "blocked"
    assert [
        action.action for action in run.latest_discovery_audit.repair_actions
    ] == ["stop_with_limitations"]
    assert "model_turn_budget_insufficient" in run.latest_discovery_audit.limitations
    assert result.latest_discovery_audit == run.latest_discovery_audit
    assert summary["stop_reason"] == "model_turn_budget_insufficient"
    assert summary["latest_discovery_audit"] == run.latest_discovery_audit.model_dump(
        mode="json"
    )


def test_result_model_keeps_legacy_default_and_audit_projection() -> None:
    legacy = OpenAIAgentsDiscoveryResult(
        status="completed",
        run_id="legacy_result",
        output_dir="output",
        state_db="state.sqlite",
    )
    assert legacy.latest_discovery_audit is None
    assert legacy.runtime_provenance is None
    assert legacy.sdk_turn_count == 0

    audit = _audit("projected_result")
    projected = legacy.model_copy(
        update={
            "run_id": audit.run_id,
            "latest_discovery_audit": audit,
            "runtime_provenance": _provenance(),
            "sdk_turn_count": 2,
        }
    )
    assert projected.latest_discovery_audit == audit
    assert projected.runtime_provenance == _provenance()
    assert projected.sdk_turn_count == 2


def test_selected_manifest_stop_reason_preserves_quality_limitations() -> None:
    clean = _run("clean-selection")
    limited_audit = _audit("limited-selection", status="ready").model_copy(
        update={
            "ready_for_selection": True,
            "limitations": [
                "portfolio_maximize_incomplete",
                "hard_repository_request_limit",
            ],
        }
    )
    limited = _run("limited-selection").model_copy(
        update={
            "latest_discovery_audit": limited_audit,
            "search_stop_reason": "hard_repository_request_limit",
        }
    )

    assert openai_agents._selected_manifest_stop_reason(clean) == "manifest_selected"
    assert (
        openai_agents._selected_manifest_stop_reason(limited)
        == "selected_with_limitations"
    )
