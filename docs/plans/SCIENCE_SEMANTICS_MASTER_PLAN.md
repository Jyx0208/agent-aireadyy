# Science Semantics Master Plan（科学语义与可靠性总方案）

| 字段 | 值 |
|------|-----|
| 状态 | **BOSS DRAFT v1 — 待实现**（蜂群 R1/R2 综合；部署边界 **OUT**） |
| 聊天室 | `science-fix-plan` |
| 基线审阅 | `agent_aireadyy_extreme_review_2026-07-24.md` @ `main` `546b8cc` |
| 蜂群角色 | SP-S 主席 / SP-A Discovery / SP-B Constraints / SP-C AI-ready·RT·PSM / SP-D Reliability / SP-E Dialogue |
| 范围外 | 部署鉴权、Docker socket、0.0.0.0 暴露加固（老板指定延后） |
| 原则 | OpenAI Agents SDK 保留；**确定性控制面**拥有发布/完成真值；unknown ≠ pass；禁止静默丢硬约束 |

---

## 0. 一句话目标

把系统从「**审计很多、谓词偏松 → Agent 在错误事实上自信**」收成：

> **每个硬条件可追踪 → 每个候选可合取判定 → 每个 ready/completed 必须过不可绕过的门禁 → 下载与任务状态可崩溃恢复。**

L1「可用文件列表」可以诚实交付；**L2 build-ready / AI-ready completed / 可训练** 必须 fail-closed。

---

## 1. 问题总表 → 工作包映射

| 审阅/现象 | 根因摘要 | WP |
|-----------|----------|-----|
| 多条件只跑首 seed；portfolio 无审计 | `prepare_pride_search_queries` break first；`prepared[0]` | **WP-A** |
| 覆盖率=候选词并集；停搜假阳性 | `semantic_coverage` corpus OR | **WP-A** |
| 高相关无命中退回全体非排除 | `high_relevance_accessions` fallback | **WP-A** |
| `hard_constraint_evidence_gap` 名实不符 | needs_review 比率冒充硬缺口 | **WP-A** |
| 硬约束静默丢 | `normalize_scientific_constraints` except/continue | **WP-B** |
| 空值变 phospho / label_free | ontology 默认发明 | **WP-B** |
| weak_ready 算 task-ready | `task_ready_files` / counters / rank | **WP-B** + **WP-C** |
| inferred 升 hard / 默认当 resolved | provenance 缺失 | **WP-B** |
| exporter 零行仍 completed | rt/psm 等 status 恒 completed | **WP-C** |
| validation 只看 rows_out | 无 leakage/科学门禁 | **WP-C** |
| leakage 不算 release 硬门 | 仅卡片 warning | **WP-C** |
| RT 单位默认分钟；PSM 混指标/decoy | 科学合同缺失 | **WP-C** |
| 下载无 checksum/原子合同 | downloader 直接写终态 | **WP-D** |
| Web job 内存权威 | `_discovery_jobs` persist best-effort | **WP-D** |
| grant 跨事务不一致 | consume 与 usage/event 分离 | **WP-D** |
| 宽 fallback 伪装成功 | except 吞持久化失败等 | **WP-D** |
| 对话写卡 vs grill 张力 | compound 与 critical agenda | **WP-E**（不削弱门禁） |

---

## 2. 跨切面权威模型（全员 R2 共识）

```text
User/Agent speech
  → Strategy / DatasetRequest IR
  → ConstraintNormalizeResult (WP-B)     # 无静默丢
  → QueryPortfolio execution (WP-A)      # 多种子可审计
  → Candidate Evidence Matrix (WP-A)     # 候选×硬条件
  → Inspection / validity (existing + WP-B)
  → DownloadReceipt (WP-D)               # 物化完整性
  → Export science contracts (WP-C)
  → ReleasePredicate ladder (WP-C)       # 发布真值
  → Registry build_ready (existing, 不合并进 mega-predicate)
```

**状态词禁混用：**

