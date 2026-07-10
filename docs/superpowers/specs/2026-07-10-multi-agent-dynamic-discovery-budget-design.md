# Discovery 多 Agent 动态预算设计

日期：2026-07-10
状态：已完成交互设计确认，等待书面规格审阅
范围：OpenAI Agents SDK Discovery Control Plane

## 1. 背景

当前 OpenAI Agents SDK Discovery 路径由一个 `Proteomics Discovery Agent` 完成搜索、观察和最终 manifest 选择。SDK 负责模型回合和 function-tool 调度，项目自己的 Control Plane 负责科学状态、SQLite 持久化、工具策略、幂等和产物。

当前预算仍在运行前静态设定：

- `max_turns`
- `max_tool_calls`
- `max_discovery_rounds`

Web 中新增的 `auto / fast / standard / deep / custom` 仍然只是按需求复杂度选择固定预设，不属于真正的动态预算。用户已经明确要求：

- 模型回合只作为服务器安全上限。
- 搜索批次、查询数量和搜索工具投入由 Agent 在搜索过程中动态决定。
- Agent 在服务器硬上限内自主运行，不要求普通用户预先填写预算。
- 日志应像编码 Agent 一样展示真实行动、工具调用、证据、决策理由和下一步。

## 2. 目标

本设计把 Discovery 改造成受控的多 Agent 系统：

1. `Discovery Manager Agent` 决定搜索目标、查询策略和最终 manifest。
2. `Budget Agent` 独立判断是否值得继续搜索以及批准多少查询。
3. 确定性的 `BudgetGovernor` 强制执行服务器硬上限、授权完整性和幂等。
4. 每轮搜索后由确定性评估器生成事实指标，供两个 Agent 使用。
5. Web 实时展示脱敏的行动轨迹、公开推理摘要、工具调用和指标变化。
6. 用固定评测集证明质量与成本的平衡，而不是凭单次运行主观判断。

## 3. 非目标

本阶段不做以下工作：

- 不迁移到 LangGraph。
- 不把 AI-ready Build、下载、转换、Docker 执行或模型训练改成多 Agent。
- 不允许 Agent 修改物种、采集模式、PTM 范围、任务类型等科学硬约束。
- 不开放 shell、任意网络请求或高风险生物学重写工具。
- 不展示或持久化模型原始隐藏思维链。
- 不为 Budget Agent 增加第二套前端 API Key 配置。
- 不在本阶段升级已固定的 `openai-agents==0.18.1`。

## 4. 已确认的设计决策

### 4.1 采用多 Agent，而不是单 Agent 自报预算

系统包含一个负责搜索的 Discovery Agent 和一个负责预算评审的 Budget Agent。Budget Agent 不是普通提示词建议器，它的决定必须经过结构化工具提交并成为搜索授权的一部分。

### 4.2 采用 Manager 模式，不采用 handoff

Budget Agent 只完成一次受限的预算评审，不接管用户对话或最终数据选择。Discovery Manager 始终拥有主流程控制权。

复杂编排由自定义 function tool 内部调用嵌套的 `Runner.run()` 完成。该方式符合 OpenAI Agents SDK 对条件重试、链式调用和高级 agent-as-tool 编排的建议。

### 4.3 Agent 决策，确定性代码执行边界

Budget Agent 判断继续、缩减、重规划或停止。BudgetGovernor 不做科学价值判断，只负责：

- 硬上限
- schema 校验
- 查询绑定
- 防重放
- 幂等
- 状态持久化
- 审计

### 4.4 普通用户不再预设预算

普通 Web 表单不再要求填写搜索轮数、查询数量、工具调用预算或搜索强度。服务器安全上限只读展示，由部署配置管理。

## 5. 总体架构

```text
用户需求
  -> Discovery Manager Agent
       -> request_search_budget(SearchProposal)
            -> Budget Agent
                 -> submit_budget_decision(BudgetDecision)
            -> BudgetGovernor
                 -> SearchGrant
       -> search_repository_datasets(grant_id, queries)
            -> repository adapters
            -> candidate pool merge
            -> RoundMetricsEvaluator
       -> 继续提出计划，或 select_discovery_manifest(...)
  -> 最终 manifest、报告和审计产物
```

OpenAI Agents SDK 负责：

