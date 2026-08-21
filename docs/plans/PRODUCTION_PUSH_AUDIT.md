# Production Push 独立审计

审计角色：`@audit`  
日期：2026-07-23  
权威：`PRODUCTION_PUSH.md`、`M5_GO_CHECKLIST.md`、`M4_OPS.md`、`PRODUCTION_PUSH_REPORT.md`

## 裁决

本波在 `PRODUCTION_PUSH.md` 明定的“可编码 + 可本地 staging”范围内通过：normal ready discovery 与 repair cycle 均已接入 production Authority；缺配置、非法 mode、伪造 context、builder 异常和 stale receipt 均 fail-closed；统一 production gate 可重复运行。

该 PASS 不是 L3 全闭合或产品上线批准。真实 KMS/HSM、multi-worker durable volume、live repository →真实 builder receipt、干净 CI/部署验收和负责人签署仍未完成，所以正式产品继续 `NO-GO`。

## 独立复跑

在 Lead 终态代码上分别执行：

```powershell
.\scripts\run_production_gate.ps1
.\scripts\run_m5_staged.ps1
.\scripts\run_m1_gate.ps1
```

结果：

- Production gate：M1 `367 passed in 37.77s`；M4/M5 aggregate `110 passed in 4.26s`；production run/signer/staged focused `23 passed in 4.36s`；environment-backed staging smoke `4 passed in 2.92s`；退出码 0。
- 单独 M5：M1 `367 passed in 39.47s`；aggregate `110 passed in 4.18s`；退出码 0。
- 单独 M1：`367 passed in 37.79s`；退出码 0。
- 额外以 `--strict-markers --runxfail` 复跑 production run path、production authority、dev wiring 和 M5：`27 passed`。
- 新 production 测试无 `skip`、`skipif`、`xfail` 或 `pytest.skip`。

## 规格轴：P1–P5

### P1 — 真实路径与 fail-closed

- `load_production_authority_runtime(...)` 从环境加载 durable ledger、trusted public keys 和 HTTPS signer 配置；缺 ledger/verifier/signer、非法 timeout、非 HTTPS 或非法 mode 均阻断。
- `_audit_and_persist → _persist_discovery_audit_snapshot` 的正常 ready 路径会从 Authority ledger 签发 normal publication attempt，绑定 `run_id`、`audit_ref`、canonical package digest、recipient、attempt 和 nonce。
- caller 自造 publication context、跨 run 或跨 package 复用 context 均不能毕业。
- `run_authority_repair_cycle` 在 production 下使用同一 durable ledger 创建默认 `RepairAuthority`；ledger 缺失时 attempt 不启动并记录 incomplete，不能回退内存账本。
- production 配置缺失时 run record 保留机器可读 blocker，`business_completion.succeeded=false`、`success_ui_allowed=false`。

### P2 — 本地 staging

- `run_staging_authority_smoke.ps1` 在临时目录创建 ledger，生成一次性 Ed25519 raw key，只经当前进程环境传给测试 callback seam，并在 `finally` 恢复环境、清理临时目录。
- 产品 `src/` 不读取 `DISCOVERY_STAGING_TEST_ED25519_PRIVATE_KEY_B64`；该变量仅出现在 smoke 脚本、测试 helper 和说明文档。
- 未用明文 compose signer 冒充真实 KMS/HSM。

### P3 — Builder dry-run run record

- typed `builder_dry_run_result` 已持久化到 `AgentRunRecord` 并进入 summary。
- adapter 只在当前 publication 为 issued build-ready 时调用；异常返回 blocked typed result，并记录 `builder_adapter_failed`。
- 当前 decision blocked、package/config 改变、adapter 缺失或 receipt 未重验时，旧 accepted receipt 会清空。
- fail 后健康 adapter 必须重新验证当前 package，才能清除 transient blocker；不会出现 accepted receipt 与旧 failure blocker并存。
- 裸 HTTP 200 继续不能构成 builder acceptance。

### P4/P5 — Gate 与证据模板

- `run_production_gate.ps1` 串联 M1/M5、production focused 与 staging smoke，并正确传播退出码。
- `STAGING_PRODUCTION.md` 提供干净 venv/CI 复跑命令，不把本地 callback smoke写成真实云基础设施。
- `L3_EVIDENCE_TEMPLATE.md` 只要求 token/nonce 指纹或外部引用，覆盖 run、audit、package digest、key lifecycle、replay、builder receipt、基础设施和人工签署。
- `M5_GO_CHECKLIST.md` 只勾选本波实际证明的 run-path/local-staging/typed-receipt 项，未完成项保留未勾并标明 owner。

## 门禁/标准轴

- **无 dev 降级**：production 分支不进入 dev signer；已有 dev/legacy Authority 在 production 中被丢弃并由 Registry 拒绝；非法拼写 mode 返回 `invalid`，不会静默当作 `off` 后启用 dev。
- **无私钥入库**：全仓未发现 PEM/private-key block、`.pem`/`.key`/`.env` 新文件或固定 bearer secret。测试 key 运行时生成，不写 fixture、报告或日志。
- **无假成功**：Runner、HTTP 200、builder adapter 异常、progress-only、缺 signer 和伪造 context 均不能产生 issued build-ready success。
- **无 product GO 偷渡**：Lead 报告、staging 文档、证据模板和 checklist 均明确当前非 product GO；没有自动写 GO 的代码或脚本。
- **无案例特判**：新增判断按 mode、run/audit/package binding、ledger、typed receipt 与 adapter 状态工作，没有 accession 或科学案例分支。

## 尚未闭合的 L3 / NO-GO 条件

`M5_GO_CHECKLIST.md` 当前仍未勾选：

1. 干净 CI/Docker runner 留存 production gate 日志；
2. 浏览器/API/build stamp 的独立部署验收；
3. 真实 staging secret manager/KMS/HSM；
4. multi-worker 共享持久卷、权限、备份及 restore/replay 实机验证；
5. 真实只读 repository → materialize → production-equivalent sign →真实 builder receipt；
6. 科学、安全、运维和独立审计的正式签署。

在以上项目全部闭合并形成脱敏 L3 证据前，不允许把本报告的 PASS 解释为正式可用、merge-ready 或 production GO。

PRODUCTION_PUSH_AUDIT: PASS

PRODUCT_STATUS: NO-GO
