from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent.cli import app
from agent.control_plane.discovery import DiscoveryToolService
from agent.control_plane.models import AgentBudget, AgentRunRecord, OpenAIAgentsDiscoveryResult
from agent.control_plane.policy import evaluate_tool_policy
from agent.control_plane.store import AgentRunStore, tool_idempotency_key
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject


def _run(run_id: str = "run_001") -> AgentRunRecord:
    return AgentRunRecord(
        run_id=run_id,
        workflow="discovery",
        budget=AgentBudget(
            max_turns=8,
            max_tool_calls=5,
            max_discovery_rounds=3,
            max_expensive_actions=1,
        ),
    )


def test_agent_run_store_round_trips_events_and_idempotent_tool_calls(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "agent_control.sqlite")
    run = store.save_run(_run())
    store.append_event(run.run_id, "run_started", {"workflow": "discovery"})

    arguments = {"queries": ["human phosphoproteomics"]}
    first, claimed = store.claim_tool_call(
        run_id=run.run_id,
        tool_name="search_repository_datasets",
        arguments=arguments,
    )
    assert claimed is True
    assert first.idempotency_key == tool_idempotency_key(
        run.run_id,
        "search_repository_datasets",
        arguments,
    )
    store.complete_tool_call(first.idempotency_key, {"selected_files": 2})

    cached, claimed_again = store.claim_tool_call(
        run_id=run.run_id,
        tool_name="search_repository_datasets",
        arguments=arguments,
    )
    assert claimed_again is False
    assert cached.status == "completed"
    assert cached.output == {"selected_files": 2}
    assert store.load_run(run.run_id) is not None
    assert [event.event_type for event in store.list_events(run.run_id)] == ["run_started"]


def test_control_plane_policy_separates_safe_expensive_biological_and_forbidden_tools() -> None:
    run = _run()

    assert evaluate_tool_policy("search_repository_datasets", run).outcome == "allow"
    assert evaluate_tool_policy("select_discovery_manifest", run).outcome == "allow"
    assert evaluate_tool_policy("run_full_workflow", run).outcome == "approval_required"
    assert evaluate_tool_policy("change_species", run).outcome == "approval_required"
    assert evaluate_tool_policy("run_shell_command", run).outcome == "deny"
    assert evaluate_tool_policy("invented_tool", run).outcome == "deny"


def test_discovery_tool_service_reuses_identical_query_result(tmp_path: Path) -> None:
    request = DatasetRequest(
        repository="pride",
        species=["human"],
        max_projects=2,
        max_files=4,
        max_candidate_projects=10,
    )
    calls: list[list[str]] = []

    def fake_discovery(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        calls.append(list(queries or []))
        project = DiscoveredProject(
            project_accession="PXDTEST001",
            project_title="Human phosphoproteomics",
        )
        file = DiscoveredFile(
            project_accession=project.project_accession,
            project_title=project.project_title,
            file_name="sample.raw",
            file_type=".raw",
            file_role="raw_acquisition",
            acquisition_mode="dda",
            species=["human"],
            validity_status="valid",
            evidence_level="mixed",
        )
        return DatasetManifest(
            request=request,
            projects=[project],
            files=[file],
            summary={
                "selected_projects": 1,
                "selected_files": 1,
                "candidate_projects_seen": 1,
                "validity_status_counts": {"valid": 1},
                "evidence_level_distribution": {"mixed": 1},
                "instrument_family_distribution": {"orbitrap": 1},
                "unknown_counts": {},
            },
        )

    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(_run("discovery_001").model_copy(update={"status": "running"}))
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=request,
        output_dir=tmp_path / "output",
        store=store,
        task_type="rt_prediction",
        discovery_func=fake_discovery,
    )

    first = service.search_repository_datasets(["human phosphoproteomics", "human phosphoproteomics"])
    second = service.search_repository_datasets(["human   phosphoproteomics"])

    assert first.status == "completed"
    assert first.selected_files == 1
    assert second == first
    assert calls == [["human phosphoproteomics"]]
    stored = store.load_run(run.run_id)
    assert stored is not None
    assert stored.discovery_round_count == 1
    assert stored.tool_call_count == 1
    assert Path(stored.current_manifest_path or "").exists()
    assert "tool_result_reused" in [event.event_type for event in store.list_events(run.run_id)]
    selected = service.auto_select_best_manifest()
    assert selected.selected_round_index == 0
    assert "manifest_auto_selected" in [event.event_type for event in store.list_events(run.run_id)]