- Discovery Manager 模型回合
- Budget Agent 嵌套模型回合
- function-tool 调度
- 流式运行事件
- SDK tracing 接口

项目 Control Plane 负责：

- 科学请求和硬约束
- 动态预算状态
- SearchGrant 生命周期
- 实际查询与仓库请求计数
- 候选池和最终选择
- SQLite 和 JSON 审计
- 密钥与敏感字段脱敏

## 6. 组件职责

### 6.1 Discovery Manager Agent

职责：

- 理解用户目标和结构化请求。
- 根据当前事实指标设计具体查询。
- 提交 `SearchProposal`。
- 处理 Budget Agent 的 `grant`、`shrink`、`replan` 或 `stop`。
- 在搜索停止后选择跨轮候选池或某个具体轮次的 manifest。
- 生成面向用户的最终结论和公开推理摘要。

它不能绕过 grant 直接搜索，也不能修改硬约束。

### 6.2 Budget Agent

职责：

- 审查 Discovery Agent 的查询提案。
- 阅读历史查询、上一轮收益、候选缺口、质量缺口、元数据缺口、多样性缺口和剩余硬资源。
- 决定批准全部、批准子集、要求重规划或停止。
- 为停止决定提供尚未解决的问题、未探索策略以及不继续的理由。

Budget Agent 不能：

- 新增或改写搜索词。
- 执行仓库搜索。
- 选择最终 manifest。
- 修改科学约束。

### 6.3 BudgetGovernor

BudgetGovernor 是确定性服务，不调用模型。它负责：

- 校验提案和预算决定。
- 将批准索引映射回原始查询。
- 检查剩余查询单元和仓库请求硬上限。
- 检查重复、空查询和无效索引。
- 签发绑定 `run_id + proposal_id + query_hash` 的一次性 grant。
- 原子化消费 grant，拒绝重放、篡改和跨任务使用。
- 记录审计事件。

### 6.4 RoundMetricsEvaluator

评估器只读取 manifest、历史查询和资源使用情况，输出确定性指标。它不直接决定继续或停止。

指标包括：

- `candidate_shortfall`
- `quality_gap`
- `metadata_gap`
- `diversity_gap`
- `strategy_novelty`
- `last_round_yield`
- `query_repetition`
- `budget_pressure`

同时保留原始计数，避免仅凭归一化分数无法审计。

### 6.5 AgentRunStore 扩展

现有 SQLite store 继续作为事实来源，新增对提案、预算决定、grant、指标快照和累计资源的持久化。不得只把这些状态放在模型上下文中。

## 7. 数据协议

### 7.1 SearchProposal

```json
{
  "objective": "补充具有完整样本元数据的人血浆DDA数据",
  "reasoning_summary": "当前候选数量足够，但样本级元数据缺失率较高",
  "evidence_refs": ["round_02", "metadata_gap:0.62"],
  "queries": [
    "human plasma proteomics DDA SDRF",
    "human plasma Orbitrap raw files"
  ],
  "expected_gain_dimensions": ["metadata_completeness", "usable_files"],
  "expected_gain": "提高样本元数据完整度并补充有效RAW文件",
  "alternatives_considered": ["扩大通用关键词", "按SDRF定向搜索"],
  "stop_condition": "新增合格文件少于2个"
}
```

`proposal_id`、canonical query list 和 query hash 由 Control Plane 生成，不能由模型指定。

### 7.2 BudgetDecision

```json
{
  "proposal_id": "proposal_...",
  "decision": "shrink",
  "approved_query_indexes": [0],
  "rejected_query_indexes": [1],
  "observed_gaps": ["metadata_gap"],
  "expected_value": 0.68,
  "confidence": 0.82,
  "reasoning_summary": "第二条查询与历史策略高度重复",
  "stop_after_execution_if": "新增合格文件少于2个",
  "unresolved_gaps": [],
  "unexplored_strategies": [],
  "why_not_continue": ""
}
```

允许的 `decision`：

- `grant`：批准全部查询。
- `shrink`：批准非空真子集。
- `replan`：不签发 grant，要求 Discovery Agent 换策略。
- `stop`：停止追加搜索预算，进入最终选择。

`stop` 必须填写 `unresolved_gaps`、`unexplored_strategies` 和 `why_not_continue`。缺少反事实说明的停止决定无效，允许 Budget Agent 修正一次。

