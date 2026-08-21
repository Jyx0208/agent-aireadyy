---
document: WAVE_B_AGENDA_WIRE_REPORT
authority: docs/plans/LOCKED_PLAN.md Wave 5 + docs/plans/WAVE5_REPORT.md
scope: TaskProfile agenda 到 Dialogue Manager 主路径的薄接线
network: offline
business_completion: build-ready only
status: READY_FOR_GROK
---

# Wave B 议程接线报告

## 1. 结论

Wave 5 的纯议程引擎现已接入真实 Dialogue Manager 路径。`src/agent/web/app.py::_discovery_critical_decision_agenda` 保留原签名与 docstring，但函数体已从旧的 task/acquisition/species/labeling Python 条件树收敛为对 `agent.discovery.agenda.agenda_for_manager(...)` 的薄委托。

本波没有修改 Manager 的写入职责、turn 解析、option contract、confirmation、Authority Plane 或 build-ready 毕业门。Manager 仍是唯一 strategy writer；agenda 只提供按优先级排序的未解决决策数据，Manager 每轮仍只产生一个动态 `next_decision`。

当前只声明 `READY_FOR_GROK`，不声明 merge-ready。

## 2. 变更文件

- `src/agent/web/app.py`
  - 新增 `from agent.discovery.agenda import agenda_for_manager`；
  - 只替换 `_discovery_critical_decision_agenda(...)` 函数体；
  - 传递原始 `intent_snapshot`、`gap_report` 与 `resolved_fields`；
  - 未修改同文件现有 timeout、Wiring A、repair event 或其它脏 diff。
- `tests/test_discovery_agent_turn.py`
  - 新增薄委托契约测试，验证三参数原样传递且返回 Manager payload 不被二次改写；
  - 新增 chimeric 主路径测试，验证 `chimeric_label_feasibility` 高于 optional `labeling_compatibility`，并保留 `critical` 与 `target_fields` legacy 键。
- `docs/plans/TEAM_BOARD.md`
  - 追加开始与完成/复审消息。

`src/agent/discovery/agenda.py` 与 `task_profiles.py` 本波未修改；直接复用已通过 Wave 5 纯函数测试的数据模型和解释器。

## 3. 锁定行为保持

### 3.1 Manager 唯一 writer

`agenda_for_manager` 是纯读取/序列化函数，不产生 strategy patch、不写 session、不调用模型或网络。原 Dialogue Manager 继续拥有 assistant reply、`next_decision` 与 validated patch 的唯一写入权。

### 3.2 动态单问题

没有恢复 Q1–Q10 或固定问卷。TaskProfile engine 只生成当前未解决项及优先级；现有 Manager turn contract 继续从 agenda 中选择一个最相关决策形成单一 `next_decision`。纯引擎的 `next_critical_decision()` 仍明确只返回最高优先的一项。

### 3.3 Open 与 browse-only

既有纯测试继续验证：

- `species_policy=open`、`quota_flexibility=open_ended`、`labeling_strategy=any` 为已解决；
- `resolved_fields` 中显式开放的 unknown 不会被重复追问；
- chimeric scientific constraint 的 strength=`open` 可明确解决 label provenance/relabel tolerance；
- `browse_only` 只加载 common agenda，不受训练 acquisition/species/labeling 议程阻塞。

### 3.4 Chimeric 优先级

主路径现在消费 TaskProfile 中的数据化优先级：

- `chimeric_label_feasibility`：priority 88、`critical=true`、目标字段 `scientific_constraints`；
- 决策变量：`label_provenance`、`relabel_tolerance`；
- optional `labeling_compatibility`：priority 58、`critical=false`。

因此 label provenance/relabel tolerance 稳定先于 optional labeling，且没有在 `app.py` 新增 chimeric 或其它领域字符串分支。

### 3.5 Authority 不变

本波没有修改 repair、publication、issuance、business completion 或 UI 成功语义。唯一业务毕业仍为 issued build-ready；agenda resolved/ready 只表示对话决策充分，不表示 discovery 已交付。

## 4. 测试与检查

### 4.1 纯 agenda suite

```powershell
& 'E:\anaconda\python.exe' -m pytest -q tests/test_discovery_agenda.py
```

```text
8 passed in 1.16s
```

覆盖 chimeric 优先级、open constraints、无关 constraint、browse-only、显式 open、resolved fields、动态单项和 Manager legacy payload。

### 4.2 Authority/Wiring 非回归

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_wiring_repair_authority.py `
  tests/test_discovery_wiring_dev_publication.py `
  tests/test_discovery_wiring_publication_to_record.py `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_authority_properties.py `
  tests/test_discovery_evidence_store.py
```

```text
93 passed in 3.22s
```

### 4.3 编译与环境限制

以下文件 `py_compile` 通过：

- `src/agent/web/app.py`；
- `src/agent/discovery/agenda.py`；
- `src/agent/discovery/task_profiles.py`；
- `tests/test_discovery_agenda.py`；
- `tests/test_discovery_agent_turn.py`。

`git diff --check` 对本波触点通过。

当前 `E:\anaconda\python.exe` 环境：

- `tests/test_discovery_agent_turn.py` collection 缺 `openai-agents`（`ModuleNotFoundError: agents`）；
- `tests/test_discovery_task_build_plan.py` collection 缺 `typer`。

因此新增两条 agent-turn 主路径测试尚需在完整依赖环境复跑。没有用 skip/xfail 掩盖依赖缺失；测试文件自身已通过编译。

## 5. 风险与未做项

- 完整依赖环境必须补跑：

  ```powershell
  python -m pytest -q tests/test_discovery_agent_turn.py tests/test_discovery_task_build_plan.py tests/test_discovery_agenda.py
  ```

- 本波未改 TaskProfile 数据或 agenda evaluator；若 Grok 发现纯引擎规则问题，应在其所有权文件中修复，不应把条件树重新塞回 `app.py`。
- 共享 worktree 中 `app.py`、agent-turn tests 与 agenda 文件含前序未提交意图；本轮未 stage/commit，避免把他人历史改动打包。
- 未使用网络、live PRIDE、外部模型、Trellis 或 secrets。

WAVE_B_STATUS: READY_FOR_GROK
