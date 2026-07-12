from __future__ import annotations

import json
from pathlib import Path

from agent.discovery.fault_injection import FaultInjectingPrideClient


class FakePride:
    def search_projects(self, keyword: str, page_size: int = 100):
        return [{"accession": "PXD000001", "title": f"Result for {keyword}"}]

    def get_project(self, accession: str):
        return {
            "accession": accession,
            "title": "Complete project",
            "projectDescription": "Human DDA proteomics",
            "organisms": [{"name": "Homo sapiens"}],
        }

    def list_project_files(self, accession: str, keyword=None, page_size=1000, max_files=None):
        return [{"fileName": f"{accession}.raw", "fileSizeBytes": 10}]


def test_transient_timeout_is_consumed_and_retry_passes() -> None:
    client = FaultInjectingPrideClient(
        FakePride(),
        [{"operation": "search_projects", "outcome": "timeout"}],
    )

    try:
        client.search_projects("human")
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected injected timeout")

    rows = client.search_projects("human")
    assert rows[0]["accession"] == "PXD000001"
    assert [event.outcome for event in client.events] == ["timeout", "pass"]


def test_duplicate_and_incomplete_faults_are_deterministic() -> None:
    client = FaultInjectingPrideClient(
        FakePride(),
        [
            {"operation": "search_projects", "outcome": "duplicate"},
            {"operation": "get_project", "outcome": "incomplete"},
            {"operation": "list_project_files", "outcome": "incomplete"},
        ],
    )

    assert len(client.search_projects("human")) == 2
    assert client.get_project("PXD000001") == {
        "accession": "PXD000001",
        "title": "Complete project",
    }
    assert client.list_project_files("PXD000001") == [{"fileName": "PXD000001.raw"}]


def test_recovery_fixture_declares_expected_behavior_for_each_fault() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "benchmarks"
        / "discovery_recovery_scenarios.v1.json"
    )
    scenarios = json.loads(path.read_text(encoding="utf-8"))

    assert len(scenarios) >= 6
    assert all(item["faults"] for item in scenarios)
    assert all(item["expected_behavior"] for item in scenarios)