| 词 | 含义 | 禁止 |
|----|------|------|
| `valid` | 文件级严格证据通过 | ≠ 项目 judgment 合格 |
| `weak_keep` / `weak_ready` | 弱可用 | **禁止**计入 `task_ready_files` / materialize / build-ready |
| `needs_review` | 待审 | 可进浏览配额，**不计** hard_pass 停搜分子 |
| `corpus_term_coverage` | 词并集探索度 | **禁止**单独触发 scientific stop |
| `completed`（export/validation） | 非空 + 科学门禁 + 产物 | **禁止**零行 completed |
| `checksum_unknown` | 未校验 | **禁止**当 release pass（discovery 预览可标 unknown） |

---

## 3. WP-A — Discovery：Query Portfolio + CEM + 科学停搜

### 3.1 根因（已代码对齐）

- `src/agent/discovery/query_builder.py`：`prepare_pride_search_queries` 每输入 **break 首 seed**
- `src/agent/discovery/search_environment.py`：再取 `prepared[0]`；`semantic_coverage` = 预览词 **并集**；`high_relevance_accessions` 无命中 → **全部非排除**
- `control_plane/discovery.py`：`hard_constraint_evidence_gap` ≈ needs_review/preview（误名）

### 3.2 设计

**QueryPortfolio（可审计）**

```text
QueryUnit {
  text, seeds_executed[], strategy,
  target_constraint_ids[], depth,
  status: executed|skipped_budget|skipped_dedupe|failed,
  yield_counts, not_executed_reason?
}
```

- 已批准 unit **必须执行或写 skip 原因**（禁止静默只跑第一个高产 seed）
- 与 grant bind 兼容：`_bind_candidate_search_to_grant` 仍强制 approved text；恢复用 atomic_seed 时 **仍受 grant 约束**（SP-D）

**Candidate Evidence Matrix（CEM）**

```text
M[c, h] ∈ {PASS, FAIL, UNKNOWN, CONFLICT, N/A}
hard_conjunction_pass(c) := ∀ hard h: M[c,h]==PASS
```

- 硬行集合：仅 **WP-B `may_be_hard`** 的字段 + hard scientific_constraints
- 停搜分子：`n_hard_pass_inspected` **仅 inspection 背书后的 valid/hard_pass**（needs_review 不计）
- 旧 `semantic_coverage` → 改名 **`corpus_term_coverage`（仅诊断）**

**科学停搜（示意）**

```text
scientific_stop iff
  n_hard_pass_inspected >= target
  AND unknown_hard_rate < ε
  AND marginal_hard_pass_gain low for M rounds
OR stop_with_limitations(explicit_gaps)
OR budget_exhausted  # 不得粉饰为 ready/completed
```

**禁止：** 仅靠 corpus 覆盖率停；高相关「全体非排除」fallback；用 needs_review 比率冒充 hard gap。

### 3.3 主要文件

- `discovery/query_builder.py`, `search_environment.py`
- `control_plane/discovery.py`（metrics / stop / audit 字段）
- `control_plane/openai_agents.py`（search budget 工具侧）
- 新：`discovery/query_portfolio.py`, `discovery/candidate_evidence_matrix.py`, `discovery/stop_policy.py`（可合并模块，但 API 清晰）

### 3.4 验收测试

- 反例：A=human, B=DDA, C=phospho → corpus 覆盖 100%，**hard_pass=0**，不得 scientific_stop-ready
- 多 seed：portfolio 记录 ≥N executed seeds 或 skip 原因
- 删除 high-rel all-nonexcluded fallback 的单测锁定
- 停搜不得仅由 `corpus_term_coverage` 触发

---

## 4. WP-B — 约束 IR + 状态格 + 禁止发明默认

### 4.1 根因

- `discovery/constraints.py`：`normalize_scientific_constraints` 非 list→`[]`；异常 continue（违反自身「不静默丢」文档）
- 策略 patch 较严，`_clean_dataset_request` 仍走静默 normalize（边界不对称）
- `ontology.py`：`None`→`phospho` / `label_free`
- `task_readiness.task_ready_files` 含 `weak_ready`；handoff / build_plan / control_plane rank 同步放大

