from pathlib import Path

import httpx
import pytest

from agent.control_plane.budget_governor import BudgetGovernor, RepositoryRequestBudgetExceeded
from agent.control_plane.models import AgentBudget, AgentRunRecord, DynamicBudgetLimits
from agent.control_plane.store import AgentRunStore
from agent.pride.client import PrideClient
from agent.repositories.metering import meter_repository_requests


def test_repository_meter_records_each_http_attempt(monkeypatch) -> None:
    observed: list[tuple[str, str]] = []
    response = httpx.Response(
        200,
        json={"_embedded": {"projects": []}},
        request=httpx.Request("GET", "https://x.test"),
    )
    client = PrideClient()
    monkeypatch.setattr(client._client, "get", lambda *args, **kwargs: response)
    with meter_repository_requests(lambda repository, operation: observed.append((repository, operation))):
        client.search_projects("human plasma")
    client.close()
    assert observed == [("pride", "search_projects")]


def test_repository_meter_blocks_before_network_dispatch_at_hard_limit(monkeypatch, tmp_path: Path) -> None:
    store = AgentRunStore(tmp_path / "state.sqlite")
    run = store.save_run(
        AgentRunRecord(
            run_id="metered_run",
            workflow="discovery",
            status="running",
            dynamic_budget_enabled=True,
            dynamic_limits=DynamicBudgetLimits(max_repository_requests=1),
            budget=AgentBudget(max_turns=10, max_tool_calls=20),
        )
    )
    governor = BudgetGovernor(store, run.run_id)
    calls = 0
    response = httpx.Response(200, json=[], request=httpx.Request("GET", "https://x.test"))
    client = PrideClient()

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return response

    monkeypatch.setattr(client._client, "get", fake_get)
    with meter_repository_requests(governor.record_repository_request):
        client.search_projects("first")
        with pytest.raises(RepositoryRequestBudgetExceeded, match="hard_repository_request_limit"):
            client.search_projects("second")
    client.close()
    assert calls == 1
    stored = store.load_run(run.run_id)
    assert stored is not None
    assert stored.dynamic_usage.repository_requests == 1
    assert stored.search_stopped is True
    assert stored.search_stop_reason == "hard_repository_request_limit"
