# M4 + M5 执行简报（用户要求做完）

权威：MEETING_CONSENSUS_PLAN.md  
业务成功：仅 issued build-ready  
**禁止**因本波宣称 production GO / 正式无脑用；出口是「机制 + 离线/分阶段证据 READY_FOR_GROK」。

## M4 目标（可编码部分）
1. **Production signer seam**（非真上云 KMS，但是生产契约）  
   - 接口：`ProductionPublicationSigner` / client  
   - 配置：环境变量如 `DISCOVERY_AUTHORITY_MODE=production|dev|off`  
   - production：只接受外部密钥/签名服务结果；**禁止**静默降级 `DEV_SIGN`  
   - 支持 key_id；verifier 校验 key_id + digest  
2. **Durable Authority ledger**  
   - 持久化：idempotency keys、metric observation tokens、completion nonces、issuance records  
   - 进程重启后仍防 double-consume / replay  
   - 存储：worktree 本地 sqlite 或 run-store 旁 JSONL（选一，文档写清）  
3. 测试：无 production 配置 fail-closed；dev 与 production 隔离；ledger 重启后拒重放  
4. 运维文档：`docs/plans/M4_OPS.md`（密钥不入库）  
5. 报告：`M4_REPORT.md`

## M5 目标（可编码部分）
1. **Staged offline E2E**（无 live PRIDE 也可）：  
   - Stage0：门禁 `run_m1_gate`  
   - Stage1：synthetic materialize → production-mode sign（测试密钥）→ business_completion succeeded 仅在合法路径  
   - Stage2：负向矩阵自动化（32/0、无 signer、坏 membership、replay、no-progress）  
   - Stage3：builder dry-run contract（接受 package 校验，不把 HTTP 200 当成功）  
2. 脚本：`scripts/run_m5_staged.ps1`  
3. GO 评审清单：`docs/plans/M5_GO_CHECKLIST.md`（人勾，不自动 product GO）  
4. 报告：`M5_REPORT.md`  
5. 明确：L3 live 外网可标 optional/skip-if-no-network，不得假绿

## 角色
- @lead：M4+M5 主实现  
- @audit：独立复跑与是否 READY_FOR_GROK（非 product GO）  
- @ui：若 completion 展示需 key_id/mode，小改 frontend  
- @agenda：M5 若需对话阶段 fixture 协助，少动 app

## 完成声明允许
`M4_STATUS: READY_FOR_GROK` / `M5_STATUS: READY_FOR_GROK`  
禁止：`PRODUCT_GO` / 正式可用  