### 7.3 SearchGrant

```json
{
  "grant_id": "grant_...",
  "run_id": "agents_discovery_...",
  "proposal_id": "proposal_...",
  "approved_queries": ["human plasma proteomics DDA SDRF"],
  "query_hash": "sha256:...",
  "query_units": 1,
  "status": "issued",
  "single_use": true
}
```

状态只能按以下路径变化：

```text
issued -> consumed
issued -> rejected
issued -> expired
```

不得从终态恢复为 `issued`。

### 7.4 RoundMetrics

每次搜索后记录本轮和累计值：

- 新增项目、文件、有效文件和 review 文件数。
- `valid / weak_keep / needs_review / exclude` 分布。
- task readiness 和数据价值分布。
- 元数据未知字段数和完整度。
- project、repository、instrument、fragmentation 和 PTM 多样性。
- 查询重复度、候选重复度、缓存命中。
- 查询单元、实际仓库请求、搜索批次和模型回合消耗。
- 与上一轮相比的增量和边际收益。

## 8. 动态预算循环

1. Discovery Manager 读取当前状态并提出一批具体查询。
2. `request_search_budget` 持久化提案并启动 Budget Agent。
3. Budget Agent 必须通过严格 schema 的 `submit_budget_decision` 工具提交决定。自然语言最终输出不是预算事实来源。
4. BudgetGovernor 校验决定。`grant / shrink` 在剩余硬资源内签发 grant；`replan / stop` 原样持久化并返回。只有硬上限耗尽时，Governor 才强制返回 `hard_limit_stop`。
5. Discovery Manager 使用同一批查询和 grant 调用搜索工具。
6. 搜索工具原子化消费 grant，再执行仓库搜索。
7. 搜索结果写入轮次 manifest，并合并到跨轮 candidate pool。
8. 评估器生成 RoundMetrics，返回两个 Agent 可见的事实状态。
9. Discovery Manager 再次提出搜索计划，或选择最终 manifest。

搜索批次不是预先配置的工作流步数，而是循环实际发生的次数。

## 9. 硬上限与资源计数

多 Agent 模式保留服务器硬上限，但不把它们解释为人工搜索计划：

- Discovery Manager `max_turns`
- 单次 Budget Agent `max_turns`
- 总查询单元硬上限
- 总仓库请求硬上限
- 总运行时间硬上限
- 全局 function-tool 调用硬上限

多 Agent 模式不使用 `max_discovery_rounds` 作为正常停止逻辑。搜索批次可由查询和请求硬上限间接约束。

一条 canonical query 消耗一个 query unit。一次工具调用携带 N 条查询时，消耗 N 个 query units，而不是只计一次。实际仓库 HTTP 请求另行计数。

## 10. 停止与最终选择

停止追加搜索的来源：

- Budget Agent 返回合法的 `stop`。
- Discovery Manager 主动选择最终 manifest。
- 服务器硬资源耗尽。
- Budget Agent 连续无法提交合法决定。
- 仓库持续不可用且没有获批的重试方案。

停止搜索不等于立即终止 Runner。系统进入 `finalizing`，Discovery Manager 仍有一次最终分析和 manifest 选择机会，搜索工具被禁用。

最终状态：

- 有合格文件且不需 review：`completed`
- 有可用文件但包含 review：`completed_with_review`
- 无可用文件：`blocked`
- SDK、状态库或不可恢复的系统错误：`failed`

候选池是跨轮去重和筛选后的事实来源。后续空轮不能覆盖较早的非空结果。

## 11. 价值平衡

评估器提供事实向量，Budget Agent 完成最终价值判断。系统不使用单一固定阈值公式替代 Agent。

Budget Agent 需要同时考虑：

```text
潜在收益：候选不足、质量缺口、元数据缺口、多样性缺口、新策略价值
搜索成本：查询数量、仓库请求、历史低收益、重复率、预算压力
```

`expected_value` 和 `confidence` 是解释和评测字段，不作为越权硬门槛。BudgetGovernor 不能只因模型自报分数低或高就改变授权。

## 12. 错误处理

### 12.1 Budget Agent 输出无效

- schema 错误作为工具结果返回 Budget Agent。
- 允许一次修正。
- 第二次仍错误时，本轮不搜索并记录 `budget_decision_invalid`。
- 已有候选时进入最终选择；尚无候选时任务 `blocked`。

