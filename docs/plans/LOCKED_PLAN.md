---
document: LOCKED_PLAN
locked_at: 2026-07-22T08:43:04Z
locked_by: user (explicit "锁定")
codex_agent_id: bdfdb979-1e5d-47cc-b08e-b2d6adace131
plan_status: LOCKED
implementation_status: WAVE2_3_PASS_WAVE4_PASS_WAVE5_PARTIAL_WAVE6_IN_PROGRESS
wave2_grok: PASS
implementation_note: WAVE3_IN_PROGRESS
wave1_grok: PASS
wave1_report: docs/plans/WAVE1_REPORT.md
wave1_review: docs/plans/_GROK_WAVE1_REVIEW.md
selected_architecture: 方案 2（灵活智能层 + 薄 Authority Plane + 开放 RepairProposal）
business_success_definition: 符合科学要求，并且材料可进入 dataset build（build-ready）
progress_only: 找到候选或完成候选审查不算任务完成
grok_verdict: PASS
source_draft: docs/plans/_CODEX_PHASE1_PLAN_DRAFT.md
source_review: docs/plans/_GROK_PHASE1_PLAN_REVIEW.md
worktree: E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning
rules:
  - 未收到编排指令 IMPLEMENT WAVE N 前禁止改 src/ 业务代码
  - 禁止 Claude Code / Gemini agents
  - Codex = 工人 gpt-5.6-sol high；Grok = 监工
  - 禁止案例特判；修 H1-H8 共性
  - 禁止 reset/clean 覆盖用户已有脏 worktree 改动
---

# LOCKED PLAN — 自主 Discovery Agent（方案 2）

## 0. 锁定声明

用户于对话中明确回复 **「锁定」**。本文件为 Phase 1 之后 **唯一权威实现依据**。  
正文主体来自 Codex 方案 2 + build-ready 成功定义修订稿；下列 **锁定补丁** 高于正文冲突处。

**实现尚未开始。** 进入编码须编排下发：`IMPLEMENT WAVE 1`（或用户明确说开始 Wave 1）。

---

## 1. 用户锁定的非协商点

1. **架构 = 方案 2**：灵活智能层（Agents SDK）+ **薄 Authority Plane**（验收/授权，不是科学硬规则机器人）。
2. **业务成功 / 毕业** = **build-ready**：符合科学要求，且材料 **能进入接下来的数据集构建**。  
   仅候选 / 仅审查 = **中间进展**，不算完成；UI 不得因此打成功/修复成功绿勾。
3. **Repair**：开放 `RepairProposal` + 可扩展 capability registry；强制可计算 `success_metric` + pre/post **delta**；连续无进步停止。
4. **单写者对话**（ADR 0002）与 hard **fail-closed**、soft 不变 hard 保持。
5. **Grok 监工**：每 wave PASS 后才能下一波；Codex 禁止自评 merge-ready。

### 毕业公式（权威平面）

```text
任务成功 ⇔
  hard 约束 fail-closed 通过
  AND 证据支持关键 claim（scope 正确）
  AND BuildReadyPackage / 业务完成决定 = 可进入 dataset build
  AND 非「仅候选列表」冒充完成
```

```text
repair_succeeded ⇔
  最新 audit 支持继续/ready（按实现定义）
  AND 业务完成决定为 build-ready 成功
  AND 对应 delta/package 证据齐全
```

禁止：`Runner` 返回 / HTTP 200 / 模型文案 ⇒ 自动成功。

---

## 2. 锁定补丁（Grok MF + 用户成功定义对齐）

### LP1 — Capability 加法扩展（原 MF1）
- 初始 registry 可含：`search_expand`, `inspect`, `materialize_evidence`, `recompute_validity`, `refresh_auth_context`, `select_manifest`, `stop_with_limitations`, `ask_user_blocking_question` 等。
- 运行时 Agent 可对新 intent **组合已有 primitives**。
- 新增 primitive 必须 registry 注册 + 测试；禁止 `if immunopeptidomics` 类业务特判。
- Wave 3 验收：至少一个 **未硬编码业务 kind 名** 的 intent，映射到已有 primitives 后可执行。

### LP2 — success_metric 白名单（原 MF2）
Wave 2 必须交付权威可观测字段表（聚合 + 比较方向）。  
不可解析 metric → **reject**，不得 degrade 成空成功。

### LP3 — no-progress signature（原 MF3）
默认连续 **2** 次相同 signature 无 delta → 强制停。  
signature ⊇ `approved_capability_set + parameter_hash + issue_code_set + metric_id`。  
仅改 intent 文案、能力/参数等价 → 同一 signature。

### LP4 — 脏 worktree（原 MF4）
禁止 reset/clean；触碰 `app.py`/前端须合并保留用户改动与 one-writer 测试；冲突先停问用户/编排。

### LP5 — 中间态 vs 毕业（原 MF5 + 用户「对」）
用户语言优先：
- **进行中**：找到候选、完成审查、阻塞原因（缺证据/缺文件/硬冲突等）
- **已毕业**：build-ready（可进入数据集构建）

工程内部若保留 candidates_only / reviewed / AI-ready 等实现细节，必须服从：  
**唯一业务完成 = build-ready**；不得削弱 build-ready 门，也不得用中间态事件发成功绿勾。

### LP6 — Wave 2 须先交付 issue→capability/metric/risk 表
Wave 3 只实现状态机与开放 proposal，不再现场设计映射。

---

