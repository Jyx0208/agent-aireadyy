---
document: WAVE2_ARTIFACTS
plan: docs/plans/LOCKED_PLAN.md
wave: 2
architecture: 方案 2（灵活智能层 + 薄 Authority Plane）
business_completion: build-ready only
status: READY_FOR_GROK
---

# Wave 2 Authority Plane 合约产物

本文固定 LP2 与 LP6，供 Wave 3 的开放 `RepairProposal` 校验使用。表格只描述 Authority Plane 可验证的观测、能力和风险，不规定 Agent 必须采用哪条科学探索路线。

## 1. Build-ready 与中间进度

- 候选数、审查数、judgment-qualified 数属于中间进度。
- `BusinessCompletionDecision.succeeded=true` 只允许由有效 build-ready package 产生。
- hard conflict、hard unknown、缺 build-ready 字段或 audit 未 ready 时，即使中间指标非零，也只能返回 `running_progress` / `blocked_with_progress`。
- `success_ui_allowed` 必须与 `succeeded` 相同，不能从 Runner 返回、HTTP 200 或模型文案推断。

## 2. LP2 — success metric 白名单

模型只能按 `metric_id` 请求度量；Authority Plane 从权威状态读取并计算，模型不能提供 metric 代码或自行填 pre/post。

| metric_id | 权威字段/来源 | 聚合 | 期望比较方向 | 说明 |
| --- | --- | --- | --- | --- |
| `unique_candidate_count` | candidate manifest 的唯一 project id | `count(distinct)` | increase | 搜索扩展的新增候选，不以重复行充数 |
| `reviewed_project_count` | inspection 成功记录 | `count(distinct project)` | increase | 只计 assessable inspection |
| `judgment_qualified_project_count` | inspection-backed judgments | `count(distinct project)` | increase | 中间质量指标，不等于毕业 |
| `verified_observation_count` | `EvidenceStore` v1 | `count(distinct observation_id)` | increase | source refs 已验证的 observation |
| `unresolved_claim_count` | audit unresolved claim ids | `count(distinct)` | decrease | 不能用改写说明文字降低 |
| `missing_build_ready_field_count` | publication contract 缺失字段 | `count(distinct field)` | decrease | file/assay membership 也作为字段检查 |
| `hard_conflict_count` | constraint audit | `count(distinct constraint)` | decrease to 0 | 非零时永不毕业 |
| `hard_unknown_count` | constraint audit | `count(distinct constraint)` | decrease to 0 | 非零时 fail-closed |
| `build_ready_project_count` | `PublicationContractRegistry` | `count(distinct project)` | increase / >0 | 唯一业务毕业核心指标之一 |
| `build_ready_file_count` | build-ready package | `count(distinct file)` | increase / >0 | 文件必须满足 build 入口材料 |
| `active_context_freshness` | search/grant authority state | boolean | false→true | 只证明上下文已刷新，不证明任务毕业 |
| `audit_ready` | 最新 authority audit | boolean | false→true | 仍须与 build-ready package 同时满足 |

### 2.1 计算规则

1. pre/post 必须来自相同 schema/version 与相同统计范围。
2. `expected_delta_direction` 只能是白名单为该 metric 声明的方向。
3. 不可解析 metric、字段缺失、范围不一致或模型自评 metric：`reject`。
4. 中间 metric 有正 delta 只允许发 `repair_progressed`；只有 build-ready completion 可发 `repair_succeeded`。
5. no-progress signature 至少包含：approved capability set、parameter hash、issue code set、metric id；连续 2 次相同 signature 零 delta 后停止。

## 3. LP6 — issue code → capability / metric / risk

capability 使用加法 registry；表中是可申请集合，不是固定执行顺序。Agent 可组合已注册 primitives，也可在被拒后改提议。

