# Notebook Dialogue Master Plan（笔记式对话 Agent）

| 字段 | 值 |
|------|-----|
| 状态 | **BOSS CONDITIONAL ACCEPT v1**（设计可开工；实现前见 §12 条件） |
| 房间 | `notebook-dialogue-plan` |
| 用户理想 | 闲聊记笔记 → 工具写卡 → 改口覆盖 → 缺了再问 → 失败后同一对话可续 |
| 反对 | 厚问卷 / 厚结构感；**不是**取消所有安全轨 |
| 蜂群 R1 | NB-A/B/C/D/E + Chair 材料（`_nb_b_r1_*`、`_nb_e_r1_critic.md`、`NOTEBOOK_DIALOGUE_R1_DESIGN.md` 等） |
| 运行时 | **保留 OpenAI Agents SDK**；Dialogue Manager 仍是唯一写卡者 |

---

## 0. 一句话目标

把主路径从 **「问卷式 grill 状态机」** 改成 **「笔记式科学助理」**：

```text
用户随便说
  → Agent 从整段上下文抽出承诺
  → 有承诺就自己 update_strategy（可多字段）
  → 用户改口就覆盖
  → 只问用户没覆盖且会影响搜索的真缺口
  → 用户确认后才搜 PRIDE
  → 搜成/搜败后同一对话继续：看结果 / 改卡 / 再搜
```

**结构只保留薄协议**（工具写卡、确认门、不假绿）。  
**结构应削弱**：默认菜单问卷、闲聊契约报错、短句被 SV 误杀、失败后像会话死亡。

---

## 1. 与用户理想的对齐（验收标准）

| # | 用户要的 | 计划交付 | 不得做成 |
|---|----------|----------|----------|
| 1 | 闲聊不报契约错 | 非写卡回合 fail-soft；无 SV 红条 | 闲聊也「无可验证修改」 |
| 2 | 从上下文提取写卡 | prompt 笔记优先 + `update_strategy` | 只靠用户点菜单才写卡 |
| 3 | 改口覆盖 | latest-turn wins（含硬字段，在有工具 patch 时） | soft-reject 静默丢掉硬字段 |
| 4 | 缺了再问 | 缺口可选：一句自然语言优先；菜单可选 | 固定 Q1–Q10 |
| 5 | 任务清晰自己写卡 | Manager 主动 tool | 纯 prose 改卡 |
| 6 | 失败可续聊 | 失败恢复 chips + phase 再进 grilling | 只能 Restart |
| 7 | 薄门闩 | 确认才搜；不假绿；硬要求不静默丢 | 自动开搜；weak=build-ready |
| 8 | 少硬规则、Agent 主导 | 规则变薄、prompt/tools 变厚 | 再堆校验器当「灵活」 |

---

## 2. 目标状态机（产品）

```text
idle
  └─ 用户任意消息 → chatting（含闲聊）

chatting / noting   （同一后端 turn，行为不同）
  ├─ 无科学承诺 → action=chat|advise，不写卡，不 SV
  ├─ 有承诺 → action=update_strategy + tool，写卡
  └─ 仍有真缺口 → 可一句追问 或 可选菜单（非必须）

awaiting_confirm
  └─ 仅此时 confirm_strategy / 确认按钮 可绑定指纹

running
  └─ 发现 job 进行中；对话可只读进度，不双开 job

done | failed_recoverable
  ├─ 默认：用户再说 → 回到 chatting（保留卡与历史）※已有 FE 基础
  ├─ 芯片：查看本轮结果(L1) | 改策略 | 按当前卡重新搜索 | 重置对话
  └─ Restart：唯一全量清空
```

**禁止：** `failed` = 对话死亡；确认与开搜混成一步无指纹。

---

## 3. 权威模型（保留双权威 — 批评家 A1/A2 不可破）

```text
Mutation authority（写卡）
  = 仅 OpenAI Agents SDK 真实工具 update_strategy
  ≠ 助手散文、≠ extra_fields、≠ 校验员补丁

Confirm authority（开搜）
  = phase=awaiting_confirm + strategy fingerprint + grill_confirmed
  ≠ 「可以说搜了」在 grilling 里自动跑 PRIDE

Option authority（回 1/2/3）
  = 选项创建时存下的 strategy_patch（不可被本轮模型加宽）
```

