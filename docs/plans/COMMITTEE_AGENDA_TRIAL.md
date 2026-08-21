# 委员会分册：B 接线后的科学议程试跑

## 1. 当前基线

B 接线已经完成并经 Grok 验收为 PASS：
`app.py::_discovery_critical_decision_agenda(...)` 现在薄委托
`agent.discovery.agenda.agenda_for_manager(...)`。运行时不再在 `app.py` 维护
task/acquisition/species/labeling 条件树，而是消费 `TaskProfile.critical_agenda`
中的声明式数据。

这个接线只改变“当前有哪些未解决决策、优先级如何”的来源，不改变职责边界：

- agenda 是只读 priority/readiness guard，不是固定问卷；
- Dialogue Manager 仍是唯一 strategy writer；
- Advisor 仍只读，不能修改策略或直接替用户确认；
- 每轮最多呈现一个动态 `next_decision`，不恢复 Q1–Q10；
- 用户咨询、主动修订或一次提供多个答案时，Manager 可以先响应当前意图，再重新计算议程。

## 2. B 接线后，对话应如何表现

### 2.1 正常节奏

1. Manager 从用户原话提取已经明确的目标、任务、交付终点、范围和开放选择。
2. server 根据当前 snapshot、`resolved_fields` 与 TaskProfile 生成未解决 agenda。
3. Manager 选择影响最大的一项，给出一个推荐、简短任务理由和可执行选项；不把完整 agenda 一次倾倒给用户。
4. 用户选择数字、选项 ID、标签或自由文本后，只应用该选项已经声明的 patch；不得顺手加入相邻默认值。
5. 显式 `open`、`any`、`open_ended` 或已记录的开放选择不再追问。
6. critical 项清空后，可以进入 `ready_to_confirm`；确认后才允许开始 discovery。

对话的正确体感应是“围绕这个任务解决下一处真正依赖”，而不是“填写一张通用表”。例如用户已经说明项目数量，就不再问规模；用户正在询问某选项利弊时，先解释而不是强迫提交策略更新。

### 2.2 Chimeric 任务

对于 `chimeric_interpretation`，`chimeric_label_feasibility` 的 priority 为 88，
高于 optional `labeling_compatibility` 的 58。于是 Manager 应先解决：

- 是否要求已有、可追溯的 multi-peptide assignment；
- 若仓库没有可复用标签，是否接受从 raw/peak-list 下游 relabel；
- 后续需要检索哪些证据：assignment provenance、q-value/FDR、isolation window、
  raw/peak-list 可用性。

这里应向用户询问“精确标签优先还是接受后续重标”的研究取舍，不应让用户猜某个 PRIDE 项目的 isolation window 或文件清单。仓库事实属于执行后的 evidence retrieval。

### 2.3 失败表现

以下均应视为试跑失败信号：

- 同一回复同时问标签来源、物种、仪器和项目数；
- 出现“第 3/10 题”等固定问卷语言；
- browse-only 被 DDA、训练标签或 relabel tolerance 阻塞；
- 用户明确说开放后仍重复询问同一维度；
- Advisor、critic 或 option renderer 成为第二个 strategy writer；
- agenda 清空或 strategy confirmed 后立即显示业务成功。

## 3. 示例开场白与期望 next_decision

### 示例 A：Browse-only 景观调查

用户：

> 先帮我浏览 PRIDE 里的海洋无脊椎动物蛋白质组，暂时不做训练；数量不限。

期望解析方向：objective 已给出，`task_type=browse_only`，quota 可记为
`open_ended`。若“浏览”尚未明确是候选即停还是需要证据复核，下一项应聚焦
`delivery_horizon`：例如建议先找到并复核一轮候选，并只问“候选后是否做证据复核”。

不应出现：DDA/DIA、训练标签、物种 generalization、label feasibility 或标记方式问题。

### 示例 B：Chimeric 训练表

用户：

> 我想做 DDA 嵌合谱解释训练表，先找大约 20 个项目，物种开放。

期望解析方向：objective、task、AI-ready 方向、scale、acquisition 与 species open
基本明确。最高优先的任务专属 `next_decision` 应是
`chimeric_label_feasibility`：建议优先可验证的 multi-peptide labels，并询问
“只收已有可信标签，还是允许扩大候选后下游 relabel？”

该问题必须排在“label-free/TMT/其他 labeling strategy”之前；optional labeling
不能抢占 build-ready 可行性。

### 示例 C：显式开放的 RT 训练请求

用户：

> 做跨实验室 RT 预测训练表，DDA，约 30 个项目；物种和标记方式都开放。

期望解析方向：若 training-table horizon、scale 与开放选择都已被结构化记录，
species 和 labeling 不得重新提问。若仍缺少交付终点的明确授权，下一项只问
`delivery_horizon`；若 critical agenda 已清空，则不应制造新问题，而应总结当前
strategy 并进入 `ready_to_confirm`，等待用户确认后执行。

## 4. Browse-only 与训练任务的差异

| 方面 | Browse-only | 训练任务 |
| --- | --- | --- |
| 议程来源 | common agenda | common + training + 可选 task-specific pack |
| 核心目的 | 形成有界、可检索的候选景观 | 形成能通向 builder 的可行策略 |
| Acquisition | 通常作为仓库证据检索，不应默认阻塞 | 可能改变训练可行性或混合策略，需要用户取舍 |
| Label feasibility | 不进入训练议程 | 由任务决定；chimeric 中是 critical |
| Species | 仅在主题范围需要时决定；可保持 open | 可能影响泛化/分层，但显式 open 后不得追问 |
| Optional labeling | 通常不问 | 只在更高优先的 task/horizon/scale/feasibility 后考虑 |
| Ready 的含义 | 策略可确认并开始浏览 | 策略可确认并开始寻找/构建材料 |
| 业务毕业 | 仍不因“问完”毕业；按所请求 package 报进展 | 唯一成功仍是 Authority 签发的 build-ready package |

