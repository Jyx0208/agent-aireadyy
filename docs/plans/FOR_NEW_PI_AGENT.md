# 给「能调用 Codex 的新 pi Agent」的完整交接

Date: 2026-07-22  
Language: **对用户一律中文**；**Codex 交付的 A–E 计划也用中文**（编号 A–E 可保留，路径/API 名可中英对照）。  
**禁止**使用 Trellis skill / trellis 工作流助手（含 `.trellis` 相关工具）；只用普通 shell / 读文件 / git。

你是 **编排 Agent（orchestrator）**。用户把工作交给你，不是直接交给 Codex。  
上一任编排会话 **无法** `create_agent` / 稳定 bash（旧会话 shell 仍可能坏）。  
你的会话应已修好 shell（`shellPath: E:\Git\bin\bash.exe` → MINGW64）。  
**你必须自己调用 Codex** 做计划讨论与后续实现；不要让用户去桌面手动开 Codex，除非工具真的不可用。

---

## 0. 用户定下的铁律（违反即 FAIL）

1. **Codex = 工人**：`gpt-5.6-sol` + **high** reasoning。会执行、会糊弄 → **禁止自评 merge-ready**。
2. **Grok = 监工**（pi 里 `grok-4.5` 或用户指定的 Grok）：严格；计划与实现都要审；输出 `PASS/FAIL + MUST_FIX`。
3. **禁止**：Claude Code、Gemini agents。
4. **顺序强制**：
   - **先**和 Codex **讨论并敲定计划**
   - **再**实现（实现时每波 Grok 验收）
5. **产品目标**：做成 **可靠、灵活、能自主处理错误** 的 Discovery Agent，体感接近 **Codex**；尽量用 **OpenAI Agents SDK** 成熟能力。  
   **成功定义（用户 2026-07-22 确认）**：最终毕业 = **符合要求且能进入数据集构建（build-ready）**；仅候选/仅审查 = 中间进展，不算完成。
6. **禁止案例特判**：不能只修「免疫肽 32 候选 / 0 交付」；要修 **共性错误类 H1–H8**。
7. **fail-closed** 真硬约束必须保留；软偏好不得变 hard。

---

## 1. 仓库与路径

**唯一工作树（产品代码）：**

```text
E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning
```

Branch: `worktree-benchmark-review-planning`  
勿 reset/clean；勿 stage `.env` / `.agent_secrets/` / runs 密钥 / dialogue DB。

**权威背景：**

| 文件 | 内容 |
| --- | --- |
| `E:\TEMP\proteomics-discovery-agent-handoff-20260722.md` | 失败 run、根因、P0/P1 |
| `docs/plans/2026-07-22-autonomous-discovery-agent.md` | 架构草案 |
| `docs/plans/CODEX_PLANNING_BRIEF.md` | Codex 评审格式 A–E |
| `docs/plans/SESSION_HANDOFF_PLAN_DISCUSSION.md` | 讨论阶段规则 |
| `docs/plans/PASEO_SHELL_FIX.md` | shell 已修说明 |
| `docs/adr/0001-*.md` / `0002-*.md` | 对话权威：单 writer |
| `docs/discovery-agent-guidance.md` | 科学对话策略 |

**Paseo 偏好（已写）：**  
`C:\Users\28425\.paseo\orchestration-preferences.json` → 全部 `codex/gpt-5.6-sol`  
**Pi shell（已写）：**  
`C:\Users\28425\.pi\agent\settings.json` → `shellPath: E:\\Git\\bin\\bash.exe`

---

## 2. 你的工作流（严格按序）

### Phase 1 — 计划讨论（**已完成锁定** → 见 `docs/plans/LOCKED_PLAN.md`）

用户已回复「锁定」（方案 2 + build-ready 成功定义）。Phase 1 文档阶段结束。  
**下一动作**：仅当用户/编排说 **开始 Wave 1 / IMPLEMENT WAVE 1** 时进入 Phase 2 Wave 1；此前禁止改 `src/`。

### Phase 1 — 计划讨论（历史步骤原文）

1. 用 Paseo **`create_agent`** 拉起 **Codex / gpt-5.6-sol**，`thinking`/`reasoning` = **high**。  
   - `relationship: subagent`（归属你的任务）  
   - `workspace`: 上述 worktree（`existing` 或 `cwd` 指到该路径；**不要**乱开无关 worktree）  
   - `notifyOnFinish: true`  
   - **Analysis only**：禁止改 `src/` 业务代码，直到用户说计划锁定且进入实现。
