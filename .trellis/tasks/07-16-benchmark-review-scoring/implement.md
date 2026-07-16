# 多模型专家 Agent 评分闭环：实施计划

> 本文件仅定义执行顺序。用户批准并通过 `task.py start` 前，不实施以下业务代码。

## Phase 0 — Baseline and Safety

- [ ] 在隔离工作树内记录当前相关文件 diff，尤其保护 `src/agent/web/app.py` 的已有 staged/unstaged 工作。
- [ ] 运行现有 expert review、blind judging、replacement evaluation 相关测试，保存 baseline 失败清单。
- [ ] 建立 feature flags，默认关闭新 expert orchestration、外部调查和 consensus overlay。
- [ ] 确认新代码不读取或序列化 API key、token 和代理凭据。

Validation:

```powershell
pytest tests/test_expert_grading.py tests/test_expert_impact.py tests/test_expert_jobs.py tests/test_expert_pool_registry.py tests/test_expert_review_api.py tests/test_benchmark_review_template.py -q
```

## Phase 1 — Domain Models and Provenance

- [ ] 新增 `src/agent/benchmark_review/` 领域包。
- [ ] 定义 `ExpertModelProfile`、`CandidateGenerationIdentity`、`EvidencePackage`、`ExpertJudgment`、`ConsensusRecord` 和来源枚举。
- [ ] 为结构化记录增加 schema/version 校验、规范化 JSON 和 hash。
- [ ] 明确 `model_expert_provisional`、`model_expert_consensus`、`needs_adjudication`、`human_verified`、`legacy_unverified`。
- [ ] 加入序列化安全测试，确保 credential ref 之外的秘密无法进入公共输出。

Tests:

- [ ] profile round-trip 与敏感字段剥离；
- [ ] Evidence Package 稳定 hash；
- [ ] 模型票不能反序列化成 human grade；
- [ ] hard-gate 枚举与旧 `review/unknown` 兼容映射。

## Phase 2 — Model Registry and Independence Policy

- [ ] 实现 provider-neutral profile registry，但各 Provider 调用仍使用其官方 SDK/适配器。
- [ ] 支持 requested/resolved model、family、provider、endpoint identity 和 verification status。
- [ ] 实现候选生成模型冲突检查和 pair/third-expert 选择。
- [ ] 对当前代理别名映射提供显式 attestation 配置；无法验证时标为 unverified。
- [ ] 在 Job 创建前返回可解释的独立性错误或降级原因。

Tests:

- [ ] 同 resolved model 不独立；
- [ ] 不同别名映射到同 resolved model 不独立；
- [ ] 同 family 不满足首评 pair；
- [ ] 与 generator family 相同被排除；
- [ ] 跨 family/provider 的 verified pair 优先；
- [ ] profile 不足时不生成伪共识。

## Phase 3 — Evidence Package and Safe Exploration

- [ ] 实现 Evidence Package Builder，纳入原始 Prompt、项目材料、canonical URL、候选和 hard-gate 事实。
- [ ] 重构盲化：隐藏 generator/runtime/reviewer 信息，允许安全的项目身份和 URL。
- [ ] 实现 URL/network policy：协议限制、DNS/IP 检查、重定向复验、响应大小与超时。
- [ ] 提供受控 web search/fetch 和 evidence blob 工具接口。
- [ ] 记录引用、获取时间、HTTP/工具状态和内容 hash。
- [ ] 明确将网页内容标记为不可信数据，抵御 prompt injection。

Tests:

- [ ] Evidence Package 不泄漏生成模型、runtime、其他专家意见或秘密；
- [ ] 允许 canonical public URL；
- [ ] 阻断 localhost、私网、link-local、metadata IP 与重定向绕过；
- [ ] 页面不可达和超时产生结构化失败，不伪造证据；
- [ ] 相同规范化输入产生相同 package hash。

## Phase 4 — Rubric and Expert Runner

- [ ] 将五维 rubric、0–3 grade anchors 和 Prompt 版本化。
- [ ] 定义严格的专家输出 schema。
- [ ] 实现单专家 Runner：相同 Evidence Package、独立上下文、受控工具、有限重试和 usage/error 记录。
- [ ] Claude Provider 使用官方 `anthropic` Python SDK；复杂判断使用 adaptive thinking，长调用使用 streaming，输出使用官方 structured output 能力。
- [ ] 其他 Provider 通过同一 Runner protocol 接入，不在领域层硬编码某一家返回格式。
- [ ] `insufficient_evidence` 时允许 null grade，并保留缺失信息。

Tests:

- [ ] 合法结构化输出被接受；
- [ ] 非法 grade、缺维度、伪引用被拒绝；
- [ ] hard fail 与高软分并存时处置仍为 fail；
- [ ] 工具失败、模型超时、schema 失败可恢复并留审计事件；
- [ ] 专家 Prompt 不含 generator identity 或其他票。

## Phase 5 — Deterministic Consensus and Job Orchestration

- [ ] 实现两票首评的并行调度。
- [ ] 实现逐维分歧分类、第三票触发和 deterministic consensus。
- [ ] 复用/适配 `ExpertJudgeJobManager` 的进度、重试、取消与重启恢复。
- [ ] 对 run/judgment ID 实现幂等合并，防止重试重复计票。
- [ ] 三票仍无法收敛时保存 `needs_adjudication`。
- [ ] 独立性不足、低证据覆盖或身份 unverified 时禁止升级共识。

Tests:

- [ ] 两票一致形成共识；
- [ ] 一档可接受差异按版本化规则聚合；
- [ ] hard 分歧、相差大于一档、低置信度和证据冲突触发第三票；
- [ ] 同底层模型的两票不能构成多数；
- [ ] 三票无可靠多数进入 `needs_adjudication`；
- [ ] 重启恢复不重复调用或重复计票；
- [ ] 公共 Job JSON 不含凭据。

## Phase 6 — Store, Legacy Migration, and Export

- [ ] 建立 append-only review events 与可恢复 snapshot；第一阶段按接口封装现有文件型存储并使用原子替换。
- [ ] 分离 model judgments、human verification 和旧 human_grades。
- [ ] 修改 `scripts/compile_discovery_blind_judgments.py`，取消无来源数据默认升级为 `human_verified`。
- [ ] 修改 `replacement_evaluation.py`，要求显式选择 overlay 来源。
- [ ] 为旧 reviewed pool 提供非破坏性读取迁移，无法证明来源时标为 `legacy_unverified`。

Tests:

- [ ] 旧文件可读但不会被自动提升；
- [ ] 新模型共识只导出为 `model_expert_consensus`；
- [ ] 只有显式真实人审路径能写 `human_verified`；
- [ ] provisional、needs_adjudication、legacy 默认不进入正式 overlay；
- [ ] 写入中断不破坏上一完整 snapshot。

## Phase 7 — API and Review UI

- [ ] 在 `web/app.py` 增加薄 API 适配，不在路由层实现聚合规则。
- [ ] UI 增加专家 profile、实际模型身份验证状态、证据来源、调查状态、逐维评分和共识状态。
- [ ] 明确区分 Model Expert 与 Human Verification。
- [ ] 对隐藏字段做服务端过滤，不能依赖前端不显示。
- [ ] 保留 Job 启动、取消、恢复和错误展示。

Tests:

- [ ] API 权限和字段脱敏；
- [ ] expert 首评视图看不到 generator/其他票；
- [ ] developer/admin 视图可审计完整 provenance，但仍不返回秘密；
- [ ] 模板显示 `model_expert_consensus` 而不是“人工已验证”；
- [ ] 原有 expert review API 兼容测试通过或有明确迁移断言。

## Phase 8 — Reporting and Offline Evaluation

- [ ] 扩展 coverage、agreement、kappa、hard agreement、third-vote、unresolved 和 evidence metrics。
- [ ] 按 provider、family、generator family 分层分析。
- [ ] 输出 sample size、可解释性警告和 null 指标。
- [ ] 使用 consensus overlay 重算 NDCG@5、high-relevance recall 和 replacement impact。
- [ ] 增加未来 HumanVerificationRecord 后的 model/human agreement 接口。

Tests:

- [ ] 小样本、无重叠、全同分、身份 unverified 和证据不足警告；
- [ ] 统计分母正确且不把 provisional 当 consensus；
- [ ] overlay 对比不改变 hard-gate 结果。

## Phase 9 — End-to-End Validation and Rollout

- [ ] 构造至少三种不同 expert profile 的离线 fixture/fake adapter，覆盖一致、分歧、证据不足和身份冲突。
- [ ] 运行完整 expert review 与 discovery/replacement 测试集。
- [ ] 进行一次服务重启恢复测试。
- [ ] 检查审计记录能从最终共识追溯到 package hash、每张票和引用。
- [ ] 默认保持 shadow mode；由显式配置开启 offline overlay。
- [ ] 文档化 feature flags、凭据配置、网络策略、回滚步骤和限制。

Suggested validation:

```powershell
pytest tests/test_expert_grading.py tests/test_expert_impact.py tests/test_expert_jobs.py tests/test_expert_pool_registry.py tests/test_expert_review_api.py tests/test_benchmark_review_template.py -q
pytest tests/test_discovery_agentic.py tests/test_discovery_value_scoring.py -q
pytest -q
```

## High-Risk Files and Review Gates

- `src/agent/web/app.py`：当前可能同时存在 staged/unstaged 用户修改；实施前逐段比对，禁止整文件覆盖。
- `src/agent/web/expert_review/grading.py`：`human_verified` 语义迁移风险高。
- `src/agent/web/expert_review/pool_registry.py`：盲化边界改变，必须做泄漏测试。
- `src/agent/web/expert_review/jobs.py`：重启、重试、并发和凭据安全。
- `scripts/compile_discovery_blind_judgments.py`：旧数据来源升级行为必须显式改变。
- `src/agent/discovery/replacement_evaluation.py`：正式 benchmark 门禁，默认必须保守。
- 外部 URL fetch：SSRF、重定向、prompt injection 和证据留存。

每个 Phase 完成后运行对应小测试；Phase 5、6、7 后运行全部 expert review 测试；最终运行完整测试集。若 baseline 已有失败，报告新旧差异，不把已有失败误归因于本任务。

## Rollback Points

- 新领域包和存储 schema 均由 feature flag 隔离。
- 每次迁移只新增显式字段/overlay，不原地删除旧 pool。
- 若 Expert Runner 不稳定，关闭 orchestration flag，旧 Job/页面仍可读取。
- 若外部调查出现安全或稳定问题，单独关闭 web tools，专家可基于已采集 Evidence Package 返回 `partial/insufficient_evidence`。
- 若 benchmark overlay 异常，切回默认无 overlay；hard gate 和 heuristic 路径不受影响。

## Planning Exit Gate

开始实施前必须满足：

- [ ] 用户审阅并批准 `prd.md`、`design.md`、`implement.md`；
- [ ] PRD convergence pass 完成；
- [ ] `implement.jsonl` 与 `check.jsonl` 含真实、当前的上下文条目；
- [ ] Trellis task 通过 `task.py start` 进入 `in_progress`；
- [ ] 实施工作在隔离工作树中进行。