这与「用 prompt 解决灵活性」**不矛盾**：prompt 决定**何时**调工具、**问不问**；  
**不能**用散文直接改卡（否则假写卡会回来）。

---

## 4. Prompt 原则（笔记式 — NB-A 方向）

### 4.1 人格

- 蛋白质组学搭档 / 记笔记，**不是**表单向导。  
- 默认：从**整段对话 + 最新一句**提取承诺；有则写卡，无则纯聊。  
- **最新用户话优先**覆盖旧字段（改口即更新）。  
- 只问**仍会影响检索/筛选且用户未表态**的缺口；优先**一句自然语言**，菜单可选。  
- 禁止：无缺口还出 2–8 选项；问候变问卷。

### 4.2 删除/改写的厚规则（相对现状）

| 削弱 | 改为 |
|------|------|
| 写卡后 **MUST** 再出完整 next_decision 菜单 | 有 critical 缺口时：**自然语言追问优先**；菜单仅当有助于选择时 |
| 巨量 clause_audit 作为用户可见契约 | 服务端可保留审计；**用户侧**不展示合同刑具 |
| 「每轮最高价值一题」压过复合句 | **复合承诺默认一次 update_strategy** |

### 4.3 系统仍注入的薄上下文

- 当前卡快照  
- 决策记忆（已选过的不要重复问）  
- critical agenda **作 readiness 列表**，不作题号  
- confirmation_context（能否确认）  

---

## 5. 写卡与校验政策（NB-B + 批评家）

### 5.1 KEEP（不可谈判）

1. 只有 SDK `update_strategy` 写卡  
2. 选项 `1` → **仅**预存 `strategy_patch`；模型不得加宽（已修路径保持）  
3. `confirm_strategy` **绝不**启动 PRIDE  
4. Critic **只读**，不得写卡  
5. 无工具事件 → 无卡变更  

### 5.2 CHANGE — 语义校验触发（变薄，不变零）

**仅当**以下任一成立才跑 SV：

- patch 含 `scientific_constraints`  
- 任一键 **不在** 低风险白名单  
- 选项 patch **超出** 选项 scope  
- plain-text recovery / 兼容性恢复路径  
- 单字段且 tool vs interpretation **明显不一致**  
- 上一轮 critic 明确要求 recovery  

**不再仅因：**

- `len(clauses) > 1`  
- `len(patch) > 1` 且键全在低风险白名单  
- 纯 chat/advise 无 patch  

### 5.3 Soft-reject v2（NB-B R2 修订；防 E-A4/B1 静默丢硬字段）

校验 `rejected` 时（仍只从 **explicit tool patch** 出发，禁止采用 critic 补丁当写卡权威）：

1. **有字段级 critic 错误列表时**：保留「低风险白名单 ∩ tool 键 − critic 否决字段」中 schema 通过的键；硬科学键（species / acquisition / horizon 等）**仅在未被 critic 点名否决时**可保留。  
2. **仅有全局 reject、无字段列表时**：**不得**自动保留全部白名单硬键；回退到软集合 `{objective, special_themes, notes, task_type}`（与现 `_DISCOVERY_SOFT_REJECT_KEEP_FIELDS` 对齐，可配置但默认不扩到 species/DDA）。  
3. 丢掉：critic 否决字段、非白名单键、未过 schema 的 constraints。  
4. 响应必须带 `soft_reject_kept_fields` + `soft_reject_dropped_fields`；对用户文案：**已写入…；未写入…（原因）** — keep 非空时禁止说「策略完全未更新」。  
5. **禁止**把硬要求只塞进 notes 然后当已执行；constraints 失败必须明示无法保证。  
6. **实现门禁**：先写 soft-reject 夹具测试（含「全局 reject 不保留 species」「字段级否决只丢被否决键」）再改生产政策（Boss 条件）。

### 5.4 选项与自然语言并存

