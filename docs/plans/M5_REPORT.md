# M5 staged offline E2E 报告

## 结论

M5 已交付可重复运行的 staged offline gate、自动化负向矩阵和 builder dry-run contract。正向 Stage1 不再直接签 fixture package，而是实际执行：

```text
deterministic manifest + EvidenceStore + membership + audit + builder preflight
  → materialize_build_ready_package
  → production-mode external signer seam
  → durable publication/completion issuance（绑定 recipient/attempt/nonce）
  → BuilderDryRunContract
```

这是一条 production-equivalent 的离线机制证据；它没有连接 live repository、真实生产密钥或真实 builder，因此不构成 product GO。

## 分阶段门禁

### Stage0 — 固定回归

`scripts/run_m1_gate.ps1` 运行 Authority、peer、wiring、agenda、agent-turn、task-build-plan、control-plane、materialization 与 web discovery。最终审查后复跑：`367 passed in 44.12s`。

### Stage1 — materialize → production sign

`tests/test_discovery_m5_staged_e2e.py` 从 typed synthetic manifest/evidence/membership 调用真实 materializer；只有 `ready_for_authority_signing=True` 且无 blocker 才进入 production signer。测试私钥仅在测试函数内动态生成，未写入 fixture、环境文件或仓库。

production completion 必须存在 durable ledger 签发的 repair recipient、attempt 和 nonce；缺 context 即 `blocked_with_progress`。对相同 snapshot 重算只返回同一 completion token，不增发 token；消费后重放不产生 success。

### Stage2 — 自动负向矩阵

覆盖：

- 32/0 或 progress-only：保留进展，builder handoff 拒绝。
- production 缺 signer，即使 dev 开关为真也拒绝。
- membership 缺失：签名前拒绝。
- package 内容替换/digest 不符：publication 先阻断。
- completion 重算幂等、跨实例二次消费失败。
- 相同 no-progress signature 连续两次：bounded stop，无假 success。
- peer/property suites 继续覆盖 hard unknown/conflict、scope promotion、caller self-certification 等攻击面。

### Stage3 — builder dry-run contract

新增 `src/agent/discovery/builder_contract.py`。接受条件同时包括：

- 当前 snapshot 现场通过 `PublicationContractRegistry` 且为 issued build-ready；
- canonical package digest、production `key_id`、builder entrypoint、preflight ref 全匹配；
- typed builder response 明确 `accepted=true`、ready/accepted status，并提供 receipt ref。

仅 `HTTP 200`、缺 typed receipt 或任一字段不匹配都返回 `builder_dry_run_blocked`。

## 文件

- `src/agent/discovery/builder_contract.py`
- `tests/test_discovery_m5_staged_e2e.py`
- `scripts/run_m5_staged.ps1`
- `docs/plans/M5_GO_CHECKLIST.md`
- 本报告

## 运行与结果

```powershell
.\scripts\run_m5_staged.ps1
```

输出摘要：

```text
367 passed in 44.12s
110 passed in 4.56s
```

没有 live PRIDE、网络、真实密钥、fake skip 或 Authority 放宽。

## 未完成项 / GO 阻塞

- 干净 CI/Docker 环境复建与 browser/build-stamp 部署验收。
- 真实 KMS/HSM、secret manager、multi-worker persistent ledger 实机验证。
- 真实只读 repository 和真实 builder adapter 的脱敏正向 receipt。
- 安全、运维、科学负责人按 `M5_GO_CHECKLIST.md` 人工签署。

上述项目未完成前，正式产品状态保持 NO-GO；`READY_FOR_GROK` 只请求独立复审当前实现与离线证据。

M5_STATUS: READY_FOR_GROK