### 4.2 设计

**ConstraintNormalizeResult**

```text
accepted: ScientificConstraint[]
rejected: {raw, error_code, message}[]
open_notes: str[]
```

规则：

1. 用户/模型写入硬约束数组：**原子失败**（任一条非法 → 整次约束写入失败并可见），与 strategy-patch 一致  
2. 禁止 `except: continue` 导致条数变少  
3. 原始条数 = accepted + rejected（可审计）

**Ontology unknown-first**

- `coerce_*`：只映射已知别名  
- 空/未知 → `unknown` / `unknown_ptm`，**永不**空变 phospho/label_free  
- 产品空卡默认只存在于 `createEmptyIntent` 文档化路径，不进 validity 用的 ontology

**Provenance**

```text
may_be_hard(p) := p ∈ {user, accepted_recommendation}
```

- missing provenance → 视为 inferred（软）  
- 禁止 inferred→user 重写、禁止自动 `include_only` 冒充用户决定

**Status lattice**

```text
validity: exclude | needs_review | weak_keep | valid
readiness: not_ready | weak_ready | ready
task_ready_files := only readiness==ready (or validity==valid 对齐产品口径)
pipeline_eligible := 可另计 weak（仅 L1/参数规划，永不 materialize）
```

硬证据缺失：`eval None` + hard → **不满足**（不 auto 软化）

### 4.3 文件

- `discovery/constraints.py`, `ontology.py`, `validity.py`（若触及）
- `discovery/task_readiness.py`, handoff/build_plan 路径
- `web/app.py` `_clean_dataset_request` / strategy patch 统一
- `control_plane/discovery.py` readiness rank ~4260

### 4.4 验收

- 非法 hard constraint → 错误可见，IR 中不消失  
- `normalize_ptm_type(None) != phospho`；labeling 同理  
- `task_ready_files` 不含 weak_ready  
- hard missing evidence 永不 `valid`/`weak_keep` 当通过

---

## 5. WP-C — AI-ready ReleasePredicate + RT/PSM 科学合同

### 5.1 根因

- exporters（RT/PSM 及 sibling）`rows_out==0` 仍 `completed`
- `validation.py` 以非空行推进 completed / training_preview
- leakage 有报告但非 horizon 硬门；`not_evaluated` 沉默
- RT 默认 minute；`require_confidence` 默认 False
- PSM：decoy 要求部分有，缺 fraction/FDR 硬门；quality_gate 偏声明式

### 5.2 设计

**新模块** `src/agent/ai_ready/release_predicates.py`

```text
evaluate_export_science(task_type, export_report, ...) -> Gate
evaluate_leakage_gate(report, horizon) -> Gate
evaluate_release(payload, horizon) -> ReleaseDecision{ok, status, blockers, warnings}
```

**阶梯（fail-closed）**

```text
export_ran
→ export_nonempty (rows + parquet 存在)
→ science_contract_ok (任务相关)
→ leakage evaluated AND pass   # not_evaluated ≠ pass；warn：ai_ready_table 最多 weak 预览；pre_release+ 要求 pass
→ artifacts_complete (+ DownloadReceipt integrity when paths present)
→ READY_AI_TABLE
→ PRE_RELEASE
→ FULL_RELEASE
```

- **Registry `build_ready` 保持独立权威**（`repair._business_completion_is_build_ready`），不合并成一个巨函数；仅共享「unknown≠pass」词汇  
- 零行：`export_empty`，永不 `completed`  
- **所有 sibling exporters**（denovo/fragment/chimeric/ptm…）同一 completed 语义（SP-C R2 自纠）

**RT 合同**

- 规范列：`rt_seconds` + `rt_unit_source` ∈ {column_explicit, user_supplied, inferred_default}  
- 无证据默认分钟 → `rt_unit_unknown`；**release 默认要求可追溯单位/置信**（opt-in `allow_unfiltered_rt` 默认 false）