def test_discovery_tool_service_retains_nonempty_manifest_after_empty_followup(tmp_path: Path) -> None:
    request = DatasetRequest(repository="pride", max_projects=2, max_files=4)
    calls = 0

    def discovery_with_empty_followup(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        nonlocal calls
        calls += 1
        if calls == 2:
            return DatasetManifest(request=request, summary={"selected_projects": 0, "selected_files": 0})
        project = DiscoveredProject(project_accession="PXD_RETAIN", project_title="usable project")
        file = DiscoveredFile(
            project_accession=project.project_accession,
            project_title=project.project_title,
            file_name="retain.raw",
            file_type=".raw",
            file_role="raw_acquisition",
            acquisition_mode="dda",
            species=["human"],
            validity_status="valid",
            evidence_level="file",
        )
        return DatasetManifest(
            request=request,
            projects=[project],
            files=[file],
            summary={"selected_projects": 1, "selected_files": 1},
        )

    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(_run("retain_001").model_copy(update={"status": "running"}))
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=request,
        output_dir=tmp_path / "output",
        store=store,
        discovery_func=discovery_with_empty_followup,
    )

    first = service.search_repository_datasets(["human dda"])
    second = service.search_repository_datasets(["human dda HCD"])

    assert first.selected_files == 1
    assert first.pooled_selected_files == 1
    assert second.selected_files == 0
    assert second.pooled_selected_files == 1

    stored = store.load_run(run.run_id)
    assert stored is not None
    assert stored.blockers == []
    assert stored.current_manifest_path is not None
    selected_manifest = DatasetManifest.model_validate(
        json.loads(Path(stored.current_manifest_path).read_text(encoding="utf-8"))
    )
    assert selected_manifest.summary["selected_files"] == 1
    events = store.list_events(run.run_id)
    assert events[-1].payload["selected_manifest_retained"] is True

    selection = service.select_discovery_manifest(0, "The merged pool retains the valid first-round file.")
    assert selection["status"] == "completed"
    assert selection["round_index"] == 0
    stored = store.load_run(run.run_id)
    assert stored is not None
    assert stored.selected_round_index == 0
    assert stored.selection_rationale == "The merged pool retains the valid first-round file."
    blocked = service.search_repository_datasets(["another query"])
    assert blocked.blockers == ["manifest_already_selected"]


def test_discovery_tool_service_merges_distinct_round_candidates(tmp_path: Path) -> None:
    request = DatasetRequest(repository="pride", max_projects=2, max_files=4)
    calls = 0

    def discovery_by_round(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        nonlocal calls
        calls += 1
        accession = f"PXD_POOL_{calls}"
        project = DiscoveredProject(project_accession=accession, project_title=f"Pool round {calls}")
        file = DiscoveredFile(
            project_accession=accession,
            project_title=project.project_title,
            file_name=f"round_{calls}.raw",
            file_type=".raw",
            file_role="raw_acquisition",
            acquisition_mode="dda",
            species=["human"],
            validity_status="valid" if calls == 1 else "weak_keep",
            evidence_level="file",
            trust_score=0.9 - calls * 0.1,
        )
        return DatasetManifest(
            request=request,
            projects=[project],
            files=[file],
            summary={"selected_projects": 1, "selected_files": 1},
        )

    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(_run("pool_001").model_copy(update={"status": "running"}))
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=request,
        output_dir=tmp_path / "output",
        store=store,
        discovery_func=discovery_by_round,
    )

    assert service.search_repository_datasets(["round one"]).pooled_selected_files == 1
    second = service.search_repository_datasets(["round two"])
    assert second.selected_files == 1
    assert second.pooled_selected_projects == 2
    assert second.pooled_selected_files == 2
    stored = store.load_run(run.run_id)
    assert stored is not None
    assert stored.candidate_pool_manifest_path is not None
    pool = DatasetManifest.model_validate_json(
        Path(stored.candidate_pool_manifest_path).read_text(encoding="utf-8")
    )
    assert {file.file_name for file in pool.files} == {"round_1.raw", "round_2.raw"}
    assert pool.summary["candidate_pool"]["merged_rounds"] == 2


def test_discovery_tool_service_enforces_round_budget(tmp_path: Path) -> None:
    request = DatasetRequest(repository="pride")
    calls = 0

    def empty_discovery(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        nonlocal calls
        calls += 1
        return DatasetManifest(request=request, summary={"selected_projects": 0, "selected_files": 0})

    store = AgentRunStore(tmp_path / "state.sqlite")
    run = AgentRunRecord(
        run_id="budget_001",
        workflow="discovery",
        status="running",
        budget=AgentBudget(max_turns=4, max_tool_calls=3, max_discovery_rounds=1),
    )
    store.save_run(run)
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=request,
        output_dir=tmp_path / "output",
        store=store,
        discovery_func=empty_discovery,
    )

    assert service.search_repository_datasets(["proteomics"]).status == "blocked"
    blocked = service.search_repository_datasets(["proteomics HCD"])

    assert blocked.status == "blocked"
    assert blocked.blockers == ["discovery_round_budget_exhausted"]
    assert calls == 1
    stored = store.load_run(run.run_id)
    assert stored is not None
    assert stored.blockers == ["no_selected_files"]


