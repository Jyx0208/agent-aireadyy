# 多模型专家 Agent 评分闭环：技术设计

## 1. Architecture Overview

采用“代码控制工作流 + 可调用工具的独立专家 Agent”结构，而不是让多个模型在同一对话中协商：

```text
Candidate + Project Context + Original Prompt
                    |
                    v
          Evidence Package Builder
                    |
          immutable package + hash
                    |
                    v
         Expert Orchestrator / Policy
          /                     \
 Expert A (independent)   Expert B (independent)
          \                     /
           Structured judgments
                    |
                    v
      Deterministic Consensus Engine
                    |
       agree --------+--------- disagree
         |                        |
         v                        v
 model_expert_consensus    Expert C independent
                                  |
                         consensus or
                         needs_adjudication
```

LLM 负责调查、理解和形成带证据的独立意见；候选分配、独立性验证、触发第三评、聚合、状态升级和 benchmark 导出由确定性代码控制。

## 2. Domain Boundaries

建议新增独立领域包 `src/agent/benchmark_review/`，避免继续把正式语义堆叠到 `web/app.py`：

- `models.py`：profile、evidence package、judgment、consensus、provenance 数据模型；
- `model_registry.py`：专家 profile 和实际模型身份解析；
- `independence.py`：候选生成模型与专家之间的独立性政策；
- `evidence.py`：统一 Evidence Package 构建、规范化和 hash；
- `rubric.py`：rubric 版本、grade anchors 和结构化 schema；
- `expert_runner.py`：单一专家 Agent 执行，工具调用与输出校验；
- `consensus.py`：确定性两票/三票聚合和分歧分类；
- `service.py`：评审状态机和编排入口；
- `store.py`：append-only 事件与快照接口；
- `reporting.py`：一致性、证据质量、偏差和统计警告；
- `export.py`：显式来源的 benchmark overlay；
- `network_policy.py`：URL 校验、允许协议、私网阻断和访问限制。

现有模块作为适配层：

- `src/agent/web/expert_review/jobs.py`：复用 Job 生命周期、并发、恢复和取消机制；
- `src/agent/web/expert_review/pool_registry.py`：兼容导入旧 pool，但不再决定正式来源语义；
- `src/agent/discovery/blind_judging.py`：复用两票/扩票思想，聚合迁移到明确的专家身份和逐维协议；
- `src/agent/web/app.py`：只做认证、参数转换和服务调用；
- `src/agent/web/templates/benchmark_review.html`：展示 profile、证据、独立性与共识，不保存权威状态。

## 3. Core Data Contracts

### 3.1 ExpertModelProfile

```text
profile_id
provider
requested_model_id
resolved_model_id | null
model_family
endpoint_identity
routing_profile_id
capabilities: [structured_output, web_search, web_fetch, local_evidence]
credential_ref
identity_verification: verified | provider_attested | unverified
enabled
config_version
```

`credential_ref` 只引用服务端凭据，不进入公共序列化。代理返回真实模型身份时记录 `resolved_model_id`；无法验证时保持空值并标记 `unverified`，不能由别名推断。

### 3.2 CandidateGenerationIdentity

```text
generator_provider | null
generator_requested_model | null
generator_resolved_model | null
generator_model_family | null
generator_runtime
identity_verification
```

该对象只供 Orchestrator 做冲突检查。Expert Runner 构造 Prompt 时必须删除整个对象。

### 3.3 EvidencePackage

```text
package_id
schema_version
candidate_id
original_user_prompt
project: {name, description, canonical_url, allowed_sources}
candidate_artifacts
project_materials
hard_gate: {outcome, reasons, unknowns}
collected_evidence[]: {source_type, uri, fetched_at, content_hash, excerpt/ref}
collection_limits
created_at
package_hash
```

规范化 JSON 后计算 `package_hash`。大附件保存为受控 blob/reference，不把秘密或未经授权的本地路径交给模型。

### 3.4 ExpertJudgment

