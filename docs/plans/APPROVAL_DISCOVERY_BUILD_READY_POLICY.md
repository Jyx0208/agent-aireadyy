# 审批稿：Discovery 证据策略 → 非零 build-ready 路径

**状态：待你审批（未开始改生产行为）**  
**委员会：** Codex `gpt-5.6-sol` high ×2  
- Planner：`1d1e065b-bce6-4d98-819a-f412e513ba60`  
- Auditor：`23914842-1232-4a0f-9a42-64dfd9e0d088`  
**完整原文：**  
- `docs/plans/_COMMITTEE_PLANNER_BUILD_READY.md`  
- `docs/plans/_COMMITTEE_AUDITOR_BUILD_READY.md`  

---

## 0. 一句话

**不是「搜不到」，而是「项目合格」和「文件可交付 / build-ready」是两套门；**  
当前 validity 把大量**项目级领域证据**打成 needs_review，usable≈0，再叠加物化只认 valid、搜索停机看错指标，于是 **build-ready 长期为 0**。  

委员会共识方案：  
**SDRF 最准 → 项目描述可认（软交付/严格晋升）→ 文件名仅辅助 → 真没证据就 exclude 并继续搜（防死循环）→ 物化只认 strict-valid 子集 → 不以「够 20 个合格项目」当终点。**

---

## 1. 双方一致的根因（合并）

| # | 根因 | 说明 |
|---|------|------|
| 1 | 两套门禁 | 项目「合格」= 审查判断；交付/build-ready = 文件 valid + 证据 + 物化。20 合格可以同时 0 交付。 |
| 2 | validity 过严且粘住 | `project_level_immunopeptide_evidence` 等进 hard_review → 整池 needs_review；与「项目描述也认」冲突。 |
| 3 | SDRF 未形成统一权威解析 | 已有匹配，但 acquisition/species/labeling 等未系统性「matched 即以 SDRF 为准」。 |
| 4 | 停机指标错 | 容易在「qualified 满 / 非 exclude 文件够」时停，而不是看 **strict-valid / build-ready 是否 >0**。 |
| 5 | delivery vs materialize 分裂 | 控制面允许 weak_keep 算 delivery；publication **只接受 valid**。正常链路还缺 evidence store / membership / builder preflight 播种。 |

**不是**：「这 20 个项目科学上全废」。  
**是**：「规则把可认的项目级证据卡成不可交付，又没继续为 build-ready 换查询」。

---

## 2. 目标证据优先级（对齐你的政策）

```
1) SDRF 匹配到该文件     → 最准，覆盖字段以 SDRF 为准
2) 项目描述 / 项目元数据 → 可认（领域、单一方法时可传播，见拍板项）
3) 文件名                 → 辅助（是否可单独证明：见拍板项）
4) 冲突 / 硬缺口          → needs_review（标出不确定）
5) 领域+方法证据皆无      → exclude，去搜别的（不占 review 队列）
（本阶段不做：论文全文、下 raw 头）
```

### 状态机（共识）

| 状态 | 含义 | 能否进「候选交付计数」 | 能否进 **build-ready 包** |
|------|------|------------------------|---------------------------|
| **valid (strict)** | SDRF 或文件级硬证据足够，且无冲突 | 是 | **唯一默认** |
| **weak_keep** | 项目级/部分元数据可认，方法缺口软 | 可计进度 / 可选 soft delivery | **默认否**（审计否决「直接进 materialize」） |
| **needs_review** | 冲突、缺下载、assay 冲突、真不确定 | 否（可触发补证/换搜） | 否 |
| **exclude** | 无证据 / 硬冲突 / 错 assay | 否 | 否（应驱动 **search_more**） |

**假绿红线（双方否决）：**

- 不把 weak_keep 直接签成 BuildReadyPackage  
- 不整体清空 needs_review  
- 不 immuno / 单 PXD 硬编码  
- 不伪造 EvidenceStore / membership / preflight  
- UI 未签发 package 不得显示交付成功  

---

## 3. 控制流（discovery 必须以 build-ready 为终点）

```
搜候选 → 拉文件 + 尽量解析 SDRF
  → 按优先级写回字段 + validity
  → 项目判断（合格 ≠ 毕业）
  → 若 strict_valid_files == 0 且预算允许:
        exclude 无证据者 → 换查询假设 search_more
        （同策略无进展 ≤2 轮；最多约 3 种不同策略 — 待拍板）
  → 仅对 strict-valid 子集 materialize
  → audit ready + evidence + membership + builder preflight
  → Authority 签发 BuildReadyPackage
  → 只有这时 UI 成功
```