def test_discovery_state_tool_is_budgeted_and_audited(tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = AgentRunRecord(
        run_id="state_budget_001",
        workflow="discovery",
        status="running",
        budget=AgentBudget(max_turns=3, max_tool_calls=1, max_discovery_rounds=1),
    )
    store.save_run(run)
    service = DiscoveryToolService(
        run_id=run.run_id,
        request=DatasetRequest(repository="pride"),
        output_dir=tmp_path / "output",
        store=store,
    )

    first = service.get_discovery_state()
    second = service.get_discovery_state()

    assert first["tool_call_count"] == 1
    assert first["policy"]["outcome"] == "allow"
    assert second["tool_call_count"] == 1
    assert second["policy"]["outcome"] == "deny"
    assert second["policy"]["reason"] == "tool_call_budget_exhausted"
    assert [event.event_type for event in store.list_events(run.run_id)] == [
        "tool_completed",
        "tool_denied",
    ]


def test_openai_agents_function_tools_expose_strict_bounded_schemas() -> None:
    from agent.control_plane import openai_agents

    sdk = openai_agents._load_agents_sdk()
    search_tool = sdk["function_tool"](openai_agents.search_repository_datasets)
    state_tool = sdk["function_tool"](openai_agents.get_discovery_state)
    selection_tool = sdk["function_tool"](openai_agents.select_discovery_manifest)

    assert search_tool.name == "search_repository_datasets"
    assert search_tool.params_json_schema["required"] == ["queries"]
    assert search_tool.params_json_schema["additionalProperties"] is False
    assert state_tool.name == "get_discovery_state"
    assert state_tool.params_json_schema["properties"] == {}
    assert selection_tool.name == "select_discovery_manifest"
    assert selection_tool.params_json_schema["required"] == ["round_index", "rationale"]
    assert selection_tool.params_json_schema["additionalProperties"] is False


def test_openai_agents_model_configuration_accepts_transient_web_config(monkeypatch) -> None:
    from agent.control_plane import openai_agents

    monkeypatch.delenv("AGENT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert openai_agents._model_configuration(
        {
            "api_key": "temporary-web-key",
            "base_url": "https://example.test/v1",
            "model": "test-model",
        }
    ) == ("temporary-web-key", "https://example.test/v1", "test-model")


def test_openai_agents_marks_candidate_only_manifests_for_review(tmp_path: Path) -> None:
    from agent.control_plane import openai_agents

    manifest = DatasetManifest(
        request=DatasetRequest(repository="pride"),
        projects=[DiscoveredProject(project_accession="PXD_REVIEW")],
        files=[
            DiscoveredFile(
                project_accession="PXD_REVIEW",
                file_name="review.raw",
                file_type=".raw",
                validity_status="needs_review",
                needs_review=True,
            )
        ],
        summary={"selected_projects": 1, "selected_files": 1},
    )
    path = tmp_path / "dataset_manifest.json"
    path.write_text(manifest.model_dump_json(), encoding="utf-8")

    assert openai_agents._manifest_completion_status(str(path)) == "completed_with_review"


def test_openai_agents_runner_executes_real_function_tool_loop(tmp_path: Path) -> None:
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    from agent.control_plane.openai_agents import run_openai_agents_discovery

    class FakeToolCallingModel(Model):
        def __init__(self) -> None:
            self.calls = 0

        async def get_response(self, *args, **kwargs) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                output = [
                    ResponseFunctionToolCall(
                        arguments=json.dumps({"queries": ["human phosphoproteomics DDA"]}),
                        call_id="call_discovery_001",
                        name="search_repository_datasets",
                        type="function_call",
                        status="completed",
                    )
                ]
            elif self.calls == 2:
                output = [
                    ResponseFunctionToolCall(
                        arguments=json.dumps(
                            {
                                "round_index": 0,
                                "rationale": "The merged pool contains a valid file-level candidate.",
                            }
                        ),
                        call_id="call_selection_001",
                        name="select_discovery_manifest",
                        type="function_call",
                        status="completed",
                    )
                ]
            else:
                output = [
                    ResponseOutputMessage(
                        id="message_001",
                        content=[
                            ResponseOutputText(
                                annotations=[],
                                text="Accept the current manifest because it contains a valid file-level candidate.",
                                type="output_text",
                            )
                        ],
                        role="assistant",
                        status="completed",
                        type="message",
                    )
                ]
            return ModelResponse(output=output, usage=Usage(requests=1), response_id=None)

        async def stream_response(self, *args, **kwargs):
            if False:
                yield None

    request = DatasetRequest(repository="pride", species=["human"], max_projects=2, max_files=4)

    def fake_discovery(request: DatasetRequest, memory=None, queries=None) -> DatasetManifest:
        project = DiscoveredProject(project_accession="PXDAGENT001", project_title="Agent test")
        file = DiscoveredFile(
            project_accession=project.project_accession,
            project_title=project.project_title,
            file_name="agent_test.raw",
            file_type=".raw",
            file_role="raw_acquisition",
            acquisition_mode="dda",
            species=["human"],
            validity_status="valid",
            evidence_level="file",
        )
        return DatasetManifest(
            request=request,
            projects=[project],
            files=[file],
            summary={
                "selected_projects": 1,
                "selected_files": 1,
                "candidate_projects_seen": 1,
                "validity_status_counts": {"valid": 1},
                "evidence_level_distribution": {"file": 1},
                "instrument_family_distribution": {"orbitrap": 1},
                "unknown_counts": {},
            },
        )

    model = FakeToolCallingModel()
    result = run_openai_agents_discovery(
        prompt="Find human phosphoproteomics DDA data",
        request=request,
        output_dir=tmp_path / "agents_discovery",
        state_db=tmp_path / "agent_control.sqlite",
        budget=AgentBudget(max_turns=4, max_tool_calls=4, max_discovery_rounds=2),
        run_id="agents_runner_001",
        discovery_func=fake_discovery,
        model=model,
    )

    assert result.status == "completed"
    assert result.discovery_round_count == 1
    assert model.calls == 3
    assert result.selected_round_index == 0
    assert result.selection_rationale == "The merged pool contains a valid file-level candidate."
    assert Path(result.files["dataset_manifest_json"]).exists()
    assert Path(result.files["agents_discovery_events_json"]).exists()
    assert "Accept the current manifest" in result.final_output
    events = json.loads(Path(result.files["agents_discovery_events_json"]).read_text(encoding="utf-8"))
    assert "manifest_selected" in [event["event_type"] for event in events]


def test_openai_agents_setup_failure_is_persisted(monkeypatch, tmp_path: Path) -> None:
    from agent.control_plane import openai_agents

    def fail_to_build_tool(_function):
        raise RuntimeError("tool schema setup failed")

    monkeypatch.setattr(
        openai_agents,
        "_load_agents_sdk",
        lambda: {"function_tool": fail_to_build_tool},
    )
    state_db = tmp_path / "state.sqlite"

    result = openai_agents.run_openai_agents_discovery(
        prompt="Find proteomics data",
        request=DatasetRequest(repository="pride"),
        output_dir=tmp_path / "output",
        state_db=state_db,
        run_id="setup_failure_001",
        model=object(),
    )

    assert result.status == "failed"
    assert result.blockers == ["tool schema setup failed"]
    stored = AgentRunStore(state_db).load_run(result.run_id)
    assert stored is not None
    assert stored.status == "failed"
    assert Path(result.files["agents_discovery_summary_json"]).exists()
    assert [event.event_type for event in AgentRunStore(state_db).list_events(result.run_id)] == [
        "run_started",
        "run_failed",
    ]


def test_agents_discover_dataset_cli_keeps_new_runtime_opt_in(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(**kwargs) -> OpenAIAgentsDiscoveryResult:
        captured.update(kwargs)
        return OpenAIAgentsDiscoveryResult(
            status="completed",
            run_id="cli_agents_001",
            output_dir=str(kwargs["output_dir"]),
            state_db=str(tmp_path / "state.sqlite"),
            selected_manifest_path=str(tmp_path / "dataset_manifest.json"),
            discovery_round_count=1,
            final_output="label‑free",
        )

    monkeypatch.setattr("agent.cli.run_openai_agents_discovery", fake_run)
    output_dir = tmp_path / "cli_output"
    result = CliRunner().invoke(
        app,
        [
            "agents-discover-dataset",
            "--prompt",
            "Find human DDA data for RT prediction",
            "--output-dir",
            str(output_dir),
            "--species",
            "human",
            "--task-type",
            "rt_prediction",
            "--max-rounds",
            "2",
            "--no-use-memory",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["task_type"] == "rt_prediction"
    assert captured["memory"] is None
    request = captured["request"]
    assert isinstance(request, DatasetRequest)
    assert request.species == ["human"]
    assert request.goal == "general"
    assert captured["budget"].max_discovery_rounds == 2
    assert "label‑free" not in result.output
    assert "label\\u2011free" in result.output
