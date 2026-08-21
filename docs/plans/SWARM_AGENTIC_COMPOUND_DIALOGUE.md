# 蜂群：Agent 式一轮多字段写卡（少菜单）

## 用户目标
不要表单向导。参考 pi coding-agent 风格：用户一句话说清就 **一轮 update_strategy 写齐**。

示例：
`人源免疫肽，RT 预测，越多越好，DDA，审查候选，直接确认可搜`
→ 一次 patch：species/human、task_type=rt_prediction、quota open_ended、acquisition dda、run_horizon=candidates_reviewed、objective/themes；菜单尽量 null；可 ready_to_confirm。

## 技术约束
- 仍用 **OpenAI Agents SDK**（respond / update_strategy / confirm_strategy）
- 不拆 build-ready fail-closed
- 不 immuno 硬编码特例（规则要通用 compound）

## 已有改动（编排手已落一部分，请 A/B 核对补全）
- system prompt：MULTI-COMMITMENT FIRST、COMPOUND UPDATES ARE THE DEFAULT
- 禁止未声明 task 时瞎写 rt_prediction
- low-risk verifier skip 扩展为 **compound whitelist（最多 8 字段）**，含 special_themes / quota / species_policy 等
- soft_reject / partial_grounding 保留

## 工作树
`E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning`

## 聊天室
`agentic-compound-dialogue`
协议：`CLAIM` → 改 → `DONE` / `BLOCKED`

## 文件所有权

| 角色 | 只改 | 任务 |
|------|------|------|
| **S 监督** | chat + pytest + live curl | 验收；冲突仲裁 |
| **A Prompt/SDK 策略** | `src/agent/web/app.py` grill system + turn instructions + advisor tool text | 确保 compound 优先、少 next_decision 菜单、不发明 task |
| **B Verifier/gates** | app.py low-risk compound skip + soft paths only | 多字段白名单 skip 正确；constraints 仍强制 critic |
| **C 测试** | `tests/test_discovery_agent_turn.py` | 辅助函数 + mock：复合 patch 跳过 verifier；未说 RT 不写 task_type 的合同测试（若可 mock） |
| **D 部署实机** | restart 本机 + lab `172.16.13.5`；POST 复合句 | 记录 action/extra_fields/next_decision/elapsed |

## 成功标准
1. pytest low_risk/soft_reject 相关绿
2. live：`人源免疫肽，RT 预测，越多越好，DDA，审查候选` → `update_strategy` 且 **≥4 个字段** 写入；`next_decision` 尽量 null 或仅一个真缺口
3. live：仅「免疫肽数据」→ 不写 `task_type=rt_prediction`（除非用户明确）
4. lab + local health 200，代码含 MULTI-COMMITMENT / COMPOUND

## 参考
- OpenAI Agents SDK Manager tools
- pi coding-agent：tool-first、对明确意图直接 act（非问卷）