### 12.2 Grant 无效

以下情况拒绝搜索：

- grant 不存在或不属于当前 run。
- query hash 不匹配。
- 查询数量或顺序被修改。
- grant 已消费、过期或被拒绝。
- 实际执行会突破硬上限。

### 12.3 仓库失败

- grant 在开始真实尝试时即消费，不能直接重放。
- 记录实际请求消耗和错误类别。
- 可重试错误返回 Discovery Manager 和 Budget Agent。
- 是否签发新的重试 grant 由 Budget Agent 决定，仍受硬上限约束。

### 12.4 模型回合耗尽

- 已有候选时执行确定性的最佳 manifest 回退选择，并标注 fallback 理由。
- 无候选时返回 `blocked` 或 `failed`，取决于是否执行过有效搜索。
- 不因回合耗尽丢弃已持久化结果。

## 13. 实时日志与推理可见性

目标体验是编码 Agent 风格的持续工作日志，但展示的是可公开、可审计的推理摘要和真实行动轨迹，不是原始隐藏思维链。

每条可见日志包含：

- 时间和 sequence ID
- Agent 角色
- 事件类型
- `reasoning_summary`
- `evidence_refs`
- 考虑过的替代方案
- 选择的动作
- 工具输入摘要
- 工具输出摘要
- 指标变化
- 下一步

推理摘要必须在提案或决定提交时同步持久化，不能在运行结束后重新编造。

OpenAI 官方说明区分原始隐藏思维链与模型生成的推理摘要：

- https://openai.com/index/learning-to-reason-with-llms/
- https://openai.com/index/chain-of-thought-monitoring/

不得写入日志：

- API Key
- Authorization header
- 未脱敏供应商请求体
- 原始隐藏思维链
- 可能包含密钥的异常原文

## 14. Web 设计

普通 Discovery 表单移除：

- `auto / fast / standard / deep / custom`
- 搜索轮数输入
- 查询数量输入
- 工具预算输入

只显示：

- 执行方式
- `Agent 自主预算`
- API Key、Base URL 和 Model
- 服务器硬上限和实际消耗的只读状态

日志区域分为：

1. `活动`：Agent 计划、预算评审、搜索、观察和最终选择。
2. `工具与指标`：查询、grant、工具结果、资源计数、缓存和指标变化。
3. `原始事件`：脱敏后的结构化 JSON。

后端从 `Runner.run_sync()` 迁移到流式运行，消费 SDK 事件并写入带 sequence ID 的 SQLite 事件流。Web 轮询只追加未见事件，保留滚动位置，避免刷新抖动。

两个 Agent 第一版共用当前请求的 API Key、Base URL 和 Model。密钥继续只在活动请求内存中存在。

## 15. 审计事件与产物

新增事件：

- `search_plan_proposed`
- `budget_review_started`
- `budget_decision_recorded`
- `budget_decision_invalid`
- `search_grant_issued`
- `search_grant_rejected`
- `search_grant_consumed`
- `round_value_evaluated`
- `budget_replan_requested`
- `dynamic_search_stopped`

现有工具、manifest 和 run 事件继续保留。

最终产物：

- `agents_discovery_summary.json`
- `agents_discovery_events.json`
- `agents_discovery_budget.json`
- `agents_discovery_report.md`
- `candidate_pool/dataset_manifest.json`
- `dataset_manifest.json`
- `agent_control.sqlite`

`agents_discovery_budget.json` 汇总提案、批准、拒绝、实际查询、实际请求、停止原因和是否触达硬上限。

## 16. 测试设计

### 16.1 单元测试

- SearchProposal、BudgetDecision、SearchGrant、RoundMetrics schema。
- grant 状态机和 query hash。
- 指标归一化、增量、重复度和资源压力。
- stop 反事实字段校验。

### 16.2 安全与策略测试

- 无 grant 搜索。
- 篡改查询、顺序或数量。
- 重放、过期、跨 run grant。
- 超过查询和仓库请求硬上限。
- Budget Agent 尝试修改硬约束。

### 16.3 Fake Model 多 Agent 集成测试

覆盖：

- `grant -> search -> stop -> select`
- `shrink -> search`
- `replan -> new proposal`
- 无效决定后修正
- Budget Agent 停止后最终选择
- 模型回合耗尽后的回退选择

