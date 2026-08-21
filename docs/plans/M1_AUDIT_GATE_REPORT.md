# M1 门禁监督与负例守护报告（@audit）

日期：2026-07-22  
权威：`MEETING_CONSENSUS_PLAN.md` M1/L1–L3、`COMMITTEE_AUDIT_GATES.md`  
结论：**PARTIAL；不允许整体 M1 宣称 `READY_FOR_GROK`。**

## 1. 审计结论

M1 主体环境已经明显前进：Python 3.13 完整 extras 可安装，Authority、wiring、agenda、agent-turn、task-build-plan、control-plane、可收集 web 与 frontend 门禁均可运行；本轮暴露的 public-record 假完成和旧 `completed` 断言也已按 build-ready-only 修正。

但 M1 出口尚未闭合：全仓 collection 仍因仓库缺少 `agent.projects` 失败，Python 无跨平台 lock，Docker base 未固定 digest，前端部署 build/version 身份和真实浏览器/API L2 尚未完成。因此只能声明“主体门禁可运行、M1 部分完成”，不能声明 M1 全量可交 Grok，也不能外推为产品正式可用。

当前产品仍为 **NO-GO**。L1 或 L2 局部绿色不等于 L3 production GO；production signer、durable ledger、真实 canonical materialization 与 builder preflight 仍是后续硬门禁。

## 2. 最终独立复跑

统一环境：根目录 `.venv`，Python 3.13.14；`pip check` 为 `No broken requirements found`。

| 范围 | 结果 | 判断 |
|---|---:|---|
| peer-audit + publication + repair + evidence + 3 个 wiring + agenda | `77 passed in 6.23s` | PASS |
| 上述集合 + 本轮新增 M1 public-record sacred negative | `78 passed in 6.68s` | PASS |
| agenda + agent-turn + task-build-plan | `175 passed in 14.78s` | PASS |
| control-plane（修正旧假成功断言后） | `38 passed in 23.92s` | PASS |
| 可收集 web：`test_web.py` + `test_web_ai_ready.py` | `103 passed in 18.56s` | PASS |
| frontend 全量 | `9 files / 191 passed` | PASS |
| 全仓 Python collection | `1430 tests collected, 1 error` | **未通过** |

唯一 collection error：

```text
tests/test_web_discovery.py
ModuleNotFoundError: No module named 'agent.projects'
```

这不是可用 skip 掩盖的可选依赖；会议共识明确要求 web discovery 可收集。仓库当前也不存在 `src/agent/projects.py`，故该项阻断 M1 完整出口。

## 3. 人为变绿检查

### 3.1 skip / xfail

- 对 `tests/**/*.py` 搜索 `pytest.mark.skip/skipif/xfail`、`pytest.skip/xfail`、`pytestmark`、`unittest.skip/expectedFailure`：**无命中**。
- 对 frontend tests 搜索 `describe/it/test.skip`、`.only`、`xit/xtest`：**无命中**。
- 最终 Python 命令使用 `--strict-markers --runxfail -ra`，未出现被隐藏的 skip/xfail。

### 3.2 sacred negatives 未削弱

首轮与最终复跑之间，以下八个门禁文件 SHA-256 完全一致：peer-audit、publication、repair、evidence、三个 wiring 文件与 agenda。没有通过删除或改弱 sacred tests 消红。

四个 control-plane 旧断言从 `result.status == "completed"` 改为 `blocked` 是合同纠偏，不是放宽：对应 fixture 只有 Runner/候选/manifest 进展，没有 issued build-ready completion；新断言同时要求 `business_completion.succeeded is False`、`success_ui_allowed is False`，并禁止 `repair_succeeded` / `build_ready_succeeded` 事件。

### 3.3 issuance / 假成功

- Authority focused 门禁继续覆盖 canonical package digest、signed inventory/package substitution、hard unknown/duplicate/overflow、metric/completion replay、private per-attempt nonce、no-progress=2 和 dev signer default-off。
- `src/agent/control_plane/openai_agents.py` 的 run-level completion 仍要求 `business_completion_allows_success(...)`；manifest selection 只是中间动作，Runner 或 `manifest_selected` 本身不签发业务成功。
- 本轮新增 `tests/test_discovery_m1_audit_extra.py` 首先稳定复现：`business_completion=None` 的 public record 被错误投影为 `status=completed`。修复后该负例为 `1 passed`：API 缺 issued decision 时降为 blocked；frontend 同样在 decision 缺失时 fail-closed。
- frontend 收紧后暴露一个旧 fixture；所有者没有回退 gate，而是为确实需要成功的 fixture 补齐完整 Authority v2 envelope，最终全量 `191 passed`。

未发现以 dev signer、Runner、HTTP/transport finished、候选数量、legacy event 或案例字符串分支制造业务成功。

## 4. 角色输出复核

- `M1_ENV_BASELINE_REPORT.md`：如实标记 `M1_STATUS: PARTIAL`，记录缺 `agent.projects`、无 Python lock 与 Docker 未实构建；结论可信。
- `M1_UI_BASELINE_REPORT.md`：前端测试/build 绿，但 build stamp、静态 bundle 身份与浏览器/API L2 未闭合，标记 `M1_UI_STATUS: PARTIAL`；结论可信。
- `M1_AGENDA_BASELINE_REPORT.md`：agenda 子范围 `175 passed`，只对子范围声明 `READY_FOR_GROK`，并明确整体 M1/产品仍受阻；允许该**子范围**声明。

## 5. MUST_FIX / 退出条件

整体 M1 获准 `READY_FOR_GROK` 前必须：

1. 恢复或正确迁移 `agent.projects` 合同，使 `tests/test_web_discovery.py` 可收集；不得伪 stub、skip 或删测试。
2. 全仓 `pytest --collect-only -q` 达到 0 error，并补跑全仓无 fail/skip/xfail 基线。
3. 建立可审核的 Python 依赖锁和更确定的 Docker base；不得把本机 Windows `pip freeze` 冒充 Linux lock。
4. 闭合 frontend build/version 身份与实际部署静态 bundle 的核对；M1/L2 浏览器/API 项未完成时必须继续标 PARTIAL。
5. 保持本报告新增 public-record 负例、peer/property/replay/no-progress 与 build-ready-only 门禁启用。

允许 @lead 宣称：`M1 主体环境门禁可运行；M1_STATUS: PARTIAL`。  
不允许 @lead 宣称：`M1 READY_FOR_GROK`、全仓测试已完整通过、产品正式可用或 production GO。

M1_AUDIT_STATUS: PARTIAL