Browse-only 不等于低质量，也不等于训练任务未填完。它是一个明确的任务选择；
server 必须避免把它误送进 training agenda。反过来，用户说“训练集”时也不能仅以
找到候选代替标签、材料和 builder-entry 可行性。

## 5. 议程与 build-ready 毕业的边界

议程只回答“策略是否足够明确，可以确认并执行”，不回答“交付是否已经完成”。

必须区分以下阶段：

1. **Agenda resolved**：关键用户取舍已解决；可以总结策略。
2. **Strategy confirmed**：用户授权执行当前 fingerprint；仍没有交付物。
3. **Candidates found/reviewed**：是可见进展，可能仍缺 label、membership、evidence 或 builder material。
4. **Build-ready evaluated**：Authority 根据真实 run/audit/manifest/EvidenceStore、membership、builder refs 与 package material 判定。
5. **Business completed**：只有 issued `BusinessCompletionDecision` 同时满足
   `succeeded=true`、`status=build_ready_succeeded`、
   `package_kind=build_ready`、`success_ui_allowed=true`，才允许成功状态。

所以：问完不等于确认，确认不等于执行完成，正向 repair delta 不等于成功，
候选或审查数量也不等于 build-ready。agenda 不能签发 completion，Manager 文案、
Runner 返回和 legacy `repair_completed` 事件同样不能越过 Authority Plane。

## 6. 可选手动试跑清单

用户可以选择手动试跑，不是验收前的强制操作。建议一次只验证一个行为：

- [ ] 用示例 A 开场，确认没有训练 acquisition/labeling 问题。
- [ ] 用示例 B 开场，确认 chimeric label feasibility 先于 optional labeling。
- [ ] 回答“标记方式开放”，确认下一轮不重复询问 labeling。
- [ ] 在同一句给出项目数、DDA 和物种 open，确认系统吸收全部信息但仍只问一个新问题。
- [ ] 先问“两个标签方案有什么区别？”，确认 Manager 先咨询回答，不把咨询误当 commitment。
- [ ] 用数字选择已有 option，确认只应用该 option 的预声明 patch。
- [ ] critical agenda 清空后，确认 UI 进入待确认而非成功。
- [ ] 确认并执行一个只有 candidates、0 build-ready 的 run，确认仍显示进展/阻塞而非绿勾。

完整依赖环境还可补跑：

```powershell
python -m pytest -q `
  tests/test_discovery_agent_turn.py `
  tests/test_discovery_task_build_plan.py `
  tests/test_discovery_agenda.py
```

当前离线报告已记录纯 agenda 8 passed、Authority/Wiring 非回归 93 passed；上述组合此前分别受 `openai-agents` 与 `typer` 依赖缺失限制，不能用现有结果冒充完整端到端对话试跑。

## 7. 后续议程数据化改进建议（不写代码）

### 7.1 扩展 task-specific packs

在 chimeric 首包之后，按 TaskProfile 增加真正影响可行性的任务决策，而不是回到
`app.py` 条件树：

- RT：method-specific baseline 与跨 LC generalization；
- fragment intensity：仪器/fragmentation 同质性与跨平台泛化；
- PSM scoring：接受 downstream target-decoy search，还是要求可复用 search outputs；
- de novo：tryptic、non-tryptic 或其它 peptide domain；
- PTM de novo：目标 PTM、enrichment 与 localization 门。

每个 pack 仍应只声明用户取舍；具体项目是否满足要求由 evidence retrieval 判定。

### 7.2 强化 evidence contract

把当前字符串型 `required_evidence` 逐步提升为版本化结构：evidence kind、作用域
（project/file/assay）、权威来源、何时检索、满足条件、未知时的处理方式。这样能让
agenda、audit 与 builder-entry 共享术语，同时避免把“需要证据”误变成“问用户”。

### 7.3 更精确的 horizon blocker

`blocks_build_ready: bool` 简单清楚，但长期可补充 `blocks_horizons` 或
`required_before`，区分 plan-only、browse、reviewed candidates 与 build-ready。
扩展时必须保持唯一业务成功为 build-ready，不能让中间 horizon 取得成功绿勾。

### 7.4 显式 open 的 provenance

继续区分系统默认 unknown 与用户明确 open。每个 resolution 应记录来源、策略版本和
对应 decision ID；后续只有用户要求 reconsider 或新证据产生真实冲突时才重开，避免
把默认值当承诺，也避免已经开放的维度被循环追问。

### 7.5 议程 schema 校验与版本化

为 pack 建立离线 lint/check：ID 唯一、priority 合法、blocking item 有非空 decision
variables 与 evidence、target field 属于策略契约、task-specific priority 不被 optional
项反超。schema 升级应可 replay，不能依赖旧对话文本重新猜测。

### 7.6 可观测性与评估

记录 agenda schema version、候选 item IDs、被选中的 next decision、选择理由、
resolved provenance 和跳过原因；不记录 secrets，也不让日志成为 mutation authority。
建立通用离线对话矩阵，覆盖 open、compound answer、numeric option、consultation、
reconsider、browse-only、每个 task pack 以及“0 build-ready 不成功”。

### 7.7 保持深模块边界

`TaskProfile` 声明科学依赖，agenda evaluator 做纯求值，Dialogue Manager 做唯一写入，
Authority Plane 判定交付。后续需求应优先深化这四个接口，禁止把科学主题特判重新散落
到 `app.py`、UI、repair 或 publication，也禁止为单一案例新增捷径。

STATUS: READY
