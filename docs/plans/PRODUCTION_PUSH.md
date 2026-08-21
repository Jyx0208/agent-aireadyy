# 继续冲正式（L3 推进波）

用户：继续冲正式。  
权威：M5_GO_CHECKLIST 未勾选 L2/L3 + M4_OPS + INTERNAL_BETA。  
**禁止自动 product GO**；目标是尽量闭合可编码/可本地 staging 的阻塞。

## 本波必须做

### P1 — 真实运行路径注入 production Authority
- `openai_agents` / discovery control path 在 `DISCOVERY_AUTHORITY_MODE=production` 时：
  - 从环境加载 DurableAuthorityLedger + ProductionPublicationVerifier
  - signer 仅通过 HTTP/callback seam（不读本地 PEM 当生产私钥硬编码）
  - 缺配置 → fail-closed，不降级 dev
- 默认 mode 仍 off/dev 兼容内测

### P2 — Staging 本地 production-equivalent 配置包
- `docs/plans/STAGING_PRODUCTION.md`：如何起 local staging（测试用 key 仅 env 注入）
- `scripts/run_staging_authority_smoke.ps1`：mode=production + 临时 key + ledger 路径 + 跑 M5 stage1 子集
- docker-compose 可选 profile `authority-staging`（若可行）

### P3 — Builder dry-run 接入 run 终态（可选但优先）
- 当 package issued 后可调用 BuilderDryRunContract（mock adapter 可注入）
- 结果写入 run record 字段，不单靠 HTTP 200

### P4 — CI/干净环境 gate
- 扩展或新增 `scripts/run_production_gate.ps1` = m1 + m5 + production tests
- 文档：CI 如何无缓存复跑

### P5 — L3 证据模板
- `docs/plans/L3_EVIDENCE_TEMPLATE.md`：脱敏 receipt 字段清单（run_id, package_digest, key_id, builder receipt…）
- 更新 `M5_GO_CHECKLIST.md`：本波能勾的勾，不能勾的写清阻塞 owner

## 明确不做（除非环境已有）
- 真实云 KMS 采购/账号
- 对生产 PRIDE 写操作
- 自动把 PRODUCT 标 GO

## 报告
- `PRODUCTION_PUSH_REPORT.md`
- Audit：`PRODUCTION_PUSH_AUDIT.md`