2. Initial prompt 使用下面 **§4 Codex 提示词**（或让它读 `SESSION_HANDOFF` + `CODEX_PLANNING_BRIEF`）。
3. 等 Codex 交付 **A–E + PLAN_STATUS**。
4. **不要**自己当最终权威糊弄通过。拉起 **Grok 监工**（或同会话用 Grok）对 Codex 的计划做严格评审：
   - 是否案例特判？是否假修复？是否削弱 fail-closed？是否 SDK 误用？
   - 格式：`VERDICT: PASS|FAIL` + `MUST_FIX` + `EVIDENCE`
5. FAIL → `send_agent_prompt` 回 Codex 修改计划；再交 Grok。最多 2–3 轮；仍分叉则 **问用户**。
6. PASS → 向用户汇总「建议锁定的计划」要点，等用户说 **锁定 / lock**。  
7. 锁定后写入（仅在用户同意后）：  
   `docs/plans/LOCKED_PLAN.md`

### Phase 2 — 实现（仅计划锁定后）

1. Codex 按 wave 实现；每 wave 结束报告：改了什么、命令、输出、对应 H 类。  
2. Grok 验收；FAIL 打回 Codex。  
3. 用户/编排决定是否进入下一 wave。

**Wave 建议顺序（可被 Codex/Grok 修订，但精神不变）：**

0. SDK 盘点（只读）  
1. 离线红灯夹具（≥2 场景，含 H1–H6 共性，禁止只 immuno）  
2. 合约：horizon / 软硬 / evidence scope / 物化  
3. 修复状态机  
4. UI 诚实事件  
5. 任务议程包  
6. 硬化与抽检  

---

## 3. 产品问题共性（给 Codex/Grok 的共同语言）

| ID | 类 | 一句话 |
| --- | --- | --- |
| H1 | Horizon 尺子错 | 「查找并审查」却用 AI-ready 文件门 → 0 交付 |
| H2 | Soft→Hard | 「优先 DDA」被写成硬约束 |
| H3 | Evidence scope | 项目级事实要求每个文件重复 |
| H4 | 双质量定义 | judgment 合格 ≠ delivery；证据未物化 |
| H5 | 修复不收敛 | 念一遍 LLM + 「修复完成」但无 delta |
| H6 | 假 UI | 0 交付 + 绿勾自主修复完成 |
| H7 | 过期 grant/search id | 再检被拒无刷新 |
| H8 | 议程不足 | 嵌合任务未先问标签来源/重标容忍 |

真实失败样例（**夹具不是特判条件**）：  
job `discovery_job_20260722_133827_de8bff` → 32 候选 / ~20 判断 / **0 交付** / 2408 needs_review。

目标架构精神：

```text
Dialogue Manager (SDK, 唯一写策略)
  → 确认后
Discovery Orchestrator (确定性状态机)
  → worker agents-as-tools（搜/检/判）
  → horizon 发布合约（纯函数门）
  → repair 控制器：audit→允许动作→测delta→停并诚实
```

SDK：agents-as-tools、sessions、tracing、guardrails、handoffs（仅可见阶段切换）；  
**业务毕业/修复成功判定必须在确定性层。**

---

## 4. 给 Codex 的 initial prompt（你 create_agent 时用）

```text
You are Codex planner (gpt-5.6-sol, high). The orchestrator is a pi agent that called you; the user does not talk to you directly.

Mission: Discuss and lock an architecture plan for a Codex-class autonomous proteomics Discovery Agent. Do NOT implement product code in this turn.

Read fully:
1. docs/plans/FOR_NEW_PI_AGENT.md (§0–3 for user law + H classes)
2. docs/plans/SESSION_HANDOFF_PLAN_DISCUSSION.md
3. docs/plans/CODEX_PLANNING_BRIEF.md
4. docs/plans/2026-07-22-autonomous-discovery-agent.md
5. E:\TEMP\proteomics-discovery-agent-handoff-20260722.md
6. docs/adr/0001-llm-owns-discovery-dialogue.md
7. docs/adr/0002-discovery-dialogue-manager-and-option-contracts.md
8. docs/discovery-agent-guidance.md
9. OpenAI Agents SDK docs as needed (multi_agent, tools/as_tool, sessions, tracing, guardrails, handoffs)

Hard rules:
- Generic failure classes H1–H8, not immuno-only patches
- Prefer mature Agents SDK; deterministic plane owns publication + repair success
- Preserve one-writer dialogue (ADR 0002) and fail-closed hard constraints
- No Claude/Gemini. Analysis only until orchestrator says IMPLEMENT WAVE N

Deliver A–E as in CODEX_PLANNING_BRIEF, end with:
PLAN_STATUS: NEEDS_HUMAN_CHOICES | READY_TO_LOCK

Cwd/worktree:
E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning
```

