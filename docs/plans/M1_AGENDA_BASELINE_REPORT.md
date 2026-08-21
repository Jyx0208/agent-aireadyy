---
document: M1_AGENDA_BASELINE_REPORT
authority: docs/plans/MEETING_CONSENSUS_PLAN.md + docs/plans/WAVE_B_AGENDA_WIRE_REPORT.md
scope: M1 对话/议程门禁
business_completion: build-ready only
status: READY_FOR_GROK
---

# M1 对话 / 议程基线报告

## 1. 结论

M1 的对话/议程子范围已在隔离的 Python 3.13 完整依赖环境封板：

- `tests/test_discovery_agenda.py` 全绿；
- `tests/test_discovery_agent_turn.py` 全文件可收集并 160 passed；
- `tests/test_discovery_task_build_plan.py` 与 agenda/agent-turn 合并为 175 passed；
- B 薄委托、browse-only 分流、chimeric 优先、显式 open 不重问、动态单问题、
  one-writer、numeric option 与 confirmation 边界均未回归；
- 不需要修改 `src/agent/discovery/agenda.py`、`task_profiles.py` 或 `app.py`。

本报告只声明 agenda 子范围可交 Grok 复核。整个产品仍受 M1 control-plane legacy
断言、web collection、M2–M5 与生产信任根限制，不是产品正式可用、merge-ready 或
production GO。

`M1_AGENDA_STATUS: READY_FOR_GROK`

## 2. 环境

为避免修改共享的旧 Python 3.12 `.venv`，本轮在工作树内创建隔离环境：

```text
.codex_tmp/m1-agenda-py313
Python 3.13.14
pytest 8.4.2
openai-agents 0.18.1
fastapi 0.139.2
typer 0.27.0
```

安装命令：

```powershell
python3.13 -m venv .codex_tmp/m1-agenda-py313
.\.codex_tmp\m1-agenda-py313\Scripts\python.exe -m pip install -e '.[web,dev]'
```

这补齐了此前 `agents`、`typer`、`fastapi`、`annotated_doc` 与
`annotated_types` 的 collection 空档。没有将环境目录、secrets 或凭据加入源码。
Docker Desktop daemon 当前未运行，但本子范围不依赖容器或网络服务。

## 3. 门禁行为

### 3.1 B 委托

既有 `test_web_critical_agenda_is_a_thin_profile_engine_delegate` 通过，确认
`app.py::_discovery_critical_decision_agenda(...)` 将 snapshot、gap report 与
resolved fields 原样交给 `agenda_for_manager(...)`，不恢复第二套条件树。

### 3.2 Browse-only

新增 `test_web_browse_only_agenda_does_not_load_training_questions`：当 objective、
task、horizon 与 scale 已解决时，即使 acquisition/species/labeling 为空，
`browse_only` 也不加载训练 agenda。它不会被 DDA、generalization 或标签问题阻塞。

### 3.3 Chimeric 优先级

既有主路径测试通过：`chimeric_label_feasibility` 保持 critical，并稳定排在 optional
`labeling_compatibility` 之前；目标仍通过 `scientific_constraints` 表达，不在
`app.py` 增加任务字符串分支。

### 3.4 Open 不重问

新增 `test_web_explicit_open_training_choices_are_not_reasked`：
`quota_flexibility=open_ended`、`acquisition_mode=unknown`、
`species_policy=open`、`labeling_strategy=any` 在主路径不重新产生问题。
既有 resolved-fields 与 open scientific-constraint 纯测试继续通过。

### 3.5 动态单问题与毕业边界

- `next_critical_decision()` 仍只返回最高优先的一项，不出现 Q1–Q10；
- Manager 仍是唯一 strategy writer，Advisor/critic 不获得写权限；
- unresolved critical search scale 仍阻止 confirmation；
- agenda 清空只表示 `ready_to_confirm`，strategy confirmed 只授权执行；
- candidates、review、repair delta 与 agenda ready 均不构成 business completion；
- 唯一业务毕业仍为 Authority-issued build-ready decision。

本轮未向 agenda payload 增加 `succeeded`、`completed` 或任何 publication 权限。

## 4. 测试结果

### 4.1 纯 agenda 基线

```powershell
& 'E:\anaconda\python.exe' -m pytest -q tests/test_discovery_agenda.py
```

```text
8 passed in 1.35s
```

### 4.2 B/agenda focused web 门禁

以下六项包含 search-scale priority、B 委托、browse-only、open、chimeric 与
confirmation：

```text
6 passed in 13.34s
```

### 4.3 完整 agent-turn

```powershell
& '.\.codex_tmp\m1-agenda-py313\Scripts\python.exe' -m pytest -q `
  tests/test_discovery_agent_turn.py
```

```text
160 passed in 13.72s
```

### 4.4 最终合并

```powershell
& '.\.codex_tmp\m1-agenda-py313\Scripts\python.exe' -m pytest -q `
  tests/test_discovery_agenda.py `
  tests/test_discovery_task_build_plan.py `
  tests/test_discovery_agent_turn.py
```

```text
175 passed in 18.11s
```

相关五个 Python 文件 `py_compile` 通过；目标 diff 的 `git diff --check` 退出码为 0。
PowerShell 仅报告现有 LF→CRLF checkout 警告，无 whitespace error。

## 5. 变更与复核

本轮 production diff 为零。只在 `tests/test_discovery_agent_turn.py` 新增两个跨层
行为测试，并新增本报告/协作消息。

本地双轴复核：

- Standards：新增测试沿用相邻 direct-call 风格，名称描述行为，无网络、无 mock
  内部实现、无案例特判；无发现。
- Spec：覆盖会议要求的 browse-only 与 open 主路径缺口，保留 B/chimeric/confirmation
  既有断言；未扩大 mutation 或 success 权限；无发现。

共享 worktree 中同一测试文件还包含 Wave B 与 timeout 的前序未提交变更，因此本轮
没有创建混合所有权 commit，也没有 reset/clean/stage 他人工作。

## 6. 外部 M1 风险

- @audit 的更大 M1 组合目前报告 278 passed / 4 failed；四项为 control-plane legacy
  `completed` 断言，与本 agenda 子范围无关，禁止通过放松 build-ready 门修绿。
- `tests/test_web_discovery.py` 仍因缺 `agent.projects` 无法 collection。
- 完整依赖环境本轮为隔离、可复现的开发验证环境，不替代依赖锁、CI、Docker
  build/version handshake 或生产基础设施。
- M2 真实 materialization、M4 production signer/durable ledger 与 M5 staged E2E
  尚未完成，产品继续 NO-GO。

M1_AGENDA_STATUS: READY_FOR_GROK
