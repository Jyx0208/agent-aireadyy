# 蜂群：OpenAI Agents SDK 对话层 — 更软 + 更快

## 用户目标
在 **继续使用 OpenAI Agents SDK** 的前提下，解决对话层：
1. **过硬**：短句（如「免疫肽数据」）被 semantic-verification 整包 reject → 策略不更新
2. **过慢**：grill-turn 常 30～60s+（主 Manager + 可选 advisor + 最多 2 次 verifier）

## 非目标
- 不换成 pi coding-agent 作产品运行时
- 不拆 fail-closed build-ready / materialize
- 不 immuno 项目硬编码特例

## 工作树
`E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning`

## 聊天室
`dialogue-soft-fast-swarm`  
协议：`CLAIM` → 改 → `DONE` / `BLOCKED`

## 文件所有权（禁止抢改）

| 角色 | 只改 | 任务 |
|------|------|------|
| **S 监督** | chat + 跑测 + 复现 curl | 验收；冲突仲裁；不写大功能 |
| **A 校验门闩** | `src/agent/web/app.py` 中 `patch_verification_warranted` / reject 回落 | 单字段/低风险 patch **跳过二次 verifier**；verifier reject 时 **软字段子集保留**（objective/special_themes 等），禁止无脑 `patch={}` |
| **B SDK 循环速度** | `app.py` `_complete_discovery_dialogue_json` 一带：max_turns、advisor 提示 | Manager `max_turns` 能 2 就 2；收紧 advisor 使用条件文案；thinking 仍禁用 |
| **C 单测** | `tests/test_discovery_agent_turn.py` | 覆盖：短句 topic 单字段不整包拒；soft reject 保留 objective；低风险 skip verifier |
| **D 实机** | restart + POST grill-turn | 用例：`免疫肽数据`、整段策略粘贴；期望 update_strategy 或 partial；记 elapsed |

## 设计要点（给 A/B）

### 更快
- `len(patch)==1` 且字段 ∈ `{objective, task_type, species, acquisition_mode, coverage_mode, target_project_count, labeling_strategy, instrument_preference, run_horizon}` 且无 `scientific_constraints`、无 tool/interpretation 分歧 → **`patch_verification_warranted=False`**
- verifier `max_attempts` 保持 2，但低风险路径 0 次
- advisor：instructions 已写「别用于 greeting」；确认 tool description 足够硬

### 更软
- verifier `reject` 且存在 `explicit_tool_patch`：不要整清零；保留 soft keys：
  `objective`, `special_themes`, `notes`, `task_type`（若 patch 里有）
- 标记 `semantic_verification.soft_reject_kept_fields`
- assistant 文案：中文说明「已记录主题，其余待确认」

### 成功标准
1. pytest agent_turn 相关绿（或仅列明无关失败）
2. live `免疫肽数据` → 非「完全无策略修改」；最好 `update_strategy` 含 objective/special_themes
3. 单字段路径 elapsed 明显少于「必经 verifier」的路径（记录数字即可）

## 参考
- 现有 `partial_grounding` 分支（保留）
- OpenAI Agents SDK：`Runner.run` + tools `respond`/`update_strategy`/`confirm_strategy`
