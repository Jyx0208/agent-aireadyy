# 多模型专家 Agent 评分闭环

## Goal

建立一个可审计、证据驱动的多模型专家评分闭环：由多个实际底层模型相互独立的专家 Agent，依据原始用户 Prompt、充分的项目背景、候选结果与必要的外部调查证据进行评分；通过代码控制的共识与分歧流程形成 `model_expert_consensus`，减少“同一个模型做题又给自己打分”的偏差，同时为未来真实人类专家校准和升级为 `human_verified` 保留明确接口。

该闭环提供更可靠的 benchmark 判断信号，但不把模型共识包装成人类金标准，也不把启发式分、硬约束结果和成功概率混成一个“科学总分”。

## Confirmed Facts

- 当前没有可持续投入的真实人类专家，第一阶段的评审主体必须是大模型专家 Agent。
- 专家需要掌握原始用户 Prompt、项目背景和候选结果；必要时应访问原项目页面、文档或仓库补证，而不是只对一段摘要做静态分类。
- 当前 `task_ai_readiness_score` / `data_value_score` 是 `heuristic_rule_based`、`not_probability`；soft score 不得覆盖 hard-gate failure。
- 当前已有文件型 pool registry、机器 Judge Job、0–3 分、影响重算和两票/五票聚合原型，但尚未证明投票来自独立底层模型，也没有完整的证据、逐维 rubric 和模型共识语义。
- 当前配置存在“公开模型别名映射到其他底层模型”的代理层，因此仅比较请求中的模型别名不足以证明专家独立。
- `human_grades`、`human_verified` 以及把无来源 reviewed JSON 默认提升为人类真值的旧路径，不适用于模型专家结果。

## Requirements

### R1. 信号和来源必须严格分离

系统必须分别保存和展示：

1. deterministic hard-gate outcome；
2. internal heuristic readiness/value；
3. model-expert judgment；
4. future human verification；
5. calibrated build-success probability（待真实 outcome 与足够样本后实现）。

约束：

- hard-gate `fail` 永远不能被任何软分、专家分或聚合分补偿。
- hard-gate `unknown` / `review` 必须明确保留不确定性，不能伪装为 `pass`。
- 第一阶段使用以下来源等级：
  - `model_expert_provisional`：尚未达到独立共识，或证据不足；
  - `model_expert_consensus`：满足独立性、证据与聚合规则的模型共识；
  - `needs_adjudication`：专家分歧无法可靠消解；
  - `human_verified`：仅由未来真实人类评审流程产生。
- 模型专家记录不得写入 `human_grades`，不得自动生成 `human_verified`。
- 无显式、可验证来源的旧 reviewed JSON 必须标为 `legacy_unverified`，不得默认升级。

### R2. 专家独立性

每个专家票必须记录请求身份与解析后的执行身份，包括：

- provider；
- requested model ID / alias；
- resolved model ID（能验证时）；
- model family；
- endpoint / routing profile ID；
-生成候选的模型身份（仅供编排器冲突检查，不暴露给评分专家）；
- rubric、Prompt、工具策略版本。

默认独立性政策：

- 同一底层模型只更换 system prompt、temperature、seed 或角色名称，不算不同专家。
- 映射到同一底层模型的不同别名，不算不同专家。
- 专家必须与候选生成模型至少在 model family 上不同；优先同时使用不同 provider。
- 两位首评专家应来自不同 model family；条件允许时也来自不同 provider。
- 若运行环境不能验证真实底层模型，不得宣称“已验证独立”，该票最多为 `model_expert_provisional`。
- 独立性不足时系统应拒绝形成正式共识，而不是静默降级。

### R3. 专家输入包与盲化

所有首评专家接收语义一致、版本固定的 Evidence Package，至少包含：