## 3. 正文（Codex A–E，方案 2 + build-ready）

下列为锁定正文（自草案，标题改为锁定版）。

# Codex Phase 1 架构计划草案

本轮结论：建议锁定“单写者 Dialogue Manager + 灵活智能层（Agents SDK）+ 薄 Authority Plane + 版本化发布/证据/修复合约”的方案 2。目标体感是 Codex 级自主：Agent 继续拥有科学探索、搜索规划、项目判断、动态议程、repair 创意和限制说明；Authority Plane 不写科学 if-else、不替 Agent 选研究路线，只守 hard/soft、能力风险、可计算度量、delta、build-ready package 和诚实终态。默认且唯一业务毕业标准是：**符合科学要求，并且材料能够进入接下来的 dataset build（build-ready）**。只找到候选或只完成候选审查属于进行中进度或带进展的阻塞，不算任务完成，也不能触发交付成功/修复成功绿勾。`ConstraintBinding` 不应重复现有 `ScientificConstraint`；“Runner 返回”绝不能等同于“repair succeeded”。本草案仍处于尚未用户锁定状态。

本轮仅分析，未修改任何产品代码。

## A. SDK fit assessment

### A1. 当前已经正确使用的部分

1. **Dialogue Manager 单写者设计正确，应保持不动。**

   `src/agent/web/app.py` 中的 Dialogue Manager 是唯一可调用 `update_strategy` / `confirm_strategy` 的 Agent；Scientific Advisor 通过 `advisor_agent.as_tool()` 只读参与。它符合 Agents SDK 的 manager-style workflow，也符合 ADR 0002。

2. **Advisor 使用 agents-as-tools 正确。**

   SDK 官方建议：当 Manager 保留最终回复权、Specialist 只执行有界分析时使用 `Agent.as_tool()`。当前 Advisor 明确接收 bounded context、返回 structured output、不能写策略，契合这一模式。

3. **Session 的使用边界是合理的。**

   当前 Dialogue Manager 的 `Runner.run()` 不直接挂载 Session，先由服务端完成承诺、选项范围、语义和版本校验，再把规范化回合写入 `SQLiteSession`。这避免原始 tool output 在校验前进入长期会话。应保留此设计，不能为了“更 SDK-native”而把 Session 变成授权边界。

4. **执行侧已有可复用的权威基础。**

   `src/agent/control_plane/discovery.py` 已拥有预算、审计、manifest 选择门、grant、idempotency 和 fail-closed 校验；`src/agent/control_plane/models.py` 已有 `DiscoveryQualityAudit`、`DiscoveryRepairAction`。这些能力应收敛成薄 Authority Plane：验证提议、守预算、量 delta、签发 publication 资格，而不是扩张成替 Agent 规划科学探索的规则引擎。

5. **Structured output 和 typed function tools 应继续使用。**

   Advisor 的 `output_type`、Manager 的 typed action tools、项目判断的 Pydantic 输入都应保留。LLM 输出可成为策略、判断、搜索计划和开放 repair proposal，但不能直接成为约束升级、能力执行许可、发布资格或修复成功证明。

### A2. 需要调整的 SDK 使用方式

| SDK 能力 | 锁定用法 |
| --- | --- |
| Multi-agent | 对话侧继续 Manager + Advisor-as-tool；执行侧允许 Discovery Agent 在预算内调用搜索规划、项目判断和 repair specialist，但任何 specialist 都无 publication authority |
| Agents-as-tools | 用于主 Agent 保留探索与解释所有权的嵌套协作；不把每个科学步骤拆成固定 Python 分支 |
| `Runner` | 承载自适应搜索、判断和 repair 提议循环；必须有 typed proposal、预算、风险边界和可恢复状态，而不是只允许固定 action 名单 |
| Sessions | 只保存会话连续性或可恢复 SDK run；策略版本、确认指纹和 publication authority 仍由应用数据库掌握 |
| Guardrails | 用于输入、输出和具体 function tool 周围的自动检查；hard/soft、证据范围、capability admission 和发布资格仍由 Authority Plane 负责 |
| Tracing | 执行 run 保留 SDK trace；repair action 使用 custom span 记录 action、pre/post metrics 和 delta |
| Handoffs | 仅在另一个 Agent 真正接管后续用户回复时使用；repair banner 或后台阶段变化不是 handoff |
| Results / interruptions | 用于 SDK 工具审批和恢复同一个 run；不能代替产品确认或 publication approval |
| Structured outputs | 用于 query plan、project judgment、`RepairProposal`、scientific advice；自然语言可描述新 repair intent，但实际能力与成功度量必须结构化 |

