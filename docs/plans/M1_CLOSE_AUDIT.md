# M1 收尾终审（@audit）

日期：2026-07-22  
权威：`MEETING_CONSENSUS_PLAN.md` L2/M1、`COMMITTEE_AUDIT_GATES.md`  
结论：**PARTIAL**  
整体 M1 `READY_FOR_GROK`：**不允许**  
产品状态：**NO-GO**

## 1. 终审结论

M1 主体环境与大部分跨层测试已可稳定复跑，原 `agent.projects` import 已不再阻断 collection，Authority/build-ready-only 门禁也未被削弱。但严格对照 MEETING L2，整体 M1 仍未达到出口：完整 `test_web_discovery.py` 有 14 个 Project orchestration 合同红测，Python 依赖尚未锁定，Docker image/health 未验证，部署 bundle 与 API 身份握手及浏览器 A/B/C 也未完成。

因此可以声明“当前已实现范围的离线主体门禁可复核”，不能声明“整体 M1 READY_FOR_GROK”或“完整 L2 已通过”。L1/L2 局部绿色不等于 L3；production signer、durable ledger、真实 canonical materialization 与 builder preflight 尚未形成生产闭环，产品继续 **NO-GO**。

## 2. 独立复跑结果

统一环境：根目录 `.venv`，Python 3.13.14；`pip check` 返回 `No broken requirements found`。

| 范围 | 独立结果 | 判断 |
|---|---:|---|
| peer/publication/repair/evidence + 3 wiring + agenda + M1 sacred negative | `78 passed in 5.35s` | PASS |
| agent-turn + task-build-plan + control-plane | `205 passed in 17.66s` | PASS |
| 可收集基础 web：`test_web.py` + `test_web_ai_ready.py` | `103 passed in 10.40s` | PASS |
| 完整 `test_web_discovery.py` | `38 passed / 14 failed` | **FAIL** |
| 全仓 collection | `1482 tests collected`，0 error | PASS（仅 collection） |
| frontend | `10 files / 192 passed` | PASS |
| frontend 临时 production build | 12540 modules，build 成功 | PASS（构建机制） |

前端构建使用隔离输出 `.codex_tmp/m1-close-audit-dist`，显式注入 revision `audit-close` 与 UTC build time；主 bundle 可检索到二者，证明 build stamp 机制有效，没有覆盖 `src/agent` 静态目录。

## 3. `agent.projects` 与 14 个红测

Lead 将 `test_web_discovery.py` 中仅用于 Fake 返回对象的不存在模块 `agent.projects` import 改为标准库 `SimpleNamespace`。差分仍保留真实 web 调用；没有添加产品 stub、skip 或 xfail，故 collection 从 1 error 恢复为 0 error，这一迁移可接受。

但下列 14 个测试仍真实失败，入口包括不存在的 `_project_store`、`create_project_record`、`_project_execution_coordinator` 及对应 Project API/workers：

- Project discovery completion coordinator；
- Project create/plan/replan/restart；
- build execution、partial recovery、specialist review；
- approval/rejection、artifact checksum、release replay；
- durable/idempotent Project discovery job 与 interrupted resume。

Lead 的 `-k not (...)` 基线为 `1468 passed / 14 deselected`，只能证明“排除 future Project 子系统后的当前范围”可运行；它不能替代 L2 所要求的 web discovery 全量通过。后续必须二选一并留下明确产品决策：实现真实 Project core/API/workers 后使测试转绿，或把该未提交 future wave 从当前 discovery suite 正式迁出；不得长期靠命令行排除来宣称整体 M1 通过。

## 4. MEETING L2 对照

| L2 出口 | 当前证据 | 结论 |
|---|---|---|
| 同一受支持环境安装并锁定 agents/typer/fastapi/web/frontend | Python 3.13 环境可装且 `pip check` 通过；无跨平台 Python lock，Docker base 未固定 digest | PARTIAL |
| agent-turn/task-build/agenda/control-plane/web/frontend/Authority 全收集并通过 | collection 0 error；主体组合全绿；完整 web discovery 仍 14 failed | **未通过** |
| Docker/Vite/API build/version 可核对 | Vite 可见 build stamp 和 production build 已验证；Docker Desktop daemon 未运行，image/health/API 身份握手未验证 | PARTIAL |
| 浏览器 A/B/C、确认边界、`record.business_completion`、桌面/移动/刷新一致 | 只有自动化合同与 UI 单测；未执行真实部署浏览器/API A–C | **未通过** |
| one-writer、单一 next_decision、numeric option、consultation/open/browse-only | Agenda close 独立 `175 passed`，薄委托未回退 | PASS |

## 5. Sacred / 人为变绿检查

- Python tests 无 `skip/skipif/xfail/pytestmark`；frontend tests 无 `.skip/.only/xit/xtest`。
- peer/publication/repair/evidence、三个 wiring、agenda 与 M1 audit-extra 九个文件 SHA-256 与上一轮审计完全一致。
- 没有恢复 Runner/manifest/candidate/legacy event 即成功；无 issued completion 时 API/UI 继续 fail-closed。
- Lead 明确保留 14 个 future tests 为红，没有伪 stub；本终审也不接受 deselection 作为整体绿色。
- 未发现 dev signer 冒充 production、issuance/replay/no-progress 门禁削弱或案例字符串特判。

## 6. CLOSE 报告复核

- `M1_CLOSE_REPORT.md`：`M1_CLOSE_STATUS: PARTIAL`，如实记录 14 红、Docker daemon、lock 与浏览器缺口；可信。
- `M1_UI_CLOSE_REPORT.md`：UI 子范围 192 tests、build stamp 与 production build 可复核，只对子范围声明 `READY_FOR_GROK`；允许。
- `M1_AGENDA_CLOSE_NOTE.md`：agenda 子范围 175 tests、B 薄委托与 one-writer 边界可复核，只对子范围声明 `READY_FOR_GROK`；允许。

## 7. 最终裁决

允许的声明：

- `M1_CLOSE_STATUS: PARTIAL`；
- 当前实现范围的 Python 3.13/Node 离线主体门禁可交独立复核；
- UI 与 Agenda **子范围**可各自 `READY_FOR_GROK`。

禁止的声明：

- 整体 `M1 READY_FOR_GROK`；
- web discovery 全量通过；
- MEETING L2 已闭合；
- 产品正式可用、merge-ready 或 production GO。

整体 M1 升级前必须让 14 个 Project tests 获得正式范围处置、完成全量无 deselection 回归、生成受审 Python lock、验证 Docker build/health/API build identity，并完成真实浏览器 A/B/C。

OVERALL_M1_READY_FOR_GROK: NO  
PRODUCT_STATUS: NO-GO  
M1_CLOSE_AUDIT: PARTIAL