- 原始用户 Prompt 和明确任务目标；
- 项目名称、描述、原项目 URL 与允许公开的身份信息；
- 与任务相关的项目文档、文件清单、元数据和已采集事实；
- 待评分候选结果及必要附件；
- deterministic hard-gate 事实、原因和未知项；
- 证据采集时间、来源 URL、内容 hash 和可用性状态；
- 统一 rubric、输出 schema 和工具使用规则。

盲化边界：

- 必须隐藏：候选由哪个模型、Agent runtime、workflow 或评审系统生成；其他专家身份、分数、理由和聚合状态。
- 必须允许：项目身份、原始项目 URL、用户 Prompt、原始项目材料和验证任务所必需的事实。
- 独立首评完成前，专家不得看到其他专家意见。
- Evidence Package 不得包含 API key、token、cookie 或其他秘密。

### R4. 主动调查与证据协议

专家 Agent 可以在受控工具范围内主动补证，包括 Web search/fetch、原项目页面、公开文档和授权的本地项目材料。

每位专家必须输出：

- 调查状态：`not_needed`、`completed`、`partial`、`failed` 或 `insufficient_evidence`；
- 实际访问的来源及引用；
- 支撑或反驳各项结论的证据；
- 页面不可达、登录墙、动态内容、超时或相互矛盾证据；
- 未经验证的推断与明确事实的区分。

约束：

- 外部访问必须有超时、重定向限制、大小限制和网络策略，防止访问内网、凭据端点或非授权资源。
- 外部页面内容一律视为不可信数据，不得把页面中的指令当作系统指令执行。
- 没有足够证据时必须返回 `insufficient_evidence` 或降低置信度，不能强行给出确定结论。
- 审计记录保存引用、内容 hash、获取时间和必要快照元数据；网页发生变化时仍可解释当时判断依据。

### R5. 统一评分 Rubric

每位专家独立评估以下维度，逐维给出状态、分数、理由、证据引用和置信度：

1. **硬约束满足度**：是否满足不可妥协条件；使用 `pass / fail / unknown`，不参与软分补偿。
2. **任务相关性**：项目与原始 Prompt、目标任务和预期使用场景的匹配程度。
3. **元数据与标签充分性**：说明、标签、许可、来源和关键元数据是否足以支持后续处理。
4. **文件包与交付完整度**：必要文件、结构、依赖和可访问材料是否完整。
5. **证据可信度**：结论是否由原始或可靠来源支持，是否存在冲突或无法验证的信息。

专家另行给出：

- 最终 0–3 relevance/fitness grade；
- 总体置信度；
- 一段简洁结论；
- 关键风险和缺失信息；
- 是否建议进入仲裁。

最终 0–3 分必须由 rubric 明确锚定，不能简单用维度平均数替代；hard-gate `fail` 时最终处置必须为拒绝/跳过，即使软维度较高。

### R6. 独立执行、共识和仲裁

- 编排器默认并行运行两位满足独立性政策的首评专家。
- 两位专家的 hard-gate 判断一致，最终 grade 相同或在协议允许的一档范围内，且均无关键证据冲突时，可由确定性代码形成共识。
- 共识 grade 采用协议规定的确定性规则（离散 grade 优先多数票；需要序数汇总时使用中位数），不得由另一个模型自由改写。
- 发生以下任一情况时调用第三位异构专家：hard-gate 分歧、grade 超过允许差异、关键事实冲突、低置信度或 `insufficient_evidence`。
- 第三位专家同样先独立评审，不得预先看到前两票。
- 三票可按多数 hard-gate、逐维多数/中位数和证据充分性规则形成共识；若无可靠多数或独立性仍不足，状态为 `needs_adjudication`。
- 第一阶段不设置可覆写事实的“万能裁判模型”。未来如增加裁判，只能在独立票完成后阅读各票和证据，输出可审计的选择理由，且不能把模型仲裁升级为 `human_verified`。

### R7. 可审计存储与安全