```text
judgment_id
candidate_id
profile_snapshot
package_hash
rubric_version
prompt_version
schema_version
investigation_status
investigated_sources[]
dimensions: {
  hard_constraints: {outcome, rationale, evidence_refs, confidence},
  task_relevance: {score, rationale, evidence_refs, confidence},
  metadata_sufficiency: {...},
  package_completeness: {...},
  evidence_credibility: {...}
}
final_grade: 0 | 1 | 2 | 3 | null
overall_confidence
summary
risks[]
missing_information[]
recommend_adjudication
usage/error metadata
created_at
```

当 `insufficient_evidence` 且无法给出可靠 grade 时，`final_grade` 允许为 null。结构化校验失败先做有限次数重试；仍失败则保存失败事件，不把自由文本当正式票。

### 3.5 ConsensusRecord

```text
consensus_id
candidate_id
judgment_ids[]
independence_evaluation
hard_gate_outcome
dimension_aggregates
consensus_grade | null
status: model_expert_provisional | model_expert_consensus | needs_adjudication
trigger_reasons[]
aggregation_policy_version
created_at
```

ConsensusRecord 不复制成 `human_grades`。未来 HumanVerificationRecord 作为单独 overlay 追加。

## 4. Independence Policy

独立性判断使用 profile 快照和生成身份，优先级从强到弱：

1. `resolved_model_id` 相同：明确冲突；
2. model family 相同：首评专家冲突；
3. 与已知候选生成 model family 相同：自评冲突；
4. provider 相同但 family 不同：允许作为降级候选，但不能满足“优先跨 provider”；
5. 任一关键身份 `unverified`：保留票，但共识质量降级；若无法证明两票异构，则不可产生 `model_expert_consensus`。

选择算法：

1. 排除 disabled、缺少必需能力或与生成模型冲突的 profile；
2. 对 profile pair 按“跨 family、跨 provider、身份已验证、工具能力匹配”排序；
3. 选择最高质量 pair；
4. 分歧时选择与前三个身份都尽可能不同的第三 profile；
5. 无合格 profile 时返回明确的 `insufficient_independent_experts`。

不能依赖用户级别名字符串证明异构性。若代理无法报告真实模型，提供管理员配置的 provider attestation，但报告必须显示该限制。

## 5. Evidence and Exploration

### 5.1 Allowed tools

第一阶段采用受控 host-side tools：

- `search_public_web(query)`；
- `fetch_public_url(url)`；
- `read_evidence_blob(ref)`；
- 可选 `list_project_materials()`。

不要给专家任意 shell、写文件或无限制浏览器。Claude Provider 接入时使用官方 `anthropic` Python SDK；复杂评审使用 adaptive thinking，长输入/输出使用 streaming，结构化结果使用 SDK 支持的 structured output。其他 Provider 通过各自适配器实现同一 Runner 接口。

### 5.2 Network policy

- 仅允许 `https`（必要时受控 `http` 重定向到 `https`）；
- DNS 解析后阻断 loopback、link-local、私网、metadata IP 和非公网地址；
- 每次重定向重新验证目标；
- 限制响应大小、MIME、跳转数、总请求数和总时长；
- 默认拒绝认证页面、cookie 注入和下载可执行文件；
- 页面文本标记为 untrusted evidence，模型 Prompt 明确禁止遵循网页指令。

### 5.3 Evidence persistence

保存来源 URI、获取时间、HTTP 状态、内容 hash、提取文本 hash、引用位置和错误类型。对版权或存储限制较强的页面只保存最小引用和 hash；必要的内部材料按授权策略存储。

## 6. Rubric and Grade Anchors

最终 grade 采用离散锚点：

- `0 — 不相关/不可用`：任务核心不匹配，或确定性 hard gate fail；
- `1 — 弱相关/高风险`：存在部分价值，但关键材料、元数据或可信证据严重不足；
- `2 — 相关/可继续评估`：主要目标匹配，问题可补救，证据足以支持继续处理；
- `3 — 高度相关/优先`：任务高度匹配、材料完整、证据可靠且无关键硬约束问题。

维度软分建议统一 0–3，但 `hard_constraints` 始终使用 `pass/fail/unknown`。最终 grade 是专家的明确结论并受锚点约束，不由平均分机械生成。

