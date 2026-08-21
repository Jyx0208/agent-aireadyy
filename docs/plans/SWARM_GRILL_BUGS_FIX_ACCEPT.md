# Grill bugs fix — supervisor accept

Date: 2026-07-24  
Room: `grill-bugs-fix`  
Worktree: `benchmark-review-planning`

## Success criteria

| # | Criterion | Result |
|---|-----------|--------|
| 1 | denovo+dda + empty species → agenda has `generalization_scope` | **PASS** |
| 2 | update_strategy + broken next_decision → full synthesized next question | **PASS** |
| 3 | User never sees raw「下一问结构不完整已明确忽略」 | **PASS** (string not product copy; friendly repair path) |
| 4 | pytest green for agenda + agent_turn | **PASS** (`184 passed` agent_turn + agenda) |

## Code

- `src/agent/discovery/task_profiles.py` — `generalization_scope` triggers on **species missing only** (not AND species_policy missing).
- `src/agent/web/app.py` — `_synthesize_discovery_next_decision_from_agenda`; repair when `decision_contract_error` or `update_strategy`+patch with empty next_decision (not after intentional redundant clear / chat); friendly contract-noise clarify; grill guidance not to skip species.
- Tests updated/added in `tests/test_discovery_agenda.py`, `tests/test_discovery_agent_turn.py`.

## Verify locally

```bash
.venv/Scripts/python.exe -m pytest tests/test_discovery_agenda.py tests/test_discovery_agent_turn.py -q
```

## Deploy (operator)

```text
# Local web (if running): restart start-web.ps1 / uvicorn process after pull.
# Lab (from launch plan): scp app.py + task_profiles.py → 172.16.13.5; docker restart service.
```

Supervisor **ACCEPT** for P0+P1. P2 (numeric fail → NL continue) not required if not landed.
