# 通宵进度日志

用户授权自动连跑，无需逐步确认。

## 状态
- started: 2026-07-22（编排启动）
- loop_id: `2810affc`（name: discovery-overnight）
- max: 12 iterations / 10h
- next: 完成定义已满足；后续仅可做 P3 非阻塞增强，产品仍 NO-GO

## 迭代记录
- 2026-07-23T00:20:00+08:00 P0 完成：14 个 Project orchestration 合同已物理迁至 `tests/test_project_orchestration_future.py`，统一标记 `future_project`；`tests/test_web_discovery.py` 保留 38 个当前 discovery 合同；`pyproject.toml` 默认排除 future marker；`M1_SCOPE.md` 锁定 current/future 边界。验证：web discovery **38 passed**；全仓收集 **1468/1482 collected, 14 deselected, 0 errors**。无 skip/xfail/stub，产品仍 NO-GO。下一项：P1 主体门禁命令。
- 2026-07-23T00:32:00+08:00 P1 完成：新增 `M1_GATE_COMMANDS.md` 与 `scripts/run_m1_gate.ps1`，固化 Authority/wiring/agenda/agent-turn/task-build/control-plane/materialization/web 单一主体门禁。验证：脚本 **350 passed**。该绿灯仅代表离线 M1 主体，不宣称 L3 或产品 GO。下一项：P2 structured v2 proposal。
- 2026-07-23T00:55:00+08:00 P2 完成：Runner 显式 structured v2 proposal 进入同一 `RepairAuthority` admission；普通文本/非 v2 envelope 不获 repair 权限。新增负例验证未知 capability dispatch 前拒绝、no-progress 只产生 incomplete 且不假成功。focused **54 passed**；更新后的主体门禁 **352 passed**。报告：`M3_OVERNIGHT_REPORT.md`。完成定义满足；未实现 adapters/Project、未削弱 Authority，产品继续 NO-GO。
- 2026-07-23 通宵续跑 P3 非阻塞复验：按 `scripts/run_m1_gate.ps1` 重跑规范主体门禁，**354 passed in 38.72s**；同步 `M1_GATE_COMMANDS.md` 的过期计数。P0→P2 完成定义继续满足；未改生产代码、未扩展 Project、未削弱 Authority，产品继续 **NO-GO**。下一项：停止扩张，保留给 peer/L3/Internal Beta 后续验收。

## V2
- 2026-07-23 V2 M3 安全 adapter：实现 `materialize_evidence` 与 `refresh_auth_context` 的 fail-closed dispatch（`openai_agents.py`），只操作已有 run state / EvidenceStore，不发明 observation、不 mint 凭据、不签发 build-ready。`AgentRunRecord.auth_refresh_attempts` 计入 refresh 上限。wiring 测试 4 新增负例/正例；`scripts/run_m1_gate.ps1` **358 passed in 38.57s**。产品继续 **NO-GO**；未实现 Project full subsystem、生产 signer、L3。
- 2026-07-23 V2 materializer integration：在既有 M2 materializer + M3 adapters 之上，为真实 run 路径增加 **substantial integration 覆盖**（`tests/test_discovery_build_ready_materialization.py` +7）：
  1. `materialize_evidence` 提升 inventory → 预检 pending 时 package 仍 blocked → preflight ready 后 audit 物化 package，且全程无 signer / 无 `succeeded` / 无 success UI；
  2. 仅 promote 不产生 package / business_completion；
  3. project-scope observation 不能冒充 file `builder_file_entry`；
  4. soft constraint 缺 observation 不单独阻塞；
  5. 已有 package 时二次 audit 不重算/不发明；
  6. 磁盘 corrupt manifest fail-closed；
  7. builder preflight not ready 即使 evidence 齐全也 blocked。
  验证：`scripts/run_m1_gate.ps1` **365 passed in 43.50s**。未改 Authority 语义、未接 Project、未 product GO。

## 如何查看（用户明早）
```text
paseo loop ls
paseo loop inspect 2810affc
paseo loop logs 2810affc
docs/plans/OVERNIGHT_PROGRESS.md
docs/plans/TEAM_BOARD.md
```