官方依据：[Agents SDK](https://developers.openai.com/api/docs/guides/agents)、[Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration)、[Guardrails and human review](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals)、[Results and state](https://developers.openai.com/api/docs/guides/agents/results)、[Tracing](https://developers.openai.com/api/docs/guides/agents/integrations-observability#tracing)。

### A3. 必须留在薄 Authority Plane 的权力

智能层继续负责对话、策略、动态议程、搜索规划、项目判断、repair 创意和限制说明。以下“毕业证权力”不得交给 LLM、guardrail Agent、Session 或 handoff：

- confirmation fingerprint 与策略版本；
- hard / soft / open 的规范化和 provenance，尤其 hard conflict / hard unknown fail-closed；
- evidence scope 的解析、传播和冲突判断；
- capability primitive 的注册、风险、参数 schema、预算、幂等与授权；
- `success_metric_spec` 是否引用可计算的可信状态；
- repair 的 pre/post/delta 计算；
- no-progress signature 与默认连续 2 次无进步停止；
- horizon publication contract 和 package 边界；
- build-ready 材料是否真的能进入 dataset build；
- publication 是否达到业务毕业、`repair_succeeded` 是否允许发出；
- UI 终态及其权威指标。

目标运行形态：

```text
User
  → Dialogue Manager Agent（唯一策略 writer）
  → 确认指纹校验
  → Discovery Agent / Specialists（Agents SDK 灵活智能层）
      → 自适应 search / inspect / judge / explain
      → RepairProposal（开放 intent、能力请求、参数与度量）
  → Authority Plane（薄权威平面）
      → capability registry + risk/budget/metric validation
      → tools/services → pre/post delta → re-audit
      → EvidenceStore → PublicationContract → BuildReadyPackage/HorizonPackage
      → versioned authoritative events
```

## B. 对草案的批判与修订

### B1. 同意锁定的部分

- §1 North star 正确。
- §2 ADR 0001/0002、确认与搜索分离、option contract 都应保持。
- §3 H1–H8 是正确的通用问题分类。
- §4.3 horizon-specific publication 是解决 H1 的必要主轴。
- §4.5 `audit → Agent proposal → Authority admission → execution → delta → re-audit/stop` 是解决 H5 的正确形式。
- §4.7 judgment-qualified、build-ready 与中间进度指标必须分开。
- 真实 32/0/2408 run 只能作为考试夹具，不能进入产品条件分支。该事故代表“有进展但未毕业”：找到了 32 个候选、约 20 个完成判断，但 0 个达到可构建毕业；旧 UI 却把 repair Runner 返回和候选审查误画成完成绿勾。

### B2. 必须修改的部分

1. **不能从 LLM 自由循环矫枉过正成“硬规则机器人”。**

   Agents SDK 智能层应保留科学探索和 repair 提案权，可在预算内改变 query、复检、补证据或请求刷新上下文。Authority Plane 只判断 capability 是否安全可执行、metric 是否可计算、delta 是否真实、hard 是否守住、package 是否毕业；不得用领域 if-else 取代 Agent。`Agent.as_tool()` 仍用于主 Agent 保留探索与解释所有权的嵌套协作。

2. **不要新造一套平行的约束模型。**

   `src/agent/discovery/constraints.py` 已有 `ScientificConstraint`，包含 `dimension`、`operator`、`value`、`strength`、`scope`、`source`。应把它演进为统一 binding contract，并为第一类字段生成同一规范化视图，而不是再维护一套互不一致的 `ConstraintBinding`。

3. **`open` 是已解决选择，不是缺失字段。**

   统一 strength 必须支持 `hard | soft | open`。`open` 不参与排除，也不能被 agenda 当作未回答问题。

4. **Evidence scope 必须是解析规则，不只是枚举。**

   必须明确：

   - project evidence 不能自动证明每个 file；
   - assay evidence 只有在有明确 assay→file membership 时才能作用到相应文件；
   - file evidence 不能证明别的文件；
   - spectrum 级要求只能由 spectrum 或可验证的派生证据满足；
   - LLM judgment 本身不是原始证据。

5. **“所有 gate 都按 horizon 分支”会制造散落条件。**

   应建立唯一 `PublicationContractRegistry`，由它返回各内部阶段所需材料、资格规则、指标和终态。调用方消费统一 `PublicationDecision`，不要在 `app.py`、UI、audit、selection 中各写一套 `if run_horizon`。工程内部可暂时保留 `candidates_only` / `candidates_reviewed` 等旧 horizon 值用于兼容，但它们只能产出进度 package；默认且唯一业务毕业 package 是 build-ready。

6. **候选发现与候选审查必须明确降级为中间态。**

   内部 `candidates_only` / `candidates_reviewed` 可以汇报候选、inspection-backed judgments、限制和 unresolved follow-ups，但：

   - 已知 hard conflict 必须排除；
   - hard unknown 不能被标成 compliant；
   - fixed quota 未满足不能叫 complete；
   - unresolved 项目可列在单独区段，不能混入 qualified set；
   - 即使候选和 reviewed 数量大于零，只要 build-ready 为零，业务状态仍是 `progress` 或 `blocked_with_progress`，绝不是 `completed`。

7. **`repair_attempt_finished` 与 `repair_succeeded` 必须分离。**

   当前 `src/agent/control_plane/openai_agents.py` 在第二次 Runner 返回后发出 `discovery_quality_repair_completed`；`src/agent/web/app.py` 又把它翻译为成功文案。这正是 H5/H6。新的成功条件必须是：

   ```text
   hard 约束 fail-closed 通过
   AND 证据支持关键 claim
   AND publication contract 证明材料可进入 dataset build
   AND package 不只是候选列表或候选审查
   ```

8. **Tracing 不能机械要求“每个对话 turn 一个完整 SDK trace”。**

   当前对话 trace 有意关闭，以避免校验前的原始提案进入 trace。锁定方案应允许继续使用脱敏 application audit；只有证明敏感字段和未提交策略不会泄漏后，才启用完整 dialogue trace。

### B3. 应削减的过度范围

本计划阶段不做：

- 全仓库 agent 大拆分；
- `ConstraintBinding` 的跨语言 codegen；
- 因 repair banner 创建 SDK handoff；
- 为每个 task 建 plugin framework；
- 重写 PRIDE client、ranker 或完整执行管线；
- 将所有旧事件立即删除或重命名；
- 把 live Grok/DeepSeek 调用放入常规单元测试。

第一阶段只建立能证明 H1–H6 的最小纵切；H7、H8 随后独立收口。

## C. Architecture decisions

### C1. Discovery Orchestrator

选择：**Agents SDK 驱动的灵活智能层 + 薄 `Authority Plane`**。

Discovery Agent 可以在预算内自主决定如何搜索、何时换 query、复检哪些项目、怎样补证据、如何解释局限，也可以提出计划外的新 repair intent。它不是固定工作流上的文本生成器。Scientific specialists 可作为 tools 或 bounded sub-runs 参与，主 Agent 保留任务上下文和科学判断。

Authority Plane 不替 Agent 决定科学路线，只在能力调用和毕业边界上介入：

- 把 `RepairProposal.requested_capabilities` 映射到可扩展 capability primitives；
- 校验参数 schema、risk、预算、幂等和 hard 约束；
- 校验 `success_metric_spec` 能从权威状态计算；
- 执行后计算 pre/post/delta，并在连续 2 次同签名无进步时停止；
- 通过 `PublicationContract` 唯一签发进度 package 或 build-ready package；只有后者可毕业；
- 发出 UI 可依赖的权威事件。

这意味着状态机只保留极薄的生命周期与安全状态，例如 `awaiting_confirm`、`running`、`waiting_approval`、`blocked`、`published`；它不编码“某科学主题下一步必须 inspect 还是 search”。repair proposal 被拒绝后，Agent 可在剩余预算内重新规划；只有预算耗尽、hard 阻塞或无可执行 capability 时才诚实停止或请求用户决策。

### C2. `ConstraintBinding` 放置

选择：**共享 Pydantic contract，放在 `src/agent/discovery/constraints.py`；当前阶段不做 codegen。**

具体做法：

- 演进现有 `ScientificConstraint`，或为其提供兼容的 `ConstraintBinding` 类型；
- `DatasetRequest` 增加规范化 `constraint_bindings`；
- `hard_constraint_fields`、`labeling_hard` 暂时保留为 v1 输入/回放兼容投影；
- 新前端停止从“有具体值”推导 hard；
- TypeScript 先维护显式 mirror type，并用同一 JSON golden fixture 验证 Python/TS round-trip；
- codegen 只在 schema 稳定且出现第三个消费者后再评估。

建议字段：

```text
id, dimension, operator, value,
strength: hard | soft | open,
evidence_scope: project | assay | file | spectrum | portfolio,
evidence_required,
provenance: source + strategy_event_id + option_id,
rationale
```

### C3. Evidence materialization

选择：**新增版本化 `EvidenceStore` artifact，不继续向 `DiscoveredProject` / `DiscoveredFile` 塞更多重复 evidence map。**

最小记录：

```text
EvidenceObservation {
  observation_id,
  subject_kind,
  subject_id,
  dimension,
  observed_value,
  evidence_scope,
  source_kind,
  source_ref,
  membership_refs,
  confidence,
  observed_at
}
```

关键规则：

- `ProjectJudgmentInput.constraint_assessments` 只是待验证 claim；
- materializer 必须验证其 `evidence_refs` 存在、observed value 与原始记录相符；
- 通过后写入 `EvidenceStore`，manifest 只保存 observation refs；
- publication、audit 和 UI 都读取同一 `EvidenceView`；
- 不允许把 project observation 无条件复制到所有 file。

### C4. `DiscoveryRepairKind` 版本化

选择：**开放式 `RepairProposal` v2 envelope + 可扩展 capability primitive registry + 显式 v1 upgrader。**

- 永久保留 v1 action 名称可解析；
- v2 不要求模型先选中一张封闭的业务 action 表，而是提交：

  ```text
  RepairProposal {
    schema_version: "discovery-repair-proposal/v2",
    proposal_id,
    intent,
    rationale,
    requested_capabilities[],
    parameters,
    success_metric_spec,
    expected_delta_direction,
    risk_class
  }
  ```

- `success_metric_spec` 必须引用 Authority Plane 注册的可观测字段、聚合方式和比较方向；不得让模型上传代码、查询表达式或用自评文字作为 metric；
- capability primitive 使用加法扩展，例如：

  - `search_expand`
  - `inspect`
  - `materialize_evidence`
  - `recompute_validity`
  - `refresh_auth_context`
  - `select_manifest`
  - `stop_with_limitations`
  - `ask_user_blocking_question`

- registry 中每个 primitive 声明参数 schema、risk class、预算计费、幂等策略、执行 adapter、可用 metric families 和审计事件；扩展 registry 不需要新增某领域的 `if immunopeptidomics`；
- Authority Plane 可 `approve`、`degrade` 或 `reject` proposal。降级必须保持原 hard 约束并给出机器可读原因；例如未知 capability 可降级到 `ask_user_blocking_question` 或 `stop_with_limitations`；
- 未知 capability、不可计算 metric、越权参数或预算不足必须 fail-closed，禁止静默执行或静默记成功；
- `select_manifest` 即使被模型请求，也只有在最新 `PublicationDecision.build_ready == true` 且 build-ready package 满足 dataset build 入口边界时才能作为毕业选择执行；候选/审查 manifest 可以保存为进度 artifact，但不能改变业务完成状态；
- v1 `rescore_projects` 等 action 继续可回放，由 upgrader 映射到 v2 proposal/capability；不能让旧 action 默默获得新语义；
- 旧 `discovery_quality_repair_completed` 回放时只映射为 `repair_attempt_finished`，绝不能推断成功。

### C5. 默认且唯一业务毕业标准

选择：**任务成功只认 build-ready，不把候选发现或候选审查当成交付完成。**

对用户和 UI 使用以下公式：

```text
任务成功 ⇔
  hard 约束 fail-closed 通过
  AND 证据支持关键 claim
  AND HorizonPackage/build-ready 材料达到「可进入数据集构建」
  AND 非仅候选列表
```

这里的 **build-ready** 指通过规范化 manifest、证据、文件/角色/可访问性及当前 dataset builder 入口所需材料校验，能够安全进入下一阶段数据集构建；它不表示数据集已经构建完成、模型已经训练，也不授权绕过后续 builder 自身的校验。

工程内部旧词的固定解释：

- `candidates_only`：候选搜索进度，业务状态只能是 `running_progress` 或 `blocked_with_progress`；
- `candidates_reviewed`：已审查候选进度，仍不算毕业；
- 旧 `AI-ready`：历史内部术语，容易与“已经训练可用”混淆；主文和 UI 改称 `build-ready`，仅在兼容旧 schema/test 名时保留并注明含义；
- `BuildReadyPackage`：唯一能使 `BusinessCompletionDecision.succeeded=true` 的 package。

Authority Plane 必须同时输出两组信息：

1. 中间进度：搜到多少、审了多少、判断合格多少、缺证据/缺文件/硬冲突各多少；这些指标供 Agent 继续灵活 repair 或诚实停止。
2. 毕业指标：build-ready 项目/文件、package validation、dataset build 入口兼容性和未解决 blocker；只有这一组满足合约才能发出完成或修复成功事件。

真实 32/0 事故的正式解释是：32 个候选和约 20 个 judgment 是有价值的中间进展，但 build-ready 为 0，所以任务未毕业；把 repair Runner 返回渲染为成功，是 H6 假 UI，而不是一次部分成功。

### C6. Sacred green 与可重写测试

必须保持 green 的安全契约：

- `tests/test_discovery_agent_turn.py`

  - numeric/id/label option 采用存储 patch；
  - critic 不能添加或修改 Manager patch；
  - confirmation phase/fingerprint；
  - session continuity；
  - direct/internal route 不能绕过确认；
  - strategy change 使旧确认失效。

- `tests/test_agent_autonomous_discovery_defaults.py`

  - `plan_only` 不启动搜索；
  - 未接通的 downstream horizon 继续 fail-closed。

- `tests/test_discovery_quality_audit.py`

  - hard constraint 覆盖 passing judgment；
  - constraint evidence grounding；
  - file/portfolio scope；
  - budget ceiling 与 truthful stop。

- `tests/test_discovery_scientific_constraint_validity.py`

  - hard unknown/conflict fail-closed；
  - soft preference 只能排序，不能排除。

- `tests/test_discovery_mixed_acquisition_policy.py`
- `tests/test_discovery_sdrf_assay_evidence.py`
- confirmation fingerprint 的 Python/前端契约测试。

需要重写但不能删除安全意图的测试：

- 所有把旧 file-level `AI-ready` 术语与候选进度混成同一个状态的测试；
- `test_quality_audit_is_ready_only_after_inspection_scoring_and_file_evidence` 应拆成“进度可报告”与“build-ready 才毕业”两个断言；
- `test_quality_audit_rejects_delivery_when_project_or_files_still_need_review` 应保留为 build-ready sacred case，并新增“reviewed 有进展但未毕业”对照；
- 把“任意显式 acquisition value 都是 hard”当作正确行为的前后端测试；
- 把 `discovery_quality_repair_completed` 名称直接渲染为绿色成功的前端测试。

## D. Locked wave plan

共同门禁：本草案虽为 `READY_TO_LOCK`，但在用户明确说 lock 前不得实施任何 wave。锁定后每个 wave 由 Codex 实现，Grok 输出 `PASS/FAIL + MUST_FIX`；Grok PASS 后才能进入下一 wave。任何 wave 都不得自称 merge-ready。实现时必须先检查并保留当前脏 worktree，尤其不得覆盖用户已有的 `src/agent/web/app.py` 与测试改动。

### Wave 1 — 离线红灯夹具

**入口**

- 计划已由用户锁定；
- Grok 通过计划审计；
- 记录当前 sacred-green baseline。

**工作**

建立两个脱敏、无网络 scenario bundle：

1. 真实 32/0/2408 摘要：内部阶段为已审查候选、DDA soft、label-free hard；期望业务结果明确为“32 个候选、约 20 个 judgment 的中间进展，0 build-ready，未毕业”。
2. 非免疫 synthetic RT/PSM 场景：同一证据先产生候选/审查进度，再经补证据与文件材料形成 build-ready package；包含 soft acquisition、project/assay evidence 和不完整 file evidence。

同时增加 scripted repair fixture：开放 proposal 映射、未知 capability、不可计算 metric、连续 2 次无进展、stale search context、attempt finished 但 audit 仍未 ready。

**退出**

- 新测试只因缺少目标 contract/controller 而红；
- 不以 immunopeptide 字符串判断；
- 真实夹具必须保留候选和 reviewed 进度，但 build-ready 为零时 `BusinessCompletionDecision.succeeded == false`，且无成功绿勾；
- synthetic 正向夹具只有在材料可进入 dataset build 后才毕业，用作成功对照；
- fixture 能区分“新 intent 可映射到已有 capability”与“任意未注册副作用”；
- sacred tests 不退化。

**文件触点**

- `tests/fixtures/discovery/`
- `tests/test_discovery_publication_contracts.py`
- `tests/test_discovery_repair_controller.py`
- `tests/test_discovery_quality_audit.py`
- `frontend/benchmark-review/src/grill-tree.test.ts`
- `frontend/benchmark-review/src/DiscoveryProgressMessage.test.tsx`
- `frontend/benchmark-review/src/CodexTimeline.test.tsx`

### Wave 2 — Contract spine：H1–H4

**入口**

- Wave 1 fixtures 获 Grok 确认；
- 明确 v1 payload/replay 兼容表。

**工作**

- 统一 hard/soft/open binding；
- 停止前端从具体 DDA/DIA 自动推导 hard；
- 引入 `EvidenceStore`；
- 建立唯一 `PublicationContractRegistry`；
- 明确定义 package 边界：进度 package 与 `BuildReadyPackage` 分离，均包含 schema、required artifacts、eligible/unresolved/excluded 分区、status 和 provenance；
- 将内部 `candidates_only`、`candidates_reviewed` 映射为 progress package，禁止映射为业务完成；
- 将历史内部 `AI-ready` 含义收敛并更名为 build-ready contract；未接通的更深 downstream executor 继续 fail-closed；
- `PublicationDecision` 输出 progress metrics、build-ready eligibility、unresolved、excluded 和 limitations；
- 新增 `BusinessCompletionDecision`，其成功值只能由有效 `BuildReadyPackage` 产生；
- 产出通用 `issue → capability primitives` 映射：每类 audit issue 声明可请求的 capability、候选 metric families、最低 evidence scope 与 risk ceiling，但不规定 Agent 必须采用哪条科学修复路线。

**退出**

- 两个 Wave 1 scenario 的 H1–H4 测试转绿；
- 候选/审查进度不再因缺 build-ready 材料而从报告中消失，但也绝不能因此被标记完成；
- hard unknown/conflict 仍不能进入 qualified set；
- soft mismatch 只影响 rank/limitations；
- 前后端 binding round-trip 一致；
- v1 `hard_constraint_fields` 仍可加载和回放；
- issue→capability 映射不包含 immunopeptide 等案例分支，并可供 Wave 3 Authority Plane 直接校验；
- 只有 `PublicationContractRegistry` 能产生 `BuildReadyPackage` 并驱动 `BusinessCompletionDecision`；Agent/Runner 文案和 progress package 不能绕过该边界。

**文件触点**

- `src/agent/discovery/constraints.py`
- `src/agent/discovery/models.py`
- `src/agent/discovery/evidence_store.py`
- `src/agent/discovery/publication.py`
- `src/agent/discovery/validity.py`
- `src/agent/discovery/scoring.py`
- `src/agent/control_plane/models.py`
- `src/agent/control_plane/capabilities.py`
- `src/agent/control_plane/discovery.py`
- `src/agent/web/app.py`
- `frontend/benchmark-review/src/intent-spec.ts`
- `frontend/benchmark-review/src/grill-tree.ts`
- `frontend/benchmark-review/src/grill-tree.test.ts`
- `tests/test_discovery_publication_contracts.py`
- `tests/test_discovery_quality_audit.py`
- `tests/test_discovery_scientific_constraint_validity.py`
- `tests/test_discovery_sdrf_assay_evidence.py`
- `tests/test_web_discovery.py`

### Wave 3 — 开放 Repair 提议 + 薄 Authority Plane：H5、H7

**入口**

- Wave 2 publication/evidence contract 与 `BuildReadyPackage` 边界稳定；
- 通用 issue→capability/metric/risk 映射已经 Grok 验收。

**工作**

保留 Agent 的 repair 创意，由模型提出开放 proposal；Authority Plane 只做能力、度量、风险和毕业校验：

```text
audit
→ Agent proposes RepairProposal
→ Authority maps requested capabilities to registered primitives
→ validate metric / hard constraints / risk / budget / idempotency
→ approve | degrade | reject（必须解释）
→ capture authoritative pre metrics
→ dispatch approved capability composition
→ capture post metrics
→ calculate delta
→ re-audit
→ Agent replans | publish | ask user | honest stop
```

每个 attempt 保存：

- `proposal_id`、开放 `intent` 与 `rationale`
- requested/approved/degraded capability 列表
- risk class、parameter hash、authority decision/reason
- `success_metric_spec` 与 expected direction
- pre/post metrics
- delta
- issue codes before/after
- action outcome
- no-progress signature

能力原语对应的可计算 metric family 示例：

- `search_expand`：新增 unique candidate、coverage 或有效 evidence target；
- `inspect`：未解析 evidence/assessment 数下降；
- `materialize_evidence`：新增已验证 observation；
- `recompute_validity`：gate 状态或 build-ready eligible 数发生预期变化；
- `refresh_auth_context`：获得有效新 search/grant context 并完成原动作重试；
- `ask_user_blocking_question`：产生新的、与当前 blocker 绑定的用户授权请求，不伪造运行进步。

模型可以提出新的 intent、参数组合及多个 primitive 的组合，不要求提前穷尽所有业务 kind；但不能提交任意代码、任意 shell、未注册副作用或自定义 metric 执行逻辑。未知 capability 必须拒绝，或显式降级为 `ask_user_blocking_question` / `stop_with_limitations`。

同一 no-progress signature 的默认上限为 **2 次连续无进步**。达到上限后 Authority Plane 必须阻止继续重复，发出 `repair_no_progress` 和 `repair_incomplete`/`repair_blocked`；Agent 可改提议，但不得换措辞后重复等价动作。`select_manifest` 只有在最新 `PublicationDecision.build_ready == true` 且 `BuildReadyPackage` 完整时，才可作为业务毕业选择执行。

**退出**

- 保留 Runner 作为 repair proposal generator，但删除或禁用“第二次 Runner 返回即成功”的路径；
- 一个此前未硬编码的新 repair intent，只要能映射到已注册 primitives 且 metric 可计算，就能获批执行；
- 未知 capability、不可计算 metric、越权参数和预算不足均可解释地拒绝或降级；
- stale search/grant 自动刷新一次并有边界；
- 相同 no-progress signature 连续 2 次无 delta 必然停止；
- hard conflict / hard unknown 始终 fail-closed，soft 不因 repair proposal 自动升级；
- repair 中间 delta 可发 `repair_progressed`，但整体成功只由 ready audit + eligible `BuildReadyPackage` 决定；
- proposal 被拒绝不会伪装成 tool success 或绿色进度；
- H5/H7 fixture 转绿。

**文件触点**

- `src/agent/control_plane/repair.py`
- `src/agent/control_plane/capabilities.py`
- `src/agent/control_plane/models.py`
- `src/agent/control_plane/discovery.py`
- `src/agent/control_plane/openai_agents.py`
- `src/agent/control_plane/sdk_runtime.py`
- `tests/test_discovery_repair_controller.py`
- `tests/test_discovery_quality_audit.py`
- `tests/test_discovery_grant_execution.py`
- `tests/test_discovery_fault_injection.py`

### Wave 4 — 诚实事件与 UI：H6

**入口**

- Authority Plane 已能产生 typed attempt/result、progress metrics 和 `BusinessCompletionDecision`；
- old-event replay adapter 已定义。

**工作**

新增或规范化事件：

- `repair_attempt_started`
- `repair_attempt_finished`
- `repair_progressed`
- `repair_no_progress`
- `repair_succeeded`
- `repair_incomplete`
- `repair_blocked`
- `build_ready_succeeded`
- `blocked_with_progress`

`repair_succeeded` 与 `build_ready_succeeded` 都只能在本次 repair 后达到 build-ready 毕业时发出；如果只增加候选、judgment 或证据但仍未 build-ready，只能发 `repair_progressed`。UI 不根据事件名称、Runner 返回或 HTTP 200 画绿勾。显示：

- searched projects；
- inspected/assessable projects；
- judgment-qualified projects；
- build-ready projects/files；
- unresolved projects by cause；
- file count 仅作为 drill-down。

**退出**

- 真实 32/0 场景显示“找到 32、审查约 20、build-ready 0、卡在缺证据/缺文件/硬冲突”，状态为进行中或 `blocked_with_progress`，不显示交付/修复成功绿勾；
- `0 build-ready + repair_required` 不可能显示绿色成功；
- 候选数、审查数或 judgment-qualified 数大于零只能显示中性进度，不触发 completed；
- legacy `discovery_quality_repair_completed` 只显示“尝试结束，结果待审计”；
- UI success 只认 Authority Plane 的 `BusinessCompletionDecision` 与 build-ready 指标；
- H6 fixture 转绿。

**文件触点**

- `src/agent/control_plane/models.py`
- `src/agent/control_plane/openai_agents.py`
- `src/agent/web/app.py`
- `frontend/benchmark-review/src/workflow-api.ts`
- `frontend/benchmark-review/src/grill-tree.ts`
- `frontend/benchmark-review/src/DiscoveryProgressMessage.tsx`
- `frontend/benchmark-review/src/CodexTimeline.tsx`
- `frontend/benchmark-review/src/grill-tree.test.ts`
- `frontend/benchmark-review/src/DiscoveryProgressMessage.test.tsx`
- `frontend/benchmark-review/src/CodexTimeline.test.tsx`

### Wave 5 — 动态科学议程：H8

**入口**

- H1–H7 核心执行语义稳定；
- 不再需要通过 dialogue patch 弥补 execution contract。

**工作**

- 把 critical agenda 作为 `TaskProfile` 数据，而不是 `app.py` 中扩张的条件树；
- 每项声明：触发条件、阻塞 horizon、决策变量、所需 repository evidence；
- chimeric 作为首个验收场景：label provenance / relabel tolerance 优先于 optional labeling；
- 保持动态单问题对话，不恢复 Q1–Q10；
- Advisor 仍只读，Manager 仍唯一 writer。

**退出**

- chimeric training request 在 optional labeling 前解决 label feasibility；
- browse-only 不被训练议程阻塞；
- open choice 被视为已解决；
- numeric option、one-writer、confirmation tests 全绿。

**文件触点**

- `src/agent/discovery/task_profiles.py`
- `src/agent/discovery/agenda.py`
- `src/agent/web/app.py`
- `docs/discovery-agent-guidance.md`
- `tests/test_discovery_agent_turn.py`
- `tests/test_discovery_task_build_plan.py`
- `frontend/benchmark-review/src/agent-turn.test.ts`

### Wave 6 — Replay、属性测试与抽检

**入口**

- H1–H8 targeted suites 全绿；
- 所有 schema/event upgrader 已存在。

**工作**

- v1 run、audit、repair event 回放；
- property tests：

  - soft 永不触发 hard exclusion；
  - unknown hard 永不成为 pass；
  - project evidence 不会无 membership 下沉至 file；
  - 相同 no-progress signature 连续 2 次无 delta 后停止；
  - success event 必须对应有效 `BuildReadyPackage` 与 `BusinessCompletionDecision.succeeded=true`；
  - 任意非零候选/审查数量在 build-ready 为零时仍不得产生 success event；

- frontend production build；
- discovery/control-plane/PRIDE 受影响测试；
- 一个真实 Grok turn；DeepSeek 仅作为已有配置下的可选兼容 smoke，不作为合并必需条件；
- 禁止在单元测试循环调用 PRIDE。

**退出**

- 两个通用 fixture、旧 run replay、sacred greens 全部通过；
- 无 secret、run bundle 或 dialogue DB 被加入 git；
- Grok 最终验收。

**文件触点**

- `tests/test_discovery_runtime_provenance.py`
- `tests/test_discovery_replacement_evaluation.py`
- `tests/test_discovery_runtime_evaluation.py`
- `tests/test_discovery_fault_injection.py`
- `tests/test_discovery_agent_turn.py`
- `tests/test_discovery_quality_audit.py`
- `tests/test_discovery_repair_controller.py`
- `frontend/benchmark-review/src/strategy-fingerprint.test.ts`
- `frontend/benchmark-review/src/workflow-api.test.ts`

## E. Explicit non-goals and anti-patterns

### 非目标

- 不重写 PRIDE client；
- 不训练 learned ranker；
- 不重构整个 web app；
- 不引入第二个策略 writer；
- 不在本轮执行 dataset build 或模型训练；本轮只建立并验证能进入 dataset build 的 build-ready 入口合约；
- 不做跨语言 schema codegen；
- 不把 live provider 调用放入普通测试；
- 不以此次架构工作处理多租户、鉴权或 SaaS 权限。

### 明确禁止的反模式

- `if immunopeptidomics and candidate_count == 32` 一类案例特判；
- 把方案 2 退化成由 Python 决定搜索、复检和科学 repair 顺序的“硬规则机器人”；
- 在 Authority Plane 中写 immunopeptide、RT、PTM 等领域 if-else 来替代 Agent 判断；
- 因字段有具体值就自动设为 hard；
- 把 soft task profile 推荐转成 execution filter；
- 把 `open` 当成 missing；
- 把 project evidence 复制给所有 files；
- 把 LLM judgment 本身当作证据；
- 多处散写 horizon 分支；
- critic、Advisor 或 repair Agent 写策略；
- 用 handoff 表示后台 UI 阶段；
- 用 Session 或 trace 代替授权；
- 用 guardrail Agent 决定 publication success；
- 把“找到候选”“完成候选审查”或 judgment-qualified 非零当成任务完成；
- 在 build-ready 为零时显示交付成功或 repair 成功绿勾；
- 把开放 repair proposal 误解为可执行任意代码、shell、URL 或未注册副作用；
- 接受没有可计算 `success_metric_spec` 的 proposal；
- 让模型自定义 metric 执行代码、伪造 pre/post，或用“感觉更好”“已修复”等文案代替 delta；
- 维护一张只有固定 6 个业务 action 的死表，并拒绝所有可组合的新 repair intent；
- 对未知 capability 静默忽略、静默成功或偷偷换成别的副作用；
- audit 建议 `rescore_projects`，但真实 blocker 是 manifest/evidence；
- 重复同一 action 却不比较 delta；
- Runner 返回即发 `repair_succeeded`；
- 将 legacy `repair_completed` 回放为绿色成功；
- 绕过唯一 `PublicationContractRegistry` 直接拼装或宣称 `BuildReadyPackage` delivered；
- 为让新测试通过而削弱 build-ready hard gate；
- 重命名旧 repair kind 导致旧 run 无法回放；
- 单元测试依赖实时 PRIDE；
- 覆盖当前 worktree 中用户已有的 `src/agent/web/app.py` 和测试改动。

建议锁定以上方案 2 架构、build-ready 唯一业务毕业标准与 Wave 1–6 顺序；六项架构决策均已有明确、兼容现状且可验证的答案，不需要额外人类选择。


---

## 4. Wave 顺序（锁定）

0. （可选）SDK 只读盘点 — 可与 Wave 1 并行文档，不改产品行为  
1. 离线红灯夹具（含 32/0「有进展未毕业」+ 非免疫共性场景 + 开放 proposal 失败/无 delta）  
2. 合约 spine：hard/soft/open、EvidenceStore、Publication/BuildReady、metric 白名单、issue→capability 表  
3. 开放 Repair + Authority Plane（H5/H7）  
4. 诚实事件与 UI（H6）— build-ready 前禁止成功绿勾  
5. 动态科学议程（H8）  
6. Replay / 属性测试 / 抽检  

每波：Codex 实现 → Grok `PASS/FAIL + MUST_FIX` → 仅 PASS 进入下一波。

---

## 5. 下一步

- 编排已完成锁定落盘。  
- **等待用户或编排指令：`开始 Wave 1` / `IMPLEMENT WAVE 1`。**  
- 在此之前：Codex 可待命，**禁止**改 `src/` 业务代码。

PLAN_STATUS: LOCKED