---

## 5. 给 Grok 监工的提示（计划阶段）

```text
你是严格监工 Grok。Analysis only，不改产品代码。
Codex 已给出计划 A–E。你看不到用户与编排的闲聊，只根据文件与 Codex 输出判断。

对照：
- docs/plans/FOR_NEW_PI_AGENT.md 铁律与 H1–H8
- docs/plans/2026-07-22-autonomous-discovery-agent.md
- Codex 本轮全文

拒绝：案例特判、假自愈、削弱 fail-closed、无度量的 repair、第二策略 writer。

输出：
VERDICT: PASS | FAIL
MUST_FIX: (编号、可执行)
EVIDENCE: (依据)
NEXT_ORDER_FOR_CODEX: (一段话)
```

---

## 6. Provider 设置

优先：

```text
create_agent provider: codex/gpt-5.6-sol
settings: high reasoning / thinking（以 inspect_provider 可用项为准）
勿开 fast_mode 做规划
```

若 Paseo 列表里没有 `gpt-5.6-sol` 字符串，用桌面/list_models 里 **最接近的 GPT-5.6 sol** 变体，并向用户确认一次。

Grok 监工：pi 默认 `relay/grok-4.5` 或 `create_agent` 能指到的 Grok；若只能本会话切换 model，则计划审可用你自己切到 Grok 做一轮（用户偏好是 Grok 监工）。

### 6.1 Windows 多行 prompt 送达铁律（2026-07-22 事故修复）

**禁止** 把完整多行 mission 当作 `paseo run` 的位置参数 `<prompt>`（会被截成第一行，agent 假启动）。

**必须**：
1. 用 `docs/plans/_paseo_run_with_prompt_file.ps1`：单行 bootstrap `run` → `send --prompt-file` 全文 → **日志指纹校验**；或
2. 对已有 agent：`paseo send --no-wait --prompt-file <path> <agentId>`。

详情：`docs/plans/PASEO_PROMPT_DELIVERY.md`。
创建/发送后 **必须** `paseo logs` 看到 `Mission:` / `PLAN_STATUS` 等指纹，再向用户报「已启动」。

---

## 7. 环境自检（开始前 30 秒）

```bash
echo SHELL_OK && uname -a
# 期望 MINGW64 / Msys，不是 WSL error
```

若仍 WSL error：读 `docs/plans/PASEO_SHELL_FIX.md`；`shellPath` 必须是 `E:\Git\bin\bash.exe`。

**不要**未经用户同意重启 Paseo daemon。

---

## 8. 对用户汇报格式

- 已创建 Codex agent id / 状态  
- Codex 结论摘要（是否 READY_TO_LOCK）  
- Grok VERDICT  
- 需要用户拍板的问题列表（若有）  
- **未锁定前不宣称「可以开工写代码」**

### 8.1 编排主动性（用户 2026-07-22 明确）

- Codex **idle / 弄完** 后，编排必须 **立刻** 拉日志或读其落盘文件，继续：Grok 审 → 回 Codex → 或向用户汇报。  
- **禁止** 干等用户问「弄完没」。可用 `paseo wait` / 完成通知；不要无意义空转轮询刷屏。  
- 用户已选架构 **方案 2**：灵活智能层 + 薄权威平面 + 开放 repair 提议但强制 success_metric/delta。

---

## 9. 上一任会话的诚实限制

- 旧 pi 会话：无 `create_agent` 工具面 + bash 误走 WSL（已用 Git Bash 配置修复，**新会话**生效）。  
- 计划草案与 brief **已写在 docs/plans/**，但 **尚未与 Codex 真正讨论敲定**。  
- 用户明确：交接给你这个 **能调 Codex 的新 pi agent**，由你驱动 Codex，而不是用户直接对 Codex。

---

## 10. 你的第一行动（立即）

1. 自检 shell  
2. `list_providers` / `inspect_provider` 确认 codex/gpt-5.6-sol  
3. 用 **§6.1 可靠包装器** 拉 Codex（prompt 文件 = §4 全文），**禁止**多行位置参数  
4. `paseo logs` 指纹校验通过后再等结果  
5. Grok 审 → 汇报用户  

**现在就开始 Phase 1。不要先改业务代码。**
