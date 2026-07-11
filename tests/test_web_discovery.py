from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import agent.web.app as web_app
from fastapi import BackgroundTasks
from agent.control_plane.models import AgentEvent, OpenAIAgentsDiscoveryResult
from agent.discovery.agentic import AgenticDiscoveryPlanner
from agent.discovery.memory import DiscoveryMemory
from agent.discovery.models import DatasetManifest, DatasetRequest, DiscoveredFile, DiscoveredProject


def _manifest(request: DatasetRequest) -> DatasetManifest:
    project = DiscoveredProject(
        project_accession="PXD000001",
        project_title="Human phosphoproteomics DDA",
        species=["human"],
        acquisition_mode="dda",
        ptm_type="phospho",
        project_score=80,
        confidence=0.9,
        trust_score=0.92,
        validity_status="valid",
        validity_reasons=["strong_ptm_evidence"],
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
        diversity_tags=["species:human", "instrument:orbitrap", "fragmentation:HCD", "lc:60-120min"],
        selected_file_count=1,
    )
    file = DiscoveredFile(
        project_accession="PXD000001",
        project_title=project.project_title,
        file_name="HeLa_01.raw",
        download_url="https://ftp.pride.ebi.ac.uk/HeLa_01.raw",
        file_type=".raw",
        file_role="raw_acquisition",
        expected_size_bytes=1000,
        species=["human"],
        acquisition_mode="dda",
        ptm_type="phospho",
        project_score=80,
        file_score=60,
        confidence=0.9,
        trust_score=0.92,
        validity_status="valid",
        validity_reasons=["strong_ptm_evidence"],
        evidence_level="mixed",
        instrument_families=["orbitrap"],
        fragmentation_methods=["HCD"],
        lc_gradient_minutes=90.0,
        diversity_tags=["species:human", "instrument:orbitrap", "fragmentation:HCD", "lc:60-120min"],
    )
    return DatasetManifest(
        request=request,
        projects=[project],
        files=[file],
        summary={
            "queries": ["phosphoproteomics"],
            "selected_projects": 1,
            "selected_files": 1,
            "diversity": {"unknown_counts": {"instrument_family": 0, "fragmentation_method": 0}},
            "unknown_counts": {"instrument_family": 0, "fragmentation_method": 0},
            "validity": {"validity_status_counts": {"valid": 1}, "validity_reason_counts": {"strong_ptm_evidence": 1}},
            "validity_status_counts": {"valid": 1},
            "validity_reason_counts": {"strong_ptm_evidence": 1},
        },
    )