**防死循环：** no-progress 签名应看 **strict-valid / materializable 增量**，不是「又多了几个 qualified 项目」。硬预算（轮次/query/repo/时间）仍是最终刹车。

---

## 4. 分波实现（合并双方 Wave）

### Wave A — 证据与 validity 合约（核心，先做）

1. 统一 **SDRF 优先** 字段解析与回填（matched 行权威）。  
2. 调整 validity：  
   - 项目级领域证据 → **弱交付路径（weak_keep）**，不再一律 hard needs_review；  
   - **无领域且无仪器/碎裂** → **exclude**；  
   - 冲突 SDRF / 错 assay / 无 URL·size → needs_review 或 exclude（按表）。  
3. 通用 domain-evidence 规则（禁止 immuno 特例补丁）。  
4. 确定性测试：SDRF matched → valid；仅项目描述 → weak_keep；全无 → exclude。

### Wave B — 交付与物化闭环

1. **strict-valid 子集** 才进入 materialize 输入（不要整包 candidate 毒化）。  
2. 正常 run 路径播种：evidence store、membership inventory、builder entrypoint/preflight（现多只在测试里齐）。  
3. delivery 指标拆分：`qualified` / `weak_keep_usable` / `strict_valid` / `build_ready`。  
4. quality audit：0 strict-valid 时优先 **search_more**，而不是只报「有 20 合格」。

### Wave C — 搜索策略 + 诚实 UI

1. early-stop / no-progress 改盯 build-ready 相关增量。  
2. 策略轮换上限 + 预算封顶。  
3. UI：明确「合格项目 / 弱可用文件 / 严格 valid / build-ready」四行；0 build-ready 禁止绿勾。  
4. 可选：weak_keep 仅展示为「可人工确认后再晋升」，默认不自动进 Batch。

---

## 5. 验收（方案成功的定义）

- 含 **matched SDRF** 或一致项目级证据的 **确定性 fixture**：`strict_valid_files > 0`，且存在 **可 materialize** 路径。  
- 适当环境下能出现 **非空 BuildReadyPackage**（Authority 验证）。  
- 无证据候选被 **exclude + 补搜**，不堆成上千 needs_review。  
- 不因「qualified=20」在 build-ready=0 时假装完成。  
- 死循环不发生（no-progress / 硬预算可复现）。  
- 全部 Authority fail-closed 回归仍绿。  

**允许：** 数据客观上不够时最终仍可为 0，但必须证明 **已换查询假设** 并写出终止原因——不是「扫了 20 个相关项目就停」。

---

## 6. 需要你拍板的开放问题（合并精简）

请逐条回 **是/否** 或改阈值：

| # | 问题 | 委员会建议默认 |
|---|------|----------------|
| **Q1** | `weak_keep` **禁止**直接进 build-ready / materialize？ | **是（禁止）**；只作进度/可选 soft 池 |
| **Q2** | 项目级仪器/碎裂/acquisition：在 **单一 assay、无混合、无文件反证** 时是否允许广播到该项目文件？ | **是（有条件广播）** |
| **Q3** | 文件名：是否 **不能单独** 满足关键科学维度（只作佐证）？ | **是（仅佐证）**；与「尽量判」结合=佐证+项目级可 weak_keep |
| **Q4** | 防死循环：同策略 **2 轮**无进展、最多 **3** 种不同查询假设，再靠硬预算停？ | **接受** |
| **Q5** | instrument + fragmentation 是否作为本次 DDA 类任务 build-ready **硬要求**（可由 task profile 声明）？ | **DDA 任务硬要求**；缺则 weak_keep/exclude 而非 valid |
| **Q6** | converted peaklist：是否允许进 strict-valid？ | **由 builder profile 决定**，不在全局 validity 一刀切 |

---

## 7. 明确不做什么

- 论文全文、raw 头下载（本阶段）  
- 假绿 / 削弱 Authority  
- immunopeptidomics 或「80 项目」特例  
- 把请求意图当观测证据  
- 仅把 reason 码改名而不改搜索与物化闭环  

---

## 8. 请你审批的决议

若你同意，请回复例如：

```
批准：Q1是 Q2是 Q3是 Q4是 Q5是 Q6按builder
按 Wave A→B→C 实施
```

或改任何 Qx。  

**未收到批准前：不合并 validity/搜索行为到默认路径（本地试验除外）。**

---

## 9. 委员原文位置

- Planner 方案：`docs/plans/_COMMITTEE_PLANNER_BUILD_READY.md`  
- Auditor 否决与回归：`docs/plans/_COMMITTEE_AUDITOR_BUILD_READY.md`  