**PSM 合同**

- 分字段：psm_q / peptide_q / protein_q / evalue… 禁止混同一语义槽  
- target/decoy **三态**：target | decoy | unknown；unknown ≠ target  
- release：`target_count>0` 且 `decoy_count>0`；`decoy_fraction` 默认带 [0.05,0.95]（profile 可配）；FDR/quality_gate **enforce**

### 5.3 文件

- 新 `ai_ready/release_predicates.py`
- `rt_exporter.py`, `psm_scoring_exporter.py`, 其它 exporters, `validation.py`, `dataset_recipe.py`
- `task_readiness.py`（与 B 对齐）
- 调用点：`web/app.py` recipe/horizon；control_plane 仅调用 evaluate_*，不吞结果

### 5.4 验收

- 零行 ≠ completed  
- leakage `not_evaluated` 阻断 pre_release+  
- weak_ready 不进任何 completed/build-ready 路径  
- RT 无单位证据默认失败 release  
- PSM 无 decoy 失败 release  
- Registry build_ready 行为回归不松

---

## 6. WP-D — 下载合同 + 耐久任务 + Grant 一致性

### 6.1 根因

- `assets/downloader.py` / 部分 adapter：终态直写、size-only reuse、无统一 checksum  
- PRIDE `.part` 有但不完整；iProX 原地写  
- `_discovery_jobs`：**内存权威**，persist 失败仍成功形态  
- `consume_search_grant` 与 usage/event **跨事务**；consume-first 可出现 execution_gap

### 6.2 设计

**DownloadReceipt**

```text
planned → downloading → size_checked → checksum_verified|checksum_unknown → published | failed
```

- 一律 `.part` + fsync + size(若知) + checksum(若知) + `os.replace`  
- reuse 必须重新校验，禁止「路径存在即成功」  
- `checksum_unknown` 可标 discovery 预览；**release/build-ready 默认阻断**（交 C 阶梯）

**DurableJob**

- **磁盘/SQLite 唯一权威**；内存仅缓存  
- persist 失败 → 不得转入 success  
- discovery 中断 → **interrupted/resumable**（对齐 expert 方向），非静默幽灵  
- 优先 SQLite 贴近 `AgentRunStore`；不发明第三套无 CAS 的 JSON 权威

**Grant（multi_agent/dynamic_budget 时）**

```text
BEGIN IMMEDIATE
  validate grant + query_hash
  reserve|consume + clear active + usage + event + search_attempt row
COMMIT
then network
```

- 推荐 **reserve+attempt**；若保留 consume-first，必须同事务 attempt ledger 以便 recovery  
- **禁止**可靠性 fallback 放宽科学资格（高相关全体退回等属 A，D 明确禁止当可靠性补丁）

### 6.3 文件

- 新 `assets/download_contract.py`（或等价）
- `downloader.py`, `pride/client.py`, `repositories/*_adapter.py`, `preparer.py`
- `web/app.py` discovery jobs；`expert_review/jobs.py`
- `control_plane/store.py`, `budget_governor.py`

### 6.4 验收

- 损坏 `.part` / checksum mismatch 永不 publish  
- 杀进程后 job 状态与磁盘一致；无「内存 success、盘无」  
- grant 崩溃恢复：无「已消费零 search」无账  
- UI「下载完成」依赖 receipt.published

---

## 7. WP-E — 对话层（SDK）：灵活写卡 + 科学 grill，不拆门禁

### 7.1 定位

- 保留 OpenAI Agents SDK：`update_strategy` / `confirm_strategy` / compound / soft-reject / next_decision 合成  
- **不**为 UX 放宽 CEM / Release / 约束 IR

### 7.2 设计要点

| 谓词 | 含义 |
|------|------|
| P_compound_hint | 多承诺句可补全白名单字段 |
| P_low_risk_skip | 仅白名单字段可跳过二次 SV |
| P_grill_continue | critical agenda 未决必须继续问（物种等） |
| P_ready_for_confirm | 卡片+critical 满足才 awaiting_confirm |
| P_confirm_eligible | 仅 awaiting_confirm 可 confirm_strategy |
| P_search_start | confirm 后才 PRIDE 搜 |

