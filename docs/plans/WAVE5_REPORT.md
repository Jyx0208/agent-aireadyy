# Wave 5 H8 实施报告

## 结论

Wave 5 的数据模型与纯议程引擎已实现，但尚未接入运行时
`app.py::_discovery_critical_decision_agenda`。因此当前不能宣称端到端完成。

`WAVE5_STATUS: PARTIAL`

## TaskProfile / agenda 设计

- `TaskProfile` 新增 `critical_agenda` 数据；每个 `CriticalAgendaItem` 明确：
  `trigger_conditions`、`blocks_build_ready`、`decision_variables`、
  `target_fields`、`required_evidence`、优先级、理由与只读来源。
- 议程分成 common、training 与 chimeric pack。通用训练 TaskProfile 默认组合
  common + training；chimeric profile 再追加任务专属 pack。
- `src/agent/discovery/agenda.py` 是无网络、无写入的通用解释器：
  `build_critical_decision_agenda()` 返回按 `(-priority, id)` 排序的未解决项；
  `next_critical_decision()` 每次只返回一个最高优先项；
  `agenda_for_manager()` 保留旧 Manager 所需的 `critical`、`target_fields`、
  `source` 字段。引擎不产出 strategy patch，Manager 仍是唯一 writer。
- 触发逻辑由声明式 operator 求值，不按任务 ID 建巨型 if 树；`other` 保留通用
  training fallback，未知任务 fail-soft 到 common agenda。

## H8 行为

### Chimeric 优先级

`chimeric_label_feasibility`：

- priority = 88，`blocks_build_ready = true`；
- 决策变量为 `label_provenance` 与 `relabel_tolerance`；
- 通过 `scientific_constraints` 承载，避免新增未获准的一等 strategy 字段；
- 所需仓库证据包括 multi-peptide assignment provenance、q-value/FDR、
  isolation-window metadata、以及可供 relabel 的 raw/peak-list；
- optional `labeling_compatibility` priority = 58 且不阻塞 build-ready。

因此 chimeric 的标签可行性稳定排在 optional labeling 之前。已有无关
`scientific_constraints` 不会误把该项视为已解决。

### Browse-only / open

- `browse_only` 只加载 common agenda，不加载 acquisition、species、label feasibility
  或 optional labeling 等训练议程。
- `species_policy=open`、`quota_flexibility=open_ended`、
  `labeling_strategy=any` 视为已解决；显式记录在 `resolved_fields` 的 unknown/open
  选择也不会被重新提问。
- chimeric 的 `label_provenance` / `relabel_tolerance` 若以 strength=`open` 的
  scientific constraint 明确保留开放，同样视为已解决。
- `next_critical_decision()` 只给一个动态最高优先项，不恢复 Q1–Q10。

## app.py 接线状态与风险

本波未修改 `src/agent/web/app.py`。该文件已有 @lead 的未提交 request-timeout
改动，按协作约定不抢所有权。

当前风险：运行时仍调用旧的 `_discovery_critical_decision_agenda` 条件树，所以新
TaskProfile agenda 尚未端到端生效；尤其 chimeric pack 与新的 open 语义目前只在
纯函数 seam 可用。后续只需把旧函数体薄接到
`agent.discovery.agenda.agenda_for_manager(...)`，保留原签名与 legacy 输出契约，
再复跑既有 single-writer、numeric option、confirmation 测试。完成该接线前状态保持
PARTIAL。

## 测试

无网络测试：

```text
E:\anaconda\python.exe -m pytest -q tests/test_discovery_agenda.py
8 passed in 1.58s
```

覆盖：chimeric 优先级与数据契约、open scientific constraints、无关 constraint、
browse-only、显式 open、resolved_fields、动态单问题、legacy Manager payload。

`tests/test_discovery_task_build_plan.py` 已增加 TaskProfile 数据契约测试，但当前环境
无法收集该文件：默认 Anaconda 环境缺 `typer`；仓库 `.venv` 的 Typer/Pydantic
安装又缺 `annotated_doc` / `annotated_types`。合并命令因此在 collection 阶段失败，
不是断言失败。本波按“无网络”要求未安装依赖。依赖完整环境中仍需复跑：

```text
python -m pytest -q tests/test_discovery_task_build_plan.py tests/test_discovery_agenda.py
```

## 文件

- `src/agent/discovery/task_profiles.py`
- `src/agent/discovery/agenda.py`
- `docs/discovery-agent-guidance.md`（仅议程契约小节）
- `tests/test_discovery_task_build_plan.py`
- `tests/test_discovery_agenda.py`
- `docs/plans/WAVE5_REPORT.md`

未修改 frontend、repair、publication、Trellis，也未加入 immuno 案例特判。