| issue code / family | 可申请 capability primitives | 首选 metric | risk ceiling | Authority 约束 |
| --- | --- | --- | --- | --- |
| `candidate_manifest_missing` | `search_expand`, `stop_with_limitations` | `unique_candidate_count` | expensive | 有预算才允许搜索；无结果不得成功 |
| `quality_target_not_reached`, `quality_target_shortfall_at_stop` | `search_expand`, `inspect`, `stop_with_limitations` | `judgment_qualified_project_count` | expensive | fixed target 仍 fail-closed |
| `portfolio_search_not_converged`, `portfolio_search_stopped_before_convergence` | `search_expand`, `stop_with_limitations` | `unique_candidate_count` | expensive | 达 ceiling 后只能停止/询问 |
| `high_relevance_inspection_coverage_incomplete`, `candidate_inspections_failed` | `inspect`, `refresh_auth_context` | `reviewed_project_count` | expensive | stale context 可刷新一次再重试 |
| `inspected_projects_missing_judgments` | `inspect`, `materialize_evidence` | `unresolved_claim_count` | bounded_write | 不能用自由文本 judgment 冒充证据 |
| `qualified_projects_have_unresolved_constraints` | `inspect`, `materialize_evidence`, `recompute_validity` | `hard_unknown_count` | bounded_write | hard unknown 必须降到 0 |
| `constraint_assessment_evidence_invalid` | `inspect`, `materialize_evidence` | `verified_observation_count` | bounded_write | 只接受可验证 source refs |
| `qualified_project_has_no_inspected_files`, `qualified_project_has_no_delivery_assets` | `inspect`, `materialize_evidence`, `recompute_validity` | `missing_build_ready_field_count` | bounded_write | project evidence 不可无 membership 下沉 |
| `qualified_project_still_needs_review`, `delivery_relies_on_weak_keep_files` | `inspect`, `materialize_evidence`, `recompute_validity` | `build_ready_file_count` | bounded_write | 中间 review 不能冒充毕业 |
| `hard_builtin_constraint_not_met`, `hard_per_project_min_files_not_met`, `hard_per_project_min_samples_not_met`, `hard_portfolio_constraint_not_met` | `inspect`, `materialize_evidence`, `ask_user_blocking_question`, `stop_with_limitations` | `hard_conflict_count` / `hard_unknown_count` | bounded_write | 禁止模型放宽 hard；只能补证据或请求用户改策略 |
| `preview_coverage_not_backed_by_selection` | `inspect`, `recompute_validity` | `unresolved_claim_count` | bounded_write | preview 数不能直接进入 package |
| `selected_manifest_contains_non_delivery_files`, `selected_manifest_contains_unqualified_projects` | `recompute_validity`, `select_manifest` | `build_ready_file_count` | bounded_write | 先重新计算；publication ready 后才可 select |
| `selected_manifest_missing` | `recompute_validity`, `stop_with_limitations` | `audit_ready` | bounded_write | 不得凭模型描述重建成功状态 |
| stale search/grant context | `refresh_auth_context`, 原 read capability | `active_context_freshness` | bounded_write | 最多一次刷新；刷新成功不等于业务成功 |
| `autonomous_repair_ceiling_exhausted`, `portfolio_search_stopped_at_hard_ceiling` | `ask_user_blocking_question`, `stop_with_limitations` | `audit_ready` | read_only | 不允许继续 expensive action |
| `quality_audit_policy_denied` | `ask_user_blocking_question`, `stop_with_limitations` | `audit_ready` | read_only | 拒绝原因必须公开、不可静默降级 |
| 未知 issue/capability | `ask_user_blocking_question`, `stop_with_limitations` | 无 | read_only | fail-closed；禁止静默执行/成功 |

## 4. Capability primitive 注册要求

每个 primitive 在 Wave 3 registry 中必须声明：

- 参数 schema；
- risk class 与预算计费；
- 幂等/重试规则；
- 可使用的 metric ids；
- 执行 adapter；
- 权威事件；
- 是否需要最新 search/grant context。

新增 primitive 必须加法注册并有测试；不得用科学主题字符串在 Authority Plane 中分支。

## 5. Evidence scope 与 materialization

- `EvidenceStore` observation 带 schema version、subject、dimension、scope 与 source refs。
- 未知 source ref 不得 materialize。
- project observation 不能自动成为 file evidence。
- assay→file、file→spectrum、sample→file 只在显式 membership ref 存在时解析。
- LLM judgment 是 claim；只有其引用的原始 evidence refs 通过校验后才能 materialize。

## 6. v1 兼容

旧 `DatasetRequest.hard_constraint_fields` 继续由现有入口和审计路径接受；新 publication binding 复用 `ScientificConstraint`，不会另建一套冲突模型，后续迁移只增加规范化投影。

WAVE2_ARTIFACTS_STATUS: READY_FOR_GROK
