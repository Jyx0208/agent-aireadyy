# M1 主体门禁命令

日期：2026-07-23

M1 主体只有一条规范 Python 门禁命令。它覆盖 Authority、publication/repair/evidence、wiring、agenda、agent-turn、task-build plan、control-plane、build-ready materialization 与当前 discovery Web 合同：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_authority_properties.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_evidence_store.py `
  tests/test_discovery_wiring_dev_publication.py `
  tests/test_discovery_wiring_publication_to_record.py `
  tests/test_discovery_wiring_repair_authority.py `
  tests/test_discovery_agenda.py `
  tests/test_discovery_agent_turn.py `
  tests/test_discovery_task_build_plan.py `
  tests/test_control_plane.py `
  tests/test_discovery_build_ready_materialization.py `
  tests/test_discovery_m1_audit_extra.py `
  tests/test_web_discovery.py
```

等价的仓库脚本为：

```powershell
.\scripts\run_m1_gate.ps1
```

`pyproject.toml` 默认设置 `-m "not future_project"`。因此该门禁不会执行尚未实现的 Project orchestration 产品波次，但测试仍可收集且没有 skip/xfail。Future 合同的诚实红测命令见 `M1_SCOPE.md`。

其中 `test_discovery_m1_audit_extra.py` 固定验证缺少 issued `business_completion` 时，public API 不得把 transport/Runner 的 `completed` 投影成业务成功。

当前基线预期为 `365 passed`（2026-07-23 V2：M3 adapters + materializer integration tests）。通过本门禁仅表示当前 M1 离线主体合同通过；不表示 Project subsystem 已实现，不表示 L3 或产品 GO。唯一业务成功仍须为 Authority issued build-ready。
