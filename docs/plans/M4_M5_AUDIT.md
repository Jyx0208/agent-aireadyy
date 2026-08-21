# M4 + M5 独立门禁审计

审计角色：`@audit`  
日期：2026-07-23  
权威：`MEETING_CONSENSUS_PLAN.md`、`M4_M5_BRIEF.md`、`M4_REPORT.md`、`M5_REPORT.md`

## 裁决

M4/M5 在本轮简报限定的“可编码机制 + staged offline evidence”范围内通过，可由 Lead 声明 `READY_FOR_GROK`。这不是 L3 live 验收：真实 KMS/HSM、共享 durable 基础设施、真实只读 repository、真实 builder receipt 和部署验收均未闭环，因此产品继续 `NO-GO`。

## 独立复跑

执行：

```powershell
.\scripts\run_m1_gate.ps1
.\.venv\Scripts\python.exe -m pytest -q -ra --strict-markers --runxfail `
  tests\test_discovery_production_authority.py `
  tests\test_discovery_m5_staged_e2e.py `
  tests\test_discovery_wiring_dev_publication.py
.\scripts\run_m5_staged.ps1
```

结果：

- 稳定终态 M1 gate：`366 passed`。
- 新 M4/M5 + dev/production 隔离 focused：`19 passed`。
- 最终 staged script：Stage0 `366 passed in 44.14s`；M4/M5、peer/property、repair/evidence、materialize 聚合 `109 passed in 3.95s`；退出码 0。
- 新测试未使用 `skip`、`skipif`、`xfail` 或 `pytest.skip`；使用 `--runxfail` 复跑仍全绿。

早期写入中快照曾出现 `14 failed / 351 passed`，并暴露 production 接受 dev token、durable consume 未接线等红线。Lead 补齐后，上述问题均有启用中的负例并在稳定终态转绿；审计未以中间态结果代替最终复跑。

## M4 证据

通过项：

- Production signer 是外部 seam；callback/HTTPS client 不持有生产私钥，signer 返回值必须绑定请求的 `key_id` 和 payload digest。
- `DISCOVERY_AUTHORITY_MODE=production` 明确拒绝 dev/legacy Authority；即使 `DISCOVERY_AUTHORITY_DEV_SIGN=1` 也不降级。
- production issuance 绑定 canonical package digest、run、audit 和 key；新签发拒绝 retired/revoked key，历史验证允许 retired、拒绝 revoked。
- SQLite ledger 使用原子 reservation/consume；repair idempotency、metric observation pair、completion context/nonce 和 completion token 均已接 durable ledger，跨实例/重启 replay 被拒绝。
- 外部 signer 失败会释放未消费的 package reservation；健康 signer 可对同一 package 有界重试，不产生永久毒化 reservation。
- production completion 缺 recipient/attempt/nonce 时 fail-closed；同 context 重算幂等，消费后不能再次发出 success。
- `M4_OPS.md` 明确 secret manager 注入、权限/备份/锁语义、key rotation/revocation 和禁止 dev fallback。

范围边界：真实 control-plane 尚未部署 KMS/HSM 或生产 secret；本轮通过的是 `M4_M5_BRIEF.md` 明定的 production contract seam 与 durable mechanism，不是基础设施上线验收。

## M5 证据

通过项：

- Stage1 从 typed synthetic manifest/EvidenceStore/membership 调用 `materialize_build_ready_package`，其产物才进入 production-mode signer seam、durable completion 和 builder dry-run；不是 caller 自报 package 即成功。
- 自动负向矩阵覆盖 32/0、缺 signer、bad membership、package substitution、completion replay 和 no-progress=2。
- `BuilderDryRunContract` 要求现场 publication 为 issued build-ready，并核对 canonical digest、production key id、builder entrypoint、preflight ref、typed accepted status 和 receipt ref；裸 HTTP 200 明确失败。
- `M5_GO_CHECKLIST.md` 未把离线 synthetic 证据写成 live 证据，L2/L3 未完成项保持未勾选。

范围边界：没有 live repository、真实生产密钥、真实 builder adapter/receipt、multi-worker 持久卷实测或部署/browser 身份验收；这些仍是 L3 与 product GO 阻塞项。

## 红线检查

- 私钥/secret：仓库扫描未发现 PEM/private-key block、生产私钥、bearer token 固定值或新增 `.env`；测试 Ed25519 私钥仅运行时生成。
- 假成功：未发现 Runner、HTTP 200、候选数量、progress-only 或 legacy event 代替 issued build-ready。
- issuance：未发现 production 回退 dev、删减 key/digest/context 校验或放宽 replay。
- 案例特判：新增逻辑按 mode、digest、key lifecycle、ledger namespace 和 typed contract 工作，没有针对 accession/科学案例的业务分支。
- product GO：M4/M5 报告与 checklist 均明确否定自动 product GO，没有正式可用偷渡。

## 非阻断后续项

- 在真实 control-plane 注入 production signer/verifier/ledger，并完成 repository → materialize → sign → builder 的真实 staged receipt。
- 在目标多 worker/持久卷上验证 SQLite 锁、备份、恢复、磁盘故障和权限；部署真实认证、限流、告警与审计导出。
- 干净 CI/Docker、API/browser/build stamp 与安全/运维/科学负责人签署仍需单独验收。

M4_AUDIT: PASS

M5_AUDIT: PASS

PRODUCT_STATUS: NO-GO