### 16.4 历史问题回归

- 第一轮空、第二轮成功。
- 前一轮成功、后一轮空。
- 跨轮候选池去重。
- needs-review 候选得到 `completed_with_review`。
- 相同查询幂等复用。
- API Key 不进入任何产物。
- Web 日志只追加、不抖动、不重置滚动位置。

### 16.5 真实模型烟雾测试

使用当前 DeepSeek OpenAI-compatible 配置执行一个受控 PRIDE Discovery，验证：

- 两个 Agent 均实际运行。
- 严格 function-tool schema 可用。
- grant 被签发和消费。
- 流式事件可见。
- 最终 manifest 和预算报告一致。

真实烟雾测试是显式 opt-in，默认测试套件不依赖外部网络和密钥。

## 17. 平衡评测与发布门槛

建立固定 Discovery replay/eval 集，至少覆盖：

- 第一轮为空、后续成功。
- 连续小幅新增。
- 数量够但质量差。
- 只有 needs-review 候选。
- 重复查询循环。
- 仓库持续无结果。
- 跨仓库搜索才能成功。
- 元数据缺失但候选数量充足。

与宽松固定预算深搜基线比较：

- 可用候选召回率至少达到基线的 95%。
- 平均搜索工具消耗降低至少 20%。
- 错误提前停止率低于 5%。
- 最终候选质量不得下降。
- 科学硬约束违反为 0。
- 重复查询率低于当前单 Agent 基线。

只有达到上述门槛，多 Agent 才能成为默认 Discovery 模式。

## 18. 兼容与发布策略

第一阶段增加服务器配置：

```text
AGENT_DISCOVERY_MODE=single_agent|multi_agent
```

初始默认保留 `single_agent`，多 Agent 作为 opt-in。完成完整回归、真实烟雾测试、浏览器检查和固定评测后，将默认值切换为 `multi_agent`。旧单 Agent 模式至少保留一个发布周期作为回退。

传统 deterministic discovery 路径保持不变。

## 19. 预计代码边界

主要修改：

- `src/agent/control_plane/models.py`
- `src/agent/control_plane/store.py`
- `src/agent/control_plane/policy.py`
- `src/agent/control_plane/discovery.py`
- `src/agent/control_plane/openai_agents.py`
- `src/agent/web/app.py`
- `src/agent/web/templates/index.html`
- `tests/test_control_plane.py`
- `tests/test_web_discovery.py`
- `tests/test_frontend_template.py`

预计新增聚焦模块：

- `src/agent/control_plane/budget_agent.py`
- `src/agent/control_plane/budget_governor.py`
- `src/agent/control_plane/discovery_metrics.py`
- `tests/test_dynamic_budget.py`

文档更新：

- `README.md`
- `docs/openai-agents-control-plane.md`
- `docs/PROJECT_HANDOFF_CN.md`

## 20. 实施顺序

1. 清理尚未完成的静态预算预设。
2. 增加动态预算模型和 SQLite 持久化。
3. 实现 RoundMetricsEvaluator。
4. 实现 Budget Agent 和严格决策工具。
5. 实现 BudgetGovernor 和一次性 SearchGrant。
6. 接入 Discovery Manager 循环。
7. 改为流式 Runner 和追加式事件日志。
8. 更新 Web UI。
9. 完成单元、集成、回归和评测测试。
10. 执行 DeepSeek 真实烟雾测试和浏览器 QA。
11. 更新项目文档并准备发布。

## 21. 完成标准

实现只有在以下条件全部满足时才算完成：

- 普通用户不需要设置搜索预算。
- Discovery Agent 无法绕过 Budget Agent 直接搜索。
- Budget Agent 无法执行查询或修改科学硬约束。
- 所有搜索均使用有效、匹配、一次性的 grant。
- 查询单元和实际仓库请求得到独立计数。
- 搜索停止后仍完成最终 manifest 选择。
- 较早非空候选不会被后续空轮覆盖。
- Web 展示实时行动轨迹和公开推理摘要，且日志稳定不抖动。
- 密钥和原始隐藏思维链不进入日志或产物。
- 相关测试和完整回归通过。
- 固定评测达到质量与成本门槛。
- DeepSeek 真实烟雾测试和桌面/移动浏览器检查通过。