def test_discovery_job_event_is_structured_and_recursively_sanitized(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    job_id = "structured_event"
    with web_app._discovery_jobs_lock:
        web_app._discovery_jobs[job_id] = {"job_id": job_id, "status": "running", "logs": []}
    try:
        web_app._append_discovery_job_event(
            job_id,
            AgentEvent(
                sequence=7,
                run_id="run_1",
                event_type="budget_decision_recorded",
                created_at="2026-07-10T19:55:00+08:00",
                payload={
                    "reasoning_summary": "Use measured metadata gap",
                    "api_key": "sk-secret-value",
                    "nested": {"authorization": "Bearer secret", "message": "sk-hidden-value"},
                },
            ),
        )
        entry = web_app._discovery_jobs[job_id]["logs"][0]
        serialized = json.dumps(entry)
        assert entry["actor"] == "Budget Agent"
        assert entry["type"] == "budget_decision_recorded"
        assert entry["source_sequence"] == 7
        assert "api_key" not in serialized
        assert "authorization" not in serialized
        assert "sk-secret" not in serialized
        assert "sk-hidden" not in serialized
    finally:
        with web_app._discovery_jobs_lock:
            web_app._discovery_jobs.pop(job_id, None)


def test_candidate_search_event_exposes_real_search_progress(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    job_id = "candidate_search_event"
    with web_app._discovery_jobs_lock:
        web_app._discovery_jobs[job_id] = {"job_id": job_id, "status": "running", "logs": []}
    try:
        web_app._append_discovery_job_event(
            job_id,
            AgentEvent(
                sequence=8,
                run_id="run_1",
                event_type="candidate_search_completed",
                created_at="2026-07-11T09:00:00+08:00",
                payload={
                    "observation": {
                        "candidate_count": 14,
                        "new_candidate_count": 9,
                        "high_relevance_candidate_count": 4,
                        "semantic_coverage": 0.75,
                    },
                    "metrics": {"duplicate_rate": 0.2, "semantic_coverage_gap": 0.25},
                },
            ),
        )
        entry = web_app._discovery_jobs[job_id]["logs"][0]
        assert entry["actor"] == "Repository Search"
        assert "14 candidate project(s)" in entry["message"]
        assert "4 high-relevance" in entry["message"]
        assert "75%" in entry["message"]
        assert entry["metrics"]["duplicate_rate"] == 0.2
    finally:
        with web_app._discovery_jobs_lock:
            web_app._discovery_jobs.pop(job_id, None)


class _FakeDiscoveryLLM:
    def complete_json(self, *, system_prompt: str, user_prompt: str):
        return {
            "task_spec": {
                "task_type": "ptm_discovery",
                "target_ptm": "phospho",
                "species_include": ["human"],
                "acquisition_mode": "dda",
            },
            "queries": [{"query": "human phosphoproteomics DDA", "purpose": "baseline"}],
            "trace": [{"step": "initial_query_plan", "thought": "plan", "action": "plan_queries"}],
            "warnings": [],
        }


class _FakeGoalParseLLM:
    def complete_json(self, *, system_prompt: str, user_prompt: str):
        assert "Parse this discovery request" in user_prompt
        return {
            "fields": {
                "repository": "pride",
                "goal": "ptm",
                "ptm_type": "phospho",
                "species": ["human"],
                "acquisition_mode": "dda",
                "task_type": "rt_prediction",
                "max_projects": 3,
                "max_files": 12,
                "max_files_per_project": 4,
                "agentic_rounds": 1,
                "diversity_strategy": "high",
            },
            "warnings": [],
            "reasoning": "Mapped natural language to supported DDA-first discovery fields.",
        }


class _FakeGoalParseLLMUsesCurrentRounds:
    def complete_json(self, *, system_prompt: str, user_prompt: str):
        assert "agentic rounds 1" in user_prompt.lower()
        return {
            "fields": {
                "repository": "pride",
                "goal": "ptm",
                "ptm_type": "phospho",
                "species": ["human"],
                "acquisition_mode": "dda",
                "task_type": "rt_prediction",
                "agentic_rounds": 2,
                "diversity_strategy": "high",
            },
            "warnings": [],
            "reasoning": "Incorrectly retained the current UI round setting.",
        }


class _FakeConfiguredGoalParseLLM:
    instances: list["_FakeConfiguredGoalParseLLM"] = []

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.instances.append(self)

    def complete_json(self, *, system_prompt: str, user_prompt: str):
        return {
            "fields": {
                "repository": "pride",
                "goal": "ptm",
                "ptm_type": "phospho",
                "species": ["human"],
                "acquisition_mode": "dda",
                "task_type": "fragment_intensity_prediction",
                "agentic_rounds": 1,
                "diversity_strategy": "high",
            },
            "warnings": [],
            "reasoning": "Used request-scoped LLM config.",
        }


class _FakeGeneralizedGoalParseLLM:
    def complete_json(self, *, system_prompt: str, user_prompt: str):
        assert "labeling_strategy" in system_prompt
        return {
            "fields": {
                "repository": "pride",
                "goal": "ptm",
                "ptm_type": "acetyl",
                "species": ["rat"],
                "acquisition_mode": "dda",
                "labeling_strategy": "TMT",
                "task_type": "fragment_intensity_prediction",
                "agentic_rounds": 1,
                "diversity_strategy": "high",
            },
            "warnings": [],
            "reasoning": "Parsed generalized metadata constraints.",
        }


class _FakePrideClient:
    def __init__(self, *args, **kwargs):
        pass

    def get_project(self, accession: str):
        assert accession == "PXD000001"
        return {
            "accession": accession,
            "title": "Human phosphoproteomics DDA",
            "projectDescription": "Homo sapiens phosphoproteomics acquired by DDA HCD.",
            "organisms": [{"name": "Homo sapiens"}],
            "instruments": [{"name": "Q Exactive"}],
            "experimentTypes": [{"name": "shotgun proteomics"}],
            "keywords": ["phosphoproteomics", "DDA"],
        }

    def list_project_files(self, accession: str, keyword: str | None = None, page_size: int = 1000, max_files: int | None = None):
        if keyword == "sdrf":
            return [{"fileName": "PXD000001.sdrf.tsv", "publicFileLocations": [{"value": "https://example.test/PXD000001.sdrf.tsv"}]}]
        return [
            {
                "fileName": "HeLa_01.mzML",
                "fileSizeBytes": 12,
                "publicFileLocations": [{"value": "https://example.test/HeLa_01.mzML"}],
            }
        ]

    @staticmethod
    def first_download_url(file_record):
        locations = file_record.get("publicFileLocations", [])
        return locations[0]["value"] if locations else None

    def download_text(self, url: str):
        return (
            "comment[data file]\tcomment[instrument]\tcomment[fragmentation method]\tcomment[chromatography]\n"
            "HeLa_01.mzML\tQ Exactive\tHCD\t90 min nanoLC gradient\n"
        )

    def close(self):
        return None


def _wait_discovery_job(job_id: str, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = asyncio.run(web_app.get_discovery_job(job_id))
        if status.get("status") in {"completed", "failed", "cancelled"}:
            return status
        time.sleep(0.05)
    return asyncio.run(web_app.get_discovery_job(job_id))


def test_parse_discovery_goal_uses_llm(monkeypatch):
    monkeypatch.setattr(web_app, "default_discovery_llm_client", lambda: _FakeGoalParseLLM())

    result = asyncio.run(
        web_app.parse_discovery_goal(
            {
                "prompt": "Find diverse human phospho DDA data for RT prediction with agentic rounds 1",
                "current": {"agentic_rounds": 2, "max_projects": 5, "max_files": 50},
            }
        )
    )

    assert result["status"] == "completed"
    assert result["parser"] == "llm"
    assert result["fields"]["species"] == ["human"]
    assert result["fields"]["task_type"] == "rt_prediction"
    assert result["fields"]["diversity_strategy"] == "high"
    assert result["fields"]["agentic_rounds"] == 1


def test_parse_discovery_goal_explicit_rounds_override_current_ui(monkeypatch):
    monkeypatch.setattr(web_app, "default_discovery_llm_client", lambda: _FakeGoalParseLLMUsesCurrentRounds())

    result = asyncio.run(
        web_app.parse_discovery_goal(
            {
                "prompt": "Find diverse human phospho DDA data for RT prediction with agentic rounds 1",
                "current": {"agentic_rounds": 2, "max_projects": 5, "max_files": 50},
            }
        )
    )

    assert result["parser"] == "llm"
    assert result["fields"]["agentic_rounds"] == 1


def test_parse_discovery_goal_uses_request_llm_config(monkeypatch):
    _FakeConfiguredGoalParseLLM.instances = []
    monkeypatch.setattr(web_app, "default_discovery_llm_client", lambda: None)
    monkeypatch.setattr(web_app, "OpenAICompatibleDiscoveryLLM", _FakeConfiguredGoalParseLLM)

    result = asyncio.run(
        web_app.parse_discovery_goal(
            {
                "prompt": "Find human phospho DDA data for fragment intensity with agentic rounds 1",
                "current": {"agentic_rounds": 2},
                "llm_config": {
                    "api_key": "sk-test",
                    "base_url": "https://llm.example.test/",
                    "model": "deepseek-test",
                    "timeout": "9",
                },
            }
        )
    )

    assert result["parser"] == "llm"
    assert result["fields"]["task_type"] == "fragment_intensity_prediction"
    assert result["fields"]["agentic_rounds"] == 1
    assert len(_FakeConfiguredGoalParseLLM.instances) == 1
    client = _FakeConfiguredGoalParseLLM.instances[0]
    assert client.api_key == "sk-test"
    assert client.base_url == "https://llm.example.test"
    assert client.model == "deepseek-test"
    assert client.timeout == 9.0


def test_parse_discovery_goal_uses_saved_llm_config_without_browser_key(monkeypatch):
    _FakeConfiguredGoalParseLLM.instances = []
    web_app._llm_config_store().save(
        {
            "api_key": "saved-discovery-key",
            "base_url": "https://saved.example.test",
            "model": "saved-model",
            "timeout": "17",
        }
    )
    monkeypatch.setattr(web_app, "default_discovery_llm_client", lambda: None)
    monkeypatch.setattr(web_app, "OpenAICompatibleDiscoveryLLM", _FakeConfiguredGoalParseLLM)

    result = asyncio.run(
        web_app.parse_discovery_goal(
            {
                "prompt": "Find human phospho DDA data for fragment intensity",
                "current": {"agentic_rounds": 2},
                "llm_config": {},
            }
        )
    )

    assert result["parser"] == "llm"
    client = _FakeConfiguredGoalParseLLM.instances[0]
    assert client.api_key == "saved-discovery-key"
    assert client.base_url == "https://saved.example.test"
    assert client.model == "saved-model"
    assert client.timeout == 17.0


def test_parse_discovery_goal_supports_species_ptm_and_labeling(monkeypatch):
    monkeypatch.setattr(web_app, "default_discovery_llm_client", lambda: _FakeGeneralizedGoalParseLLM())

    result = asyncio.run(
        web_app.parse_discovery_goal(
            {
                "prompt": "Find diverse rat TMT acetyl DDA data for fragment intensity",
                "current": {"agentic_rounds": 2},
            }
        )
    )

    assert result["status"] == "completed"
    assert result["fields"]["species"] == ["rat"]
    assert result["fields"]["canonical_species"] == ["rat"]
    assert result["fields"]["organism_taxon_id"] == ["10116"]
    assert result["fields"]["ptm_type"] == "acetyl"
    assert result["fields"]["modification_scope"] == "acetyl"
    assert result["fields"]["labeling_strategy"] == "TMT"
    assert result["fields"]["task_type"] == "fragment_intensity_prediction"


def test_web_general_discovery_request_does_not_default_to_human():
    request = web_app._clean_dataset_request(
        {
            "goal": "general",
            "prompt": "Find small DDA proteomics datasets for drug treatment with kinase inhibitor context.",
            "species": [],
            "species_policy": "open",
        }
    )

    assert request.goal == "general"
    assert request.species == []
    assert request.species_policy == "open"
    assert request.ptm_type == "unknown_ptm"
    assert "drug treatment" in request.query_terms


def test_raw_prompt_request_does_not_invent_hard_acquisition_or_labeling_constraints():
    request = web_app._clean_dataset_request(
        {"prompt": "Find human neuronal proteomics suitable for drug-response modeling."}
    )

    assert request.acquisition_mode == "unknown"
    assert request.labeling_strategy == "unknown"
    assert request.hard_constraint_fields == ["repository"]
    assert request.constraint_provenance == {"repository": "default"}


def test_explicit_web_fields_are_recorded_as_hard_constraints():
    request = web_app._clean_dataset_request(
        {
            "prompt": "Find human DDA data.",
            "species": ["human"],
            "species_policy": "include_only",
            "acquisition_mode": "dda",
            "labeling_strategy": "label_free",
        }
    )

    assert "species" in request.hard_constraint_fields
    assert "acquisition_mode" in request.hard_constraint_fields
    assert "labeling_strategy" in request.hard_constraint_fields


def test_discovery_job_runs_in_background_and_exposes_logs(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)

    def fake_discovery(request, memory=None, report=None, should_cancel=None, **_kwargs):
        assert should_cancel is not None
        assert should_cancel() is False
        if report is not None:
            report("fake discovery progress")
        return _manifest(request)

    monkeypatch.setattr(web_app, "discover_pride_dataset", fake_discovery)
    job_id = "discovery_job_test_complete"
    with web_app._discovery_jobs_lock:
        web_app._discovery_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": web_app._now_app_iso(),
            "started_at": None,
            "finished_at": None,
            "cancel_requested": False,
            "logs": [{"ts": web_app._now_app_iso(), "level": "info", "message": "Discovery job queued."}],
            "body": {"max_projects": 1, "max_files": 1},
            "record": None,
            "error": None,
        }
    try:
        web_app._run_discovery_job(job_id)
        final = asyncio.run(web_app.get_discovery_job(job_id))
        assert final["status"] == "completed"
        assert final["record"]["file_count"] == 1
        messages = [item["message"] for item in final["logs"]]
        assert "Discovery job queued." in messages
        assert "fake discovery progress" in messages
        assert "Discovery job completed." in messages
    finally:
        with web_app._discovery_jobs_lock:
            web_app._discovery_jobs.pop(job_id, None)


def test_discovery_job_recovers_completed_state_after_memory_loss(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, **_kwargs: _manifest(request))

    created = asyncio.run(web_app.start_discovery_job({"max_projects": 1, "max_files": 1}))
    job_id = created["job_id"]
    final = _wait_discovery_job(job_id)
    assert final["status"] == "completed"
    assert final["record"]["file_count"] == 1

    with web_app._discovery_jobs_lock:
        web_app._discovery_jobs.pop(job_id, None)

    recovered = asyncio.run(web_app.get_discovery_job(job_id))
    assert recovered["status"] == "completed"
    assert recovered["record"]["file_count"] == 1
    messages = [item["message"] for item in recovered["logs"]]
    assert "Discovery job completed." in messages


def test_discovery_job_can_defer_worker_until_response(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, **_kwargs: _manifest(request))

    background_tasks = BackgroundTasks()
    created = asyncio.run(web_app.start_discovery_job({"max_projects": 1, "max_files": 1}, background_tasks))
    job_id = created["job_id"]
    assert created["status"] == "queued"

    with web_app._discovery_jobs_lock:
        queued = web_app._discovery_jobs[job_id]
        assert queued["status"] == "queued"
        assert queued["record"] is None

    asyncio.run(background_tasks())
    final = _wait_discovery_job(job_id)
    assert final["status"] == "completed"
    assert final["record"]["file_count"] == 1


def test_discovery_job_reports_interrupted_state_after_memory_loss(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    job_id = "discovery_job_test_interrupted"
    web_app._persist_discovery_job(
        {
            "job_id": job_id,
            "status": "running",
            "created_at": web_app._now_app_iso(),
            "started_at": web_app._now_app_iso(),
            "finished_at": None,
            "cancel_requested": False,
            "logs": [{"ts": web_app._now_app_iso(), "level": "info", "message": "Discovery job started."}],
            "record": None,
            "error": None,
        }
    )
    with web_app._discovery_jobs_lock:
        web_app._discovery_jobs.pop(job_id, None)

    recovered = asyncio.run(web_app.get_discovery_job(job_id))
    assert recovered["status"] == "failed"
    assert recovered["error"] == "discovery_job_interrupted_by_server_reload"
    assert any("interrupted by a server reload" in item["message"] for item in recovered["logs"])


def test_discovery_job_can_be_cancelled(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)

    def slow_discovery(request, memory=None, report=None, should_cancel=None, **_kwargs):
        assert should_cancel is not None
        for _index in range(200):
            if should_cancel():
                raise InterruptedError("Discovery cancelled.")
            if report is not None:
                report("slow discovery heartbeat")
            time.sleep(0.01)
        return _manifest(request)

    monkeypatch.setattr(web_app, "discover_pride_dataset", slow_discovery)
    created = asyncio.run(web_app.start_discovery_job({"max_projects": 1, "max_files": 1}))
    job_id = created["job_id"]
    try:
        cancel = asyncio.run(web_app.cancel_discovery_job(job_id))
        assert cancel["cancel_requested"] is True
        final = _wait_discovery_job(job_id)
        assert final["status"] == "cancelled"
        assert final["cancel_requested"] is True
        messages = [item["message"] for item in final["logs"]]
        assert any("Cancel requested" in message for message in messages)
        assert any("Discovery cancelled" in message for message in messages)
    finally:
        with web_app._discovery_jobs_lock:
            web_app._discovery_jobs.pop(job_id, None)


def test_create_discovery_writes_manifest_and_memory(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, **_kwargs: _manifest(request))

    payload = {
        "repository": "pride",
        "goal": "ptm",
        "ptm_type": "phospho",
        "species": ["human"],
        "acquisition_mode": "dda",
        "max_projects": 1,
        "max_files": 1,
        "max_files_per_project": 1,
        "save_memory": True,
    }

    result = asyncio.run(web_app.create_discovery(payload))

    assert result["status"] == "completed"
    assert result["discovery_id"]
    assert result["project_count"] == 1
    assert result["file_count"] == 1
    assert result["summary"]["memory_saved"] is True
    assert result["summary"]["memory_used"] is True
    assert result["summary"]["usable_files"] == 1
    assert "dataset_manifest_csv" in result["downloads"]
    assert "dataset_manifest_usable_csv" in result["downloads"]
    assert "batch_inputs_valid" in result["downloads"]
    assert "batch_inputs_usable" in result["downloads"]
    assert "quality_report" in result["downloads"]
    assert "repository_audit_json" in result["downloads"]
    assert "repository_audit_csv" in result["downloads"]
    assert "repository_audit_md" in result["downloads"]
    assert result["files"][0]["instrument_families"] == ["orbitrap"]
    assert result["files"][0]["validity_status"] == "valid"

    output_dir = tmp_path / "discovery" / result["discovery_id"]
    assert (output_dir / "dataset_manifest.json").exists()
    assert (output_dir / "dataset_manifest.csv").exists()
    assert (output_dir / "dataset_manifest_usable.csv").exists()
    assert (output_dir / "batch_inputs_valid.txt").exists()
    assert (output_dir / "batch_inputs_usable.txt").exists()
    assert (output_dir / "quality_report.json").exists()
    assert (output_dir / "repository_audit.json").exists()
    assert (output_dir / "repository_audit.csv").exists()
    assert (output_dir / "repository_audit.md").exists()
    assert (tmp_path / "discovery_memory" / "discovery_runs.jsonl").exists()
    stored = json.loads((output_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert stored["run_id"] == result["run_id"]


def test_clean_dataset_request_accepts_multi_ptm_types():
    request = web_app._clean_dataset_request(
        {
            "repository": "pride",
            "goal": "ptm",
            "ptm_types": ["phospho", "acetyl", "ubiquitin"],
            "species": ["human"],
            "acquisition_mode": "dda",
            "max_projects": 1,
            "max_files": 1,
        }
    )

    assert request.ptm_type == "phospho"
    assert request.ptm_types == ["phospho", "acetyl", "ubiquitin"]
    assert request.modification_scope == "phospho;acetyl;ubiquitin"


def test_get_discovery_reads_existing_manifest(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, **_kwargs: _manifest(request))
    created = asyncio.run(web_app.create_discovery({"max_projects": 1, "max_files": 1}))

    loaded = asyncio.run(web_app.get_discovery(created["discovery_id"]))

    assert loaded["discovery_id"] == created["discovery_id"]
    assert loaded["files"][0]["file_name"] == "HeLa_01.raw"
    assert loaded["summary"]["selected_files"] == 1


def test_web_discovery_response_exposes_diversity_summary(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, **_kwargs: _manifest(request))

    created = asyncio.run(web_app.create_discovery({"max_projects": 1, "max_files": 1}))

    assert created["summary"]["unknown_counts"]["instrument_family"] == 0
    assert created["files"][0]["fragmentation_methods"] == ["HCD"]
    assert created["files"][0]["diversity_tags"]
    assert created["summary"]["validity_status_counts"]["valid"] == 1


def test_web_discovery_can_add_task_readiness(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, **_kwargs: _manifest(request))

    created = asyncio.run(web_app.create_discovery({"max_projects": 1, "max_files": 1, "task_type": "rt_prediction"}))

    assert created["summary"]["task_type"] == "rt_prediction"
    assert created["summary"]["task_readiness"]["status_counts"]["weak_ready"] == 1
    assert created["files"][0]["task_readiness_status"] == "weak_ready"
    assert "dataset_manifest_task_ready_csv" in created["downloads"]
    assert "batch_inputs_task_ready" in created["downloads"]


def test_local_discovery_does_not_assign_requested_species_without_evidence(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path / "runs")
    local_dir = tmp_path / "local_samples"
    local_dir.mkdir()
    (local_dir / "sample_01.mzML").write_text("tiny", encoding="utf-8")

    created = asyncio.run(
        web_app.create_discovery(
            {
                "source": "local",
                "local_dir": str(local_dir),
                "species": ["mouse"],
                "max_files": 5,
            }
        )
    )

    assert created["status"] == "completed"
    assert created["files"][0]["species"] == []
    assert created["projects"][0]["species"] == []
    assert created["files"][0]["validity_status"] == "weak_keep"
    assert created["files"][0]["species_policy"] == "open"
    assert "missing_species_evidence" in created["files"][0]["validity_reasons"]
    assert created["summary"]["species_distribution"] == {"unknown": 1}


def test_local_discovery_excludes_clear_species_mismatch(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path / "runs")
    local_dir = tmp_path / "local_samples"
    local_dir.mkdir()
    (local_dir / "HeLa_01.mzML").write_text("tiny", encoding="utf-8")

    created = asyncio.run(
        web_app.create_discovery(
            {
                "source": "local",
                "local_dir": str(local_dir),
                "species": ["mouse"],
                "species_policy": "include_only",
                "max_files": 5,
            }
        )
    )

    assert created["files"][0]["species"] == ["human"]
    assert created["files"][0]["validity_status"] == "exclude"
    assert "species_mismatch" in created["files"][0]["validity_reasons"]


def test_local_discovery_enriches_project_directory_metadata(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path / "runs")
    monkeypatch.setattr(web_app, "PrideClient", _FakePrideClient)
    local_dir = tmp_path / "local_samples"
    project_dir = local_dir / "PXD000001"
    project_dir.mkdir(parents=True)
    (project_dir / "HeLa_01.mzML").write_text("tiny", encoding="utf-8")

    created = asyncio.run(
        web_app.create_discovery(
            {
                "source": "local",
                "local_dir": str(local_dir),
                "species": ["human"],
                "max_files": 5,
            }
        )
    )

    assert created["projects"][0]["project_accession"] == "PXD000001"
    assert created["files"][0]["project_accession"] == "PXD000001"
    assert created["files"][0]["species"] == ["human"]
    assert created["files"][0]["instrument_families"] == ["orbitrap"]
    assert created["files"][0]["fragmentation_methods"] == ["HCD"]
    assert created["files"][0]["lc_gradient_minutes"] == 90.0
    assert created["summary"]["metadata_enrichment"] == "pride_project_hint"


def test_web_discovery_agentic_exposes_round_summary(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "default_agentic_discovery_planner", lambda: AgenticDiscoveryPlanner(_FakeDiscoveryLLM()))
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, queries=None, **_kwargs: _manifest(request))

    created = asyncio.run(
        web_app.create_discovery(
            {
                "max_projects": 1,
                "max_files": 1,
                "agentic": True,
                "agentic_rounds": 1,
                "prompt": "Find human phospho DDA data",
            }
        )
    )

    assert created["summary"]["agentic"]["enabled"] is True
    assert created["summary"]["agentic"]["rounds"] == 1
    assert "agentic_plan" in created["downloads"]
    assert "agentic_rounds" in created["downloads"]


def test_web_discovery_agentic_falls_back_without_llm_planner(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "default_agentic_discovery_planner", lambda: None)
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, queries=None, **_kwargs: _manifest(request))

    created = asyncio.run(
        web_app.create_discovery(
            {
                "max_projects": 1,
                "max_files": 1,
                "agentic": True,
                "agentic_rounds": 1,
                "prompt": "Find human immunopeptidomics DDA data",
            }
        )
    )

    assert created["status"] == "completed"
    assert created["summary"]["agentic"]["enabled"] is False
    assert created["summary"]["agentic"]["requested"] is True
    assert created["summary"]["agentic"]["fallback"]["reason"] == "llm_unavailable"
    assert "agentic_plan" not in created["downloads"]


def test_agentic_discovery_planner_uses_saved_llm_config(monkeypatch):
    _FakeConfiguredGoalParseLLM.instances = []
    web_app._llm_config_store().save(
        {
            "api_key": "saved-planner-key",
            "base_url": "https://planner.example.test",
            "model": "planner-model",
            "timeout": "23",
        }
    )
    monkeypatch.setattr(web_app, "OpenAICompatibleDiscoveryLLM", _FakeConfiguredGoalParseLLM)

    planner = web_app._agentic_discovery_planner({})

    assert isinstance(planner, AgenticDiscoveryPlanner)
    client = _FakeConfiguredGoalParseLLM.instances[0]
    assert client.api_key == "saved-planner-key"
    assert client.base_url == "https://planner.example.test"
    assert client.model == "planner-model"
    assert client.timeout == 23.0


def test_web_agent_uses_server_dynamic_limits_not_request_presets(monkeypatch):
    monkeypatch.setenv("AGENT_DISCOVERY_MODE", "multi_agent")
    monkeypatch.setenv("AGENT_MAX_MODEL_TURNS", "50")
    monkeypatch.setenv("AGENT_MAX_TOOL_CALLS", "100")
    monkeypatch.setenv("AGENT_MAX_QUERY_UNITS", "24")
    monkeypatch.setenv("AGENT_MAX_REPOSITORY_REQUESTS", "120")
    mode, budget, limits = web_app._agent_discovery_configuration(
        {"agent_budget_mode": "deep", "agent_max_rounds": 8}
    )
    assert mode == "multi_agent"
    assert budget.max_turns == 50
    assert budget.max_tool_calls == 100
    assert limits.max_query_units == 24
    assert limits.max_repository_requests == 120


def test_web_discovery_openai_agents_runtime_reuses_existing_result_contract(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setenv("AGENT_DISCOVERY_MODE", "multi_agent")
    web_app._llm_config_store().save(
        {
            "api_key": "saved-agent-key",
            "base_url": "https://saved.example.test/v1",
            "model": "saved-agent-model",
            "timeout": "1200",
        }
    )
    captured: dict[str, object] = {}

    def fake_run_openai_agents_discovery(**kwargs) -> OpenAIAgentsDiscoveryResult:
        captured.update(kwargs)
        output_dir = Path(kwargs["output_dir"])
        manifest = _manifest(kwargs["request"]).model_copy(update={"run_id": kwargs["run_id"]})
        paths = web_app.write_dataset_manifest(manifest, output_dir)
        summary_path = output_dir / "agents_discovery_summary.json"
        events_path = output_dir / "agents_discovery_events.json"
        report_path = output_dir / "agents_discovery_report.md"
        budget_path = output_dir / "agents_discovery_budget.json"
        summary_path.write_text('{"tool_call_count":2,"stop_reason":"manifest_selected","dynamic_usage":{"query_units":1,"repository_requests":3,"search_batches":1,"budget_reviews":1},"budget_audit":{"hard_limits_reached":false}}', encoding="utf-8")
        events_path.write_text("[]", encoding="utf-8")
        report_path.write_text("# Agent report\n", encoding="utf-8")
        budget_path.write_text('{"mode":"multi_agent_dynamic"}', encoding="utf-8")
        return OpenAIAgentsDiscoveryResult(
            status="completed",
            run_id=str(kwargs["run_id"]),
            output_dir=str(output_dir),
            state_db=str(output_dir / "agent_control.sqlite"),
            selected_manifest_path=str(paths["dataset_manifest_json"]),
            selected_round_index=0,
            selection_rationale="The merged candidate pool is the strongest manifest.",
            discovery_round_count=1,
            final_output="Accepted the persisted manifest.",
            files={
                **{key: str(path) for key, path in paths.items()},
                "agents_discovery_summary_json": str(summary_path),
                "agents_discovery_events_json": str(events_path),
                "agents_discovery_report_md": str(report_path),
                "agents_discovery_budget_json": str(budget_path),
            },
        )

    monkeypatch.setattr(web_app, "run_openai_agents_discovery", fake_run_openai_agents_discovery)
    created = asyncio.run(
        web_app.create_discovery(
            {
                "runtime": "openai_agents",
                "prompt": "Find human phospho DDA data for RT prediction",
                "species": ["human"],
                "task_type": "rt_prediction",
                "max_projects": 1,
                "max_files": 1,
                "agent_max_rounds": 2,
                "agent_max_turns": 9,
                "agent_max_tool_calls": 7,
            }
        )
    )

    assert created["status"] == "completed"
    assert created["runtime"] == "openai_agents"
    assert created["project_count"] == 1
    assert created["file_count"] == 1
    assert created["agent"]["status"] == "completed"
    assert created["agent"]["tool_calls"] == 2
    assert created["agent"]["mode"] == "multi_agent"
    assert created["agent"]["query_units"] == 1
    assert created["agent"]["repository_requests"] == 3
    assert created["agent"]["selected_round_index"] == 0
    assert created["agent"]["selection_rationale"] == "The merged candidate pool is the strongest manifest."
    assert created["summary"]["agent_runtime"]["final_output"] == "Accepted the persisted manifest."
    assert "agents_discovery_summary_json" in created["downloads"]
    assert "agents_discovery_events_json" in created["downloads"]
    assert "agents_discovery_report_md" in created["downloads"]
    assert "agents_discovery_budget_json" in created["downloads"]
    assert captured["task_type"] == "rt_prediction"
    assert captured["mode"] == "multi_agent"
    assert captured["budget"].max_turns == 50
    assert captured["budget"].max_tool_calls == 100
    assert captured["dynamic_limits"].initial_query_units == 12
    assert captured["dynamic_limits"].expanded_query_units == 30
    assert captured["dynamic_limits"].max_query_units == 60
    assert captured["dynamic_limits"].max_repository_requests == 300
    assert captured["llm_config"] == {
        "api_key": "saved-agent-key",
        "base_url": "https://saved.example.test/v1",
        "model": "saved-agent-model",
        "timeout": "1200",
    }
    assert "saved-agent-key" not in json.dumps(created)

    reloaded = asyncio.run(web_app.get_discovery(created["discovery_id"]))
    assert reloaded["runtime"] == "openai_agents"
    assert reloaded["status"] == "completed"
    assert reloaded["agent"]["final_output"] == "Accepted the persisted manifest."


def test_web_discovery_openai_agents_blocked_manifest_is_renderable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    web_app._llm_config_store().save(
        {
            "api_key": "saved-blocked-agent-key",
            "base_url": "https://saved.example.test/v1",
            "model": "saved-agent-model",
            "timeout": "1200",
        }
    )

    def fake_blocked_run(**kwargs) -> OpenAIAgentsDiscoveryResult:
        output_dir = Path(kwargs["output_dir"])
        manifest = DatasetManifest(
            request=kwargs["request"],
            run_id=kwargs["run_id"],
            summary={"selected_projects": 0, "selected_files": 0},
        )
        paths = web_app.write_dataset_manifest(manifest, output_dir)
        summary_path = output_dir / "agents_discovery_summary.json"
        summary_path.write_text(
            '{"tool_call_count":1,"stop_reason":"no_selected_files_after_agent_rounds"}',
            encoding="utf-8",
        )
        return OpenAIAgentsDiscoveryResult(
            status="blocked",
            run_id=str(kwargs["run_id"]),
            output_dir=str(output_dir),
            state_db=str(output_dir / "agent_control.sqlite"),
            selected_manifest_path=str(paths["dataset_manifest_json"]),
            discovery_round_count=1,
            final_output="No files matched the hard constraints.",
            blockers=["no_selected_files"],
            files={
                **{key: str(path) for key, path in paths.items()},
                "agents_discovery_summary_json": str(summary_path),
            },
        )

    monkeypatch.setattr(web_app, "run_openai_agents_discovery", fake_blocked_run)
    created = asyncio.run(
        web_app.create_discovery(
            {
                "runtime": "openai_agents",
                "prompt": "Find a constrained dataset",
                "max_projects": 1,
                "max_files": 1,
                "save_memory": False,
            }
        )
    )

    assert created["status"] == "blocked"
    assert created["project_count"] == 0
    assert created["file_count"] == 0
    assert created["agent"]["blockers"] == ["no_selected_files"]
    assert created["summary"]["agent_runtime"]["stop_reason"] == "no_selected_files_after_agent_rounds"
    assert "dataset_manifest_json" in created["downloads"]


def test_review_discovery_run_writes_memory_and_updates_manifest(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, **_kwargs: _manifest(request))
    created = asyncio.run(web_app.create_discovery({"max_projects": 1, "max_files": 1}))

    result = asyncio.run(
        web_app.review_discovery_run(
            created["discovery_id"],
            {
                "reviews": [
                    {
                        "project_accession": "PXD000001",
                        "file_name": "HeLa_01.raw",
                        "decision": "keep",
                        "reason": "correct",
                        "note": "looks good",
                    }
                ]
            },
        )
    )

    assert result["status"] == "completed"
    assert result["review_decisions"] == 1
    assert result["record"]["files"][0]["review_decision"] == "keep"
    assert result["record"]["files"][0]["review_note"] == "looks good"

    memory = DiscoveryMemory(tmp_path / "discovery_memory")
    decisions = memory.load_review_decisions()
    assert len(decisions) == 1
    assert decisions[0].decision == "keep"

    manifest_path = tmp_path / "discovery" / created["discovery_id"] / "dataset_manifest.json"
    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert stored["files"][0]["review_reason"] == "correct"


def test_review_discovery_run_rejects_invalid_decision(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, **_kwargs: _manifest(request))
    created = asyncio.run(web_app.create_discovery({"max_projects": 1, "max_files": 1}))

    result = asyncio.run(
        web_app.review_discovery_run(
            created["discovery_id"],
            {
                "reviews": [
                    {
                        "project_accession": "PXD000001",
                        "file_name": "HeLa_01.raw",
                        "decision": "maybe",
                        "reason": "unclear",
                    }
                ]
            },
        )
    )

    assert "invalid decision" in result["error"]
    assert not (tmp_path / "discovery_memory" / "review_decisions.jsonl").exists()


def test_download_discovery_file_rejects_unknown_key(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(web_app, "_runs_dir", tmp_path)
    monkeypatch.setattr(web_app, "discover_pride_dataset", lambda request, memory=None, **_kwargs: _manifest(request))
    created = asyncio.run(web_app.create_discovery({"max_projects": 1, "max_files": 1}))

    result = asyncio.run(web_app.download_discovery_file(created["discovery_id"], file="../secret"))

    assert result == {"error": "Discovery file not available."}
