# 蜂群：多字段策略粘贴 + 语义校验整包拒绝

## 用户痛点（已复现）
粘贴一整段：
`免疫肽/HLA 配体 · 人源 · 下游偏de novo · DDA下游任务de novo物种优先 human规模精选 · 约 20 个项目采集方式DDA`
→ semantic-verification **rejected**（missing_fields 等）→ **整包清零** → 文案「没有产生可验证的策略修改」。

## 目标
1. **部分落地优于全拒**：校验通过的字段写入策略；未对齐字段说明缺失，不整包 reject。
2. 用户消息更清楚：写出已应用字段 / 仍缺字段。
3. 相关单测绿；重启 web 验证可复现用例。
4. 保持：weak_keep 不进 build-ready；无 immuno 特例硬编码。

## 工作树
`E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning`

## 聊天室
`strategy-paste-swarm`  
协议：`CLAIM` → 改 → `DONE` / `BLOCKED`

## 文件所有权

| 角色 | 只改 | 任务 |
|------|------|------|
| **S 监督** | chat + 跑测 | 验收、冲突仲裁、不写大功能 |
| **A 后端 partial** | `src/agent/web/app.py` 语义校验分支 | incomplete → 子集 accept/repair；partial_grounding |
| **B 用户文案** | `app.py` assistant_message 路径 和/或 前端 grill 展示 | 部分成功时中文说明已改/未改 |
| **C 单测** | `tests/test_discovery_agent_turn.py` | 覆盖「多字段、verifier 缺 1–2 字段仍应用子集」 |
| **D 重启验证** | 脚本 + curl grill-turn | 用用户原文 POST，期望 update_strategy 或至少有 strategy 字段 |

## 已有线索（编排手）
- 复现：elapsed~58s，sv missing_fields 含 acquisition_mode, quota_flexibility, species 等，整包 reject。
- 编排手曾改 app.py partial 分支——**A 核对是否完整、是否有回归**。
- 旧测试 `test_semantic_verifier_partial_grounding_is_not_a_verified_subset` 可能与「子集可应用」冲突——**C 按新政策改期望**。
