# 多 Codex 协同编排

Date: 2026-07-22  
用户授权：可多开 Codex，分工互查，连续完成 Wave。

## 角色

| 角色 | Agent | 职责 | 文件所有权（禁止越界） |
| --- | --- | --- | --- |
| Lead | `bdfdb979-…` | Wave 3 收尾报告；之后 Wave 6 / 接线总成 | `control_plane/repair.py`, `capabilities.py`, 报告 |
| UI | 新建 W4 | Wave 4 诚实 UI | **仅** `frontend/benchmark-review/src/**` + 相关测试；可读 models 事件名 |
| Agenda | 新建 W5 | Wave 5 议程数据化 | `src/agent/discovery/task_profiles.py`, `agenda.py`, 相关测试, guidance 文档；**少动 app.py** |
| Auditor | 新建 AUD | 只读审查 + 补强测试建议/小测 | 优先只写 `docs/plans/*_REVIEW.md` 与 `tests/test_discovery_*_audit_extra.py`；不改 production 除非 Lead 授权 |

## 规则
1. 同一文件同时只一个 writer。
2. 每波仍：实现 → 报告 → Grok PASS → 下一波接线。
3. 成功 = build-ready only。
4. provider: codex/gpt-5.6-sol thinking high；mode full-access 写代码。
5. prompt 一律 `--prompt-file`。
6. 禁止 Trellis / Claude / Gemini。

## 互相对话

Paseo **没有**「自动把所有 agent 塞进一个 IM 并互相推送」的魔法开关；协作靠：

1. **权威邮箱**：`docs/plans/TEAM_BOARD.md`（各 agent 追加消息、互相 @）
2. **Paseo chat room**：`multi-codex`（`paseo chat post/read`）
3. **编排转发**：必要时 `paseo send` 把对方请求塞进指定 agent

各 agent 被明确要求：有接口问题直接在 TEAM_BOARD @对方，不要只等编排。

## 进度
- Wave 1–2: PASS
- Wave 3: 实现基本完成（repair 测试绿），报告收尾中
- Wave 4–6: 并行准备 / 顺序接线