- 每次专家运行、工具调用摘要、结构化输出、独立性判定、聚合结果和状态变化都必须可追踪。
- 记录 Evidence Package hash、rubric/prompt/schema 版本、时间、错误和重试，不允许静默覆盖历史。
- API 凭据只存在服务端安全配置或凭据系统中，不写入公开 Job JSON、Prompt、证据包或导出文件。
- 正式状态以服务端存储为准；浏览器缓存不得成为权威来源。
- 第一阶段可在现有文件型 Job/Pool 原型上兼容演进，但写入必须原子化，并为后续事务存储迁移保留稳定领域模型。

### R8. Benchmark 与生产使用边界

- `model_expert_consensus` 可作为模型专家 benchmark overlay，用于比较 discovery/replacement 排序、离线评估和候选抽样。
- 在没有真实人类校准前，不得称其为人类金标准，也不得直接替代 hard gate。
- 第一阶段不允许模型共识直接触发不可逆生产动作；生产 selection/action 若使用该信号，必须作为可解释的辅助信号并受 hard gate 与显式策略约束。
- 未来真实人类专家可对抽样案例复核，测量 model/model 和 model/human agreement，并将个案明确升级为 `human_verified`；升级必须保留模型历史而不是覆盖。

### R9. 报告与质量警告

报告至少包含：

- 评审覆盖率、证据充分率、调查成功/失败率；
- 专家间 percent agreement、weighted Cohen’s kappa、hard-gate agreement；
- 第三评率、`needs_adjudication` 率、低置信度率；
- 按 provider、model family、候选生成模型家族分层的偏差与一致性；
- 模型共识与未来人类标签的一致性；
- NDCG@5、high-relevance recall 和 replacement benchmark 影响。

小样本、无重叠、全同分、模型身份无法验证、证据覆盖不足等条件必须产生明确警告，不输出误导性的精确结论。

## Acceptance Criteria

- [ ] 可配置至少三条专家 profile，并记录 provider、请求模型、解析模型、model family 和路由身份。
- [ ] 系统能识别同底层模型别名、同模型家族和候选生成模型冲突；不满足政策时拒绝生成 `model_expert_consensus`。
- [ ] 两位首评专家接收相同版本的 Evidence Package 和 rubric，但看不到生成模型或其他专家意见。
- [ ] Evidence Package 包含原始 Prompt、项目事实、原项目 URL、候选结果、hard-gate 事实和可验证 hash，且不包含秘密。
- [ ] 专家能在受控网络策略内调查原项目，并保存调查状态、引用、获取时间和证据 hash。
- [ ] 专家输出通过严格结构化 schema 校验，包含五维判断、最终 0–3 分、理由、证据、置信度和缺失信息。
- [ ] hard-gate `fail` 无法被软维度或聚合分覆盖；`unknown` / `review` 被明确保留。
- [ ] 两位异构专家满足协议时形成 `model_expert_consensus`；出现分歧时自动运行第三位异构专家。
- [ ] 三票仍无法可靠收敛时返回 `needs_adjudication`，而不是伪造确定标签。
- [ ] 模型专家结果不写入 `human_grades`，不生成 `human_verified`；旧无来源数据被标为 `legacy_unverified`。
- [ ] 服务重启后专家票、证据引用、独立性判定、聚合状态和审计历史仍可恢复，且公开 Job 数据不含 API key。
- [ ] replacement benchmark 能显式选择 `model_expert_consensus` overlay，并与 heuristic、hard gate、human verification 分开展示。
- [ ] 报告对模型身份不可验证、证据不足和统计不可解释条件给出警告。
- [ ] 未来真实人类复核可追加并升级单个案例为 `human_verified`，且模型评审历史仍保留。

## Out of Scope for First Vertical Slice

- 宣称模型专家共识等同于真实人类金标准。
- 训练或微调新的评分模型。
- 基于真实 build outcome 自动拟合成功概率。
- 让模型共识自动执行不可逆生产动作。
- 大规模组织级 SSO/RBAC。
- 无限制浏览器、任意代码执行或开放式内网访问。