- 复合写卡失败时：软字段可保留；**硬约束数组仍走 WP-B 原子失败**  
- 向用户展示 portfolio/CEM **摘要**（可选），避免只报 corpus 覆盖率  
- multi-agent 默认：feature-flag 级议题；本方案不强制改默认，但要求 **有 grant 则 D 一致性**

### 7.3 文件

- `web/app.py` grill-turn / strategy  
- `discovery/agenda.py`, `task_profiles.py`  
- 测试：`test_discovery_agent_turn.py`, grill repair tests

### 7.4 验收

- 复合句多字段写入不削弱 hard IR  
- 未决 species 等仍 grill  
- confirm 前不搜  
- 无「为灵活而跳过 leakage/CEM」

---

## 8. 实现顺序（依赖，非排期）

```text
WP-B1  约束 Result 规范化 + ontology unknown-first     ← 最底层 IR
WP-B2  weak_ready ∉ task_ready + provenance
WP-A1  QueryPortfolio 多种子执行与审计
WP-A2  去掉高相关全体 fallback；intent 结构化
WP-A3  CEM + 指标改名 + scientific stop
WP-C1  release_predicates + 全 exporter 零行/completed
WP-C2  leakage 门 + RT/PSM 合同
WP-D1  DownloadReceipt 合同
WP-D5  Grant 多行事务 + attempt ledger
WP-D3  Discovery job 权威倒置（SQLite）
WP-E   对话对齐（B/A 稳定后收口）
```

平行：C 与 A 在 B 的 weak_ready 拆分后可并行；D1 可与 A/B 早期并行。

---

## 9. 测试策略（最低集）

| 层 | 必测 |
|----|------|
| 约束 | 非法 hard 不消失；None≠phospho；weak∉task_ready |
| Discovery | 合取反例；portfolio seeds；fallback 删除；stop 不看 corpus-only |
| Release | 零行；leakage not_evaluated；RT unit；PSM decoy |
| Download | part/checksum/replace；reuse 重校验 |
| Jobs/Grants | persist fail；crash consume gap |
| Dialogue | compound + grill + confirm 门闩回归 |

禁止：长期红的 `future_project` 装绿；要么实现 WP-D3+ 要么降级标记，不制造假信心。

---

## 10. 明确非目标

- 部署鉴权 / Docker socket / 绑定地址（老板延后）  
- 推倒 OpenAI Agents SDK / 换成 pi coding-agent 作运行时  
- 把 Registry build_ready 与 AI-ready horizon 揉成单一不透明状态  
- 用「更聪明的 prompt」代替 CEM/Release 谓词  
- 为 L1 交付把 weak_keep 升格 materialize

---

## 11. 蜂群产物索引

| 角色 | 档案 |
|------|------|
| SP-A | `docs/plans/_spa_r1_discovery.md` |
| SP-B | `docs/plans/_spb_r1_constraints.md`, `_spb_r2_critique.md` |
| SP-C | `docs/plans/_spc_design_release_predicates.md`, `_spc_r1_release_predicates.md` |
| SP-D | `docs/plans/_sp_d_r1_chat.txt`, `_sp_d_r2_critique.txt` |
| SP-E | `docs/plans/_sp_e_r1.txt`, `_sp_e_r2.txt`（若存在） |
| 板子 | `docs/plans/SWARM_SCIENCE_FIX_PLAN.md` |
| 聊天室 | `paseo chat read science-fix-plan` |

---

## 12. Boss 审阅结论（对本文件 v1）

见下节聊天室同步的 **BOSS REVIEW**。通过条件：依赖顺序正确、L1/L2 不混淆、C 与 Registry 权威分离、D 不放宽科学资格、E 不拆门禁。  
若后续实现偏离谓词，整包 REJECT 回讨论。
