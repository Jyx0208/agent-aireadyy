# Production Push P1–P5 实现报告

## 结论

本波完成了 `PRODUCTION_PUSH.md` 中可编码、可本地 staging 的 P1–P5。production Authority 已进入正常 ready discovery 与 repair cycle 两条真实路径；统一 gate、staging smoke、typed builder receipt 与 L3 证据模板已落地。

这只把代码和本地 production-equivalent 机制推进到独立审计入口。真实 KMS/HSM、共享持久卷、多 worker、live repository 与真实 builder receipt 尚无 L3 证据，因此不得宣称 product GO。

## P1 — Production Authority 真实运行路径

### 配置加载

`src/agent/discovery/production_authority.py` 新增 `ProductionAuthorityRuntime` 与 `load_production_authority_runtime(...)`：

- ledger 从 `DISCOVERY_AUTHORITY_LEDGER_PATH` 加载；
- verifier 从 `DISCOVERY_AUTHORITY_TRUSTED_PUBLIC_KEYS` 加载；
- signer 可由受控 callback 注入，或从 HTTPS endpoint/key id/bearer token 环境配置构建；
- 缺配置、非法 timeout、非 HTTPS、非法 mode 均 fail-closed；`DISCOVERY_AUTHORITY_DEV_SIGN=1` 不能成为 production/拼写错误 mode 的 fallback。

### 正常 ready 路径

`_audit_and_persist → _persist_discovery_audit_snapshot` 在 production package 已签发且 audit ready 时，通过 durable ledger 签发 normal `publication-attempt`。该 context 绑定：

- `run_id`
- `audit_ref`
- canonical package digest
- Authority recipient / attempt / nonce

同 run/audit/package 可幂等恢复同一未消费 context；caller 自造 context、跨 run/package 借用均被拒。当前 schema 为兼容既有 completion contract，仍使用 `repair_authority_id/repair_attempt_id/repair_attempt_nonce` 字段承载 normal publication attempt，并以 `publication-authority:` / `publication-attempt:` 前缀区分。

### Repair 路径

`run_authority_repair_cycle` 的默认 `RepairAuthority` 在 production 下读取同一个 durable ledger，并使用稳定 `DISCOVERY_REPAIR_AUTHORITY_ID`（未配置时按 run 派生）。post-audit 显式禁止 normal publication auto-context，最终只使用 repair 自身 context，避免双 completion。

新增真路径测试证明：proposal admission → dispatch → metric capture/delta → ready re-audit → production completion → success events 使用同一 ledger。ledger 缺失时 attempt 不启动并记录 `repair_incomplete`。

## P2 — 本地 staging 配置包

新增：

- `scripts/run_staging_authority_smoke.ps1`
- `docs/plans/STAGING_PRODUCTION.md`

脚本在系统临时目录生成 ledger，并通过当前进程环境注入一次性 Ed25519 raw private key。测试 helper 在 smoke 模式下真实读取该 key 与 ledger；产品代码不会读取测试 key 环境变量。`finally` 恢复环境并删除临时目录。

Smoke 覆盖：materialize → production sign → normal publication attempt → builder dry-run、caller forged context、repair cycle 共用 ledger。未添加虚假的 compose signer profile，因为仓库没有能代表真实 HTTPS/KMS 信任边界的服务。

## P3 — Builder dry-run 与 run 终态

`AgentRunRecord` 新增 typed `builder_dry_run_result`，并进入 discovery summary 序列化。`_persist_discovery_audit_snapshot` 和 `run_authority_repair_cycle` 提供受控 `builder_adapter` 注入点；只有当前 publication decision 为 issued build-ready 时才调用 contract。

一致性规则：

- 当前 decision blocked、package/config 改变、adapter 缺失或 receipt 未重验时，旧 accepted receipt 必须清空；
- adapter 异常生成 blocked typed result，并记录 `builder_adapter_failed`；
- 后续健康 adapter 对当前 package 成功重验后，才同时清除 transient run/receipt blocker；
- 裸 HTTP 200 仍不能接受。

本波没有臆造主 Runner 的真实 builder HTTP endpoint；自动生产 adapter 仍需真实 builder 服务契约与部署配置。

## P4 — 统一 production gate

新增 `scripts/run_production_gate.ps1`，顺序执行：

1. `run_m5_staged.ps1`（内部先跑 M1）；
2. production run-path / signer / M5 tests；
3. staging Authority smoke。

最终命令：

```powershell
.\scripts\run_production_gate.ps1
```

最终输出：

```text
367 passed in 39.97s   # M1
110 passed in 4.01s    # M4/M5 aggregate
23 passed in 3.47s     # production run/signer/staged focused
4 passed in 3.02s      # environment-backed staging smoke
exit 0
```

另有 production/wiring focused：`39 passed in 6.22s`。

`STAGING_PRODUCTION.md` 给出干净 venv 安装与无缓存 CI 命令；真正干净 CI/Docker 日志仍需平台 owner 留存。

## P5 — L3 证据与 GO checklist

新增 `docs/plans/L3_EVIDENCE_TEMPLATE.md`，字段覆盖 run/audit/manifest/evidence/membership/package digest/key lifecycle/repair delta/replay/builder receipt/基础设施/人工签署，secret 只允许写指纹或引用。

`M5_GO_CHECKLIST.md` 已勾选本波实际证明的 production run injection、本地 staging、typed run receipt 和 unified gate；真实 KMS、multi-worker persistent volume、live repository→builder receipt 与负责人签署保持未勾，并标明 owner。

## 代码审查吸收项

- normal context 增加 run/audit/package digest 全绑定与跨 run 负例；
- 非法 Authority mode 不再静默当 `off`；
- staging smoke 使用脚本注入的 key/ledger，而不是测试自行替换；
- builder exception 形成机器可读 blocker；stale accepted receipt fail→recover 状态一致；
- `_persist_discovery_audit_snapshot` 职责较多是后续模块化债，本波未做高风险大拆。

## 仍然阻塞正式 GO

- 真实 secret manager/KMS/HSM 与受保护 HTTPS signer；
- 目标多 worker 共享存储的 SQLite 锁、权限、备份和 restore/replay 实测；
- 真实只读 repository → materialize → sign → builder receipt；
- CI/Docker/browser/build stamp 部署证据；
- 科学、安全、运维与独立审计签署。

PRODUCTION_PUSH_STATUS: READY_FOR_AUDIT

PRODUCT_STATUS: NO-GO