## 7. Consensus Algorithm

### Two-vote stage

1. 校验两票 schema、package/rubric version 和独立性；
2. 任一 hard `fail` 与另一票不一致：触发第三评；两票均 `fail`：共识处置为 fail，grade 0；
3. 任一票 `unknown`、null grade、低置信度或关键证据冲突：触发第三评；
4. hard 一致且 grades 相同：直接形成共识；
5. hard 一致且 grades 相差 1，且逐维无方向性冲突、置信度足够：consensus grade 取中位/保守整数规则（策略版本化）；
6. grades 相差超过 1：触发第三评。

### Three-vote stage

- hard outcome 取有充分证据的多数；任何可信 `fail` 不得被软分抵消，若 fail 事实冲突未解决则 `needs_adjudication`；
- grade 有两票相同且这两票满足独立性与证据门槛时取多数；
- 三票连续分散时取中位数仅作为分析值，正式状态为 `needs_adjudication`，除非逐维规则能明确消解；
- 两票共享同一底层身份时不能作为“两票多数”；
- 输出 trigger reasons 和每一步使用的 judgment IDs。

## 8. State Machine

```text
queued
  -> evidence_ready
  -> reviewing_primary
  -> primary_complete
      -> consensus_complete
      -> reviewing_tiebreaker
          -> consensus_complete
          -> needs_adjudication
  -> failed / cancelled
```

每次转换追加事件。重启时从最后一个完整事件和快照恢复；进行中的外部调用不假定幂等，依靠 run ID 和 judgment ID 去重。

## 9. Compatibility and Migration

- 保留旧 pool 导入，但对 `human_grades` 与模型机器票进行来源审计；无法证明真实人类来源的记录标为 `legacy_unverified`。
- 修改 `grading.py` 时新增 model expert 专用路径，保留真实 human API 但不供 Judge Job 调用。
- `compile_discovery_blind_judgments.py` 必须要求显式 `judgment_source`，移除缺省升级为 `human_verified` 的行为。
- `replacement_evaluation.py` 新增显式 overlay selector；默认不把 provisional 或 legacy 数据当正式标签。
- `blind_candidate_view()` 的盲化从“隐藏所有项目身份”改为“隐藏生成器身份和其他评审”，允许经过安全筛选的 canonical project URL。
- 迁移期间读取旧 schema，所有新写入使用版本化新 schema；不原地重写用户历史文件。

## 10. Reporting

按 candidate、expert profile、provider、family 和 generator family 计算覆盖与一致性。统计函数返回 value、sample size 和 warnings，不满足解释条件时 value 为 null。

新增关键指标：

- verified-independence coverage；
- evidence sufficiency / investigation completion；
- pairwise 和 overall agreement；
- third-vote 与 unresolved rate；
- 同 provider vs 跨 provider 差异；
- 同 family（仅诊断数据）vs 跨 family 差异；
- 未来 model/human agreement。

## 11. Rollout and Rollback

1. **Shadow**：新专家闭环只写新记录，不影响现有 benchmark；
2. **Offline overlay**：可选择 `model_expert_consensus` 重算指标，与旧结果并列；
3. **Read-only UI**：展示证据、票和共识来源；
4. **Controlled use**：明确策略开启后作为 selection 辅助信号；
5. **Human calibration**：未来抽样人审后评估偏差。

通过 feature flag 隔离新 runner、外部调查和 overlay。回滚时停止新 Job 并切回旧读取路径；append-only 新记录保留，不删除或伪装成旧格式。

## 12. Key Trade-offs

- **跨 provider 优先而非绝对强制**：提高认知多样性，但在 Provider 不足时允许明确降级；降级不能冒充已验证共识。
- **无万能裁判模型**：降低单一模型覆写独立证据的风险；代价是部分案例会停在 `needs_adjudication`。
- **受控 host-side tools**：比开放浏览器安全、可审计；代价是复杂动态页面覆盖较低。
- **先兼容文件型存储**：缩小首个切片；领域接口和事件模型为后续 SQLite/WAL 或数据库迁移留边界。
