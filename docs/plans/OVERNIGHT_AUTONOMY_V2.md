# 通宵续跑 V2 — 内测通过后继续（用户要自动，少确认）

目标：在 INTERNAL_BETA PASS 之上继续增强，仍 **非 product GO**。

## 优先级
1. M3 安全 adapter 至少一个：`materialize_evidence` 或 `refresh_auth_context`（只调已有 service；fail-closed；测试）
2. materializer 与真实 run 路径更多 integration / 文档化 blockers
3. RUNBOOK 按实机修正（若发现错误）
4. Docker：daemon 可用则 build+health；否则诚实记 PARTIAL
5. 保持门禁 `run_m1_gate.ps1` 全绿；不碰 future_project 装绿

禁止同 V1。进度写 OVERNIGHT_PROGRESS.md 追加章节 `## V2`。
