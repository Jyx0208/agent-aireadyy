# 通宵自治执行计划（用户授权：跑到明早，无需逐步确认）

Date: 2026-07-22  
Worktree: 当前目录  
权威：`MEETING_CONSENSUS_PLAN.md`、`_GROK_M1_CLOSE_QUALITY.md`  
成功定义：仅 issued build-ready 业务完成；产品默认 **NO-GO** 直至 L3 证据齐。

## 用户意图
- 自动连跑一晚，**不要每步问用户**
- 主管/循环负责质量；假绿否决
- **现在不做完整 Project 子系统实现**（性价比低）
- **升级目标：内测可用**（见 INTERNAL_BETA.md），不是正式 product GO
- 优先：P0 范围诚实 + 主体门禁绿 + RUNBOOK 可启动 + M3 切片

## 硬禁止
- Trellis / Claude / Gemini
- 私钥、.env、dialogue DB 入库或日志明文
- skip/xfail 掩盖红；削弱 peer-audit / issuance / no-progress
- 案例特判（immuno 等）
- 宣称 product GO / merge-ready / 整体 M1 READY（除非 audit 证据真满足 L2 全出口）
- 复制未审 `src/agent/projects` 草稿装绿
- 为 14 红伪造 `_project_store` stub

## 有序 backlog（每轮从最高未完成项做起）

### P0 — M1 范围诚实（先做完再宣称 M1 主体可交）
1. 将 14 个 Project orchestration 测试 **正式迁出** 当前 discovery 门禁主路径：
   - 新建 `tests/test_project_orchestration_future.py`（或等价），把 14 用例移入
   - 文件头文档：future wave；不计入 discovery L2 / M1 主体
   - `test_web_discovery.py` 只保留当前已实现 discovery 合同
   - **不要** pytest.skip 假过；迁出后主 suite 应全绿
2. 更新 `docs/plans/M1_SCOPE.md`：当前 in-scope / future Project
3. 复跑：web_discovery 全量绿；future 文件可红或 `@pytest.mark.future_project` 且 **默认 CI 不跑** 或明确 `-m "not future_project"` 写进报告（二选一写死，推荐 mark + 默认不收集进主门禁命令）

### P1 — 门禁命令固化
- 写 `docs/plans/M1_GATE_COMMANDS.md`：一条「主体门禁」pytest 命令（含 Authority/wiring/agenda/agent-turn/control-plane/可收集 web/materialize）
- 可选：`scripts/run_m1_gate.ps1`

### P2 — M3 最小有价值切片
1. Runner structured v2 proposal 进入同一 `RepairAuthority` admission（若尚未）
2. 或实现 **一个** 安全 adapter：`materialize_evidence` **或** `refresh_auth_context`（只调已有 service、schema/预算/idempotency、失败明确）
3. 测试：reject 越权、no-progress、不假成功
4. `docs/plans/M3_OVERNIGHT_REPORT.md`

### P3 — 若还有时间
- materializer 与 run 路径更多 integration
- UI 诚实回归保持绿
- Docker：仅当 daemon 可用时 build+health；不可用则记 PARTIAL 不装过

### 明确不做（本夜）
- 完整 Project core/API/workers 实现
- 生产 KMS signer 运维上线
- 对外宣称正式可用

## 每轮迭代要求
1. 读本文件 + `TEAM_BOARD.md` 末尾
2. 做 **一项** 可验证进展（优先 P0→P1→P2）
3. 跑相关测试；失败则修，禁止删负例
4. 追加 `TEAM_BOARD.md` 一行 + 可选 `paseo chat post multi-codex "[NIGHT] …"`
5. 更新 `docs/plans/OVERNIGHT_PROGRESS.md`（创建或追加）：时间、做了啥、测试数字、下一项
6. 不找用户确认

## 环境
```powershell
.\.venv\Scripts\python.exe -m pytest -q <targets>
```
若无 .venv：按 M1_CLOSE 报告创建。

## 完成定义（循环 verifier）
- **done=true 仅当**：P0 完成且主体门禁命令全绿，且 M3 至少有一个可测切片 + 报告，且无新增 sacrilege
- 否则 done=false，列出下一项