- 有 pending 菜单时：用户回 `1` → 预存 patch（SV skip）  
- 用户不用数字、直接说「label-free」→ **自由承诺路径** `update_strategy`，成功写重叠字段后可清除/覆盖 pending  
- 不强迫「必须先点菜单」  

---

## 6. 卡外意图（NB-C）— 第一等公民

枚举卡装不下的需求（细胞系、药物处理、文献 crooks、特殊纳入排除…）：

| 机制 | 用途 |
|------|------|
| `notes` / `objective` 加长 | 人类可读科学备忘；**必须**进入 discovery runner 提示 |
| `scientific_constraints[]` | 可审计、可硬/软 strength 的结构化约束 |
| （可选后续）`intent_notebook` blob | 对话提取的 bullet 列表，只增改不取代卡 |

**硬规则：**  
- 用户明确的硬科学要求 → 尽量进 **constraints 或 hard 字段**，禁止「只写 notes 假装执行」。  
- Discovery job payload / 检索规划 **必须消费** notes+constraints（R2 要求文件级证明，见 §12）。  
- 若无法执行某条硬约束 → **明示无法保证**，禁止静默丢。  

---

## 7. 失败/完成后的恢复 UX（NB-D）

### 7.1 已有基础（保留）

- `done` / `failed` 下用户再说话 → 可回到 `grilling`，保留 session / 卡 / memory（`CarbonAgentChat`）  
- 仅 **Restart conversation** 全量重置  

### 7.2 必须补的产品（否则用户感觉「只能重启」）

失败/完成气泡下固定芯片（绑定 **last job_id + 当前卡 generation**）：

| 芯片 | 行为 |
|------|------|
| 查看本轮结果 | 打开结果轨 / L1 下载 / 送入批量 |
| 先改策略再搜 | phase=grilling，不自动搜 |
| 按当前卡重新搜索 | 新 job + **新 agent session**（防旧 session 上下文爆）；需再次确认或明确「用当前指纹再确认」 |
| 重置对话 | 现有 Restart |

**禁止：** 恢复芯片触发双开 discovery；忽略 `run_horizon` 假开搜；无 job 绑定的「看结果」。

### 7.3 文案

- 失败：人话摘要 + 是否已有 L1 进度；**禁止**只贴 raw 1M context 报错全文（可「技术详情」折叠）。  

---

## 8. 实现工作包（有路径，非空话）

| WP | 内容 | 主文件 |
|----|------|--------|
| **NB-1 Prompt** | 笔记式 system/user 合同；削弱 MUST-menu；闲聊规则 | `app.py` grill prompts |
| **NB-2 Verifier thin** | SV 触发矩阵；soft-reject v2；多子句白名单 skip | `app.py` warrant + soft_reject |
| **NB-3 Option+NL** | 保持预存 patch；pending 下 NL 自由写卡 | `app.py` option resolve（已有加强） |
| **NB-4 Notebook fields** | notes/constraints 进 job + runner 提示；UI 展示备忘 | `app.py` job payload、`openai_agents` runner input、FE 策略轨 |
| **NB-5 Recovery chips** | failed/done 芯片 + job 绑定 + 新 session 再搜 | `CarbonAgentChat.tsx` |
| **NB-6 Copy/FE chrome** | 非写卡不露 SV 红条；写卡部分成功用「已写入」 | `CarbonAgentChat.tsx` / timeline |
| **NB-7 Tests** | 见 §9 | `test_discovery_agent_turn.py` + FE |

**推荐顺序：** NB-1 → NB-2 → NB-5（用户体感最大）→ NB-4 → NB-6 → NB-3 回归 → NB-7。

---

## 9. 测试 / 非退化

| 用例 | 期望 |
|------|------|
| 问候 / 问术语 | action chat/advise；无 strategy_patch；无 SV rejected 文案 |
| 复合句多字段 | 一次 update_strategy；白名单不强制 SV |
| 回 `1` | 仅选项 patch；无 SV |
| 改口覆盖 | 新 tool patch 覆盖旧 species/task 等 |
| soft-reject | keep 非空时有「已写入」；dropped 可见 |
| confirm | grilling 下不能开搜；fingerprint 错不能开搜 |
| ai_ready 终点 | 不静默降级假搜 |
| failed 后再发「改成 20 个」 | 进入 grilling 且可写卡 |
| 再搜 | 新 job，不与 running 双开 |
| L1 送批量 | 非退化 |
| context | 工具输出仍走 compact（已有） |

