"""Deep Discovery Runner facade.

Product entrypoints (Web job API, CLI) should call through this module so runtime
selection, cancel hooks, and result packaging share one interface.

Today this is a thin adapter over the OpenAI Agents control-plane runner plus
workflow/agentic helpers. Further deepening should move Web orchestration logic
here rather than growing app.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal

from agent.control_plane.models import AgentBudget, DynamicBudgetLimits, OpenAIAgentsDiscoveryResult
from agent.control_plane.openai_agents import run_openai_agents_discovery
from agent.discovery.memory import DiscoveryMemory
from agent.discovery.models import DatasetRequest
from agent.discovery.search_environment import DiscoverySearchEnvironment

DiscoveryRuntime = Literal["openai_agents", "workflow", "agentic"]


def run_agents_discovery(
    *,
    prompt: str,
    request: DatasetRequest,
    output_dir: str | Path,
    task_type: str | None = None,
    memory: DiscoveryMemory | None = None,
    budget: AgentBudget | None = None,
    mode: Literal["single_agent", "multi_agent"] = "multi_agent",
    dynamic_limits: DynamicBudgetLimits | None = None,
    search_environment: DiscoverySearchEnvironment | None = None,
    discovery_func: Any = None,
    llm_config: dict[str, str] | None = None,
    run_id: str | None = None,
    project_id: str | None = None,
    state_db: str | Path | None = None,
    session_db: str | Path | None = None,
    event_callback: Callable[[Any], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    stream_events: bool = False,
    resume_existing: bool = False,
) -> OpenAIAgentsDiscoveryResult:
    """Run the production default agents discovery path behind one interface."""
    return run_openai_agents_discovery(
        prompt=prompt,
        request=request,
        output_dir=output_dir,
        task_type=task_type,
        memory=memory,
        budget=budget,
        mode=mode,
        dynamic_limits=dynamic_limits,
        search_environment=search_environment,
        discovery_func=discovery_func,
        llm_config=llm_config,
        run_id=run_id,
        project_id=project_id,
        state_db=state_db,
        session_db=session_db,
        event_callback=event_callback,
        should_cancel=should_cancel,
        stream_events=stream_events,
        resume_existing=resume_existing,
    )