---

## 10. 明确非目标

- 取消确认门 / 自动 PRIDE  
- 散文直接改卡  
- 删除语义校验全体  
- 取消 OpenAI Agents SDK  
- 部署鉴权、检索 CEM 大改、批量状态栏重做  
- 「prompt-only」无文件路径的方案  

---

## 11. 批评家攻击 → 计划应答（摘要）

| 攻击 | 应答 |
|------|------|
| A1 选项 1 被灵活冲掉 | **KEEP** 预存 patch + 不可加宽 |
| A2 自动开搜 | **禁止**；仅 awaiting_confirm |
| A3 假绿 | horizon 矩阵 + 恢复芯片守门 |
| A4 硬字段被 soft 掉 | soft-reject v2：字段级否决才丢对应键；全局 reject 仅软集合；dropped 必见；constraints 不静默丢 |
| A5 无菜单变沉默 | readiness 服务端仍 block confirm；必须问缺口（自然语言即可） |
| A6 关光 SV | **拒绝**；仅收窄触发 |
| A7 上下文 | 瘦用户可见合同；保留 compact tool |
| A8 恢复=重置 | chips ≠ Restart |
| A9 自由意图 | NB-4 强制 discovery 消费 |
| A10 只写 prompt | **本 master 含 WP 与路径** |

---

## 12. Boss 审阅

### 判定：**有条件通过（CONDITIONAL ACCEPT）**

**通过点**

- 对准用户「闲聊笔记 Agent」而非加厚表单  
- 保留 SDK 写卡、选项契约、确认门、反假绿（批评家硬要求）  
- 写清削弱 SV/菜单、失败恢复、卡外意图  
- 可实施 WP + 测试 + 非目标  

**条件（实现前或 R2 补一行即可，不挡接受方向）**

1. **NB-C 消费链**：列出 `toDiscoveryJobPayload` / runner 今日是否已传 `notes`+`scientific_constraints`；若缺口，NB-4 第一项补文件级 diff。  
2. **soft-reject v2** 与「用户硬要求在 tool 里」的夹具测试必须先写再改政策，防静默丢硬字段（E-A4）。  
3. **再搜** 默认 **新 session**，避免旧 session 爆上下文。  
4. 实现时 **禁止** 借笔记本名义去掉 `grill_confirmed`。  

**若实现偏离上述 KEEP 或用户理想 1–6 → 整包打回讨论。**

---

## 13. 给用户的人话摘要

你要的：

> 随便聊 → 它记笔记写卡 → 你改它改 → 不会的就问 → 确认再搜 → 挂了还能接着说  

计划：

> **按这个改主路径**；工具还负责写卡（可查账）；**去掉表单味**（少强迫菜单、闲聊别报错、失败给按钮）；**确认才搜、不假绿**留下。  

不是再做更厚的结构，而是 **把结构削到只剩笔记本 + 几道门**。

---

## 14. 蜂群索引

| 角色 | 产出 |
|------|------|
| NB-B | `docs/plans/_nb_b_r1_write_card_verifier.md`, `_nb_b_r2_critique.md` |
| NB-E | `docs/plans/_nb_e_r1_critic.md`, `_nb_e_r2_critique.md` |
| 综合 R1 | `docs/plans/NOTEBOOK_DIALOGUE_R1_DESIGN.md`, `_DESIGN_R1_NOTEBOOK_DIALOGUE.md`, `_nb_r1_*.md` |
| 板子 | `docs/plans/SWARM_NOTEBOOK_DIALOGUE.md` |
| **本方案** | `docs/plans/NOTEBOOK_DIALOGUE_MASTER_PLAN.md`（§5.3 已经 NB-B R3 修订） |
| 聊天室 | `paseo chat read notebook-dialogue-plan` |
