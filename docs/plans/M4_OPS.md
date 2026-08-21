# M4 Production Authority 运维说明

本说明覆盖 production signer seam、可信公钥生命周期和 durable Authority ledger。它不是生产上线批准；唯一业务毕业仍是 issued `build_ready_succeeded`。

## 1. 生产配置

- `DISCOVERY_AUTHORITY_MODE=production`：启用生产信任域。此模式无条件拒绝 dev/legacy issuance，且不会读取 `DISCOVERY_AUTHORITY_DEV_SIGN` 作为降级路径。
- `DISCOVERY_AUTHORITY_LEDGER_PATH=<持久卷上的 sqlite 路径>`：必须位于单写一致、可备份、仅服务账号可读写的持久存储；容器临时层不合格。
- `DISCOVERY_AUTHORITY_TRUSTED_PUBLIC_KEYS=<JSON>`：公钥为 base64 Ed25519 raw bytes。兼容旧格式 `{"key-id":"base64"}`；推荐带生命周期：

  ```json
  {
    "key-2026-07": {"public_key": "BASE64", "status": "active"},
    "key-2026-04": {"public_key": "BASE64", "status": "retired"},
    "key-compromised": {"public_key": "BASE64", "status": "revoked"}
  }
  ```

- production signer 必须通过依赖注入提供 `ProductionPublicationSigner`。`HttpProductionPublicationSigner` 只接受 HTTPS，并要求调用方从 secret manager 注入 endpoint、`key_id`、bearer token；仓库不提供或保存生产私钥/令牌。

## 2. 密钥生命周期

- `active`：可签新 package，也可验证历史 issuance。
- `retired`：只可验证历史 issuance；禁止签发新 package。
- `revoked`：新旧验证全部失败。吊销会令依赖该 key 的业务成功 fail-closed，恢复必须走正式例外/重签流程，不能改成 dev。
- 轮换顺序：部署新 active 公钥 → signer 切新 `key_id` → 观察 issuance → 旧 key 改 retired。疑似泄露则直接 revoked，并保留审计记录。

## 3. Durable ledger

SQLite ledger 原子持久化：

- `publication_package` / `publication_issuance`
- `repair_idempotency`
- `metric_observation`
- `repair_completion_context`
- `business_completion`

所有 reserve/consume 使用事务；进程重启后仍拒绝等价副作用、metric pair 二次结算和 completion 二次消费。production completion 必须绑定 `run_id`、`audit_ref`、canonical package digest、`key_id`、repair authority recipient、attempt 和 nonce。

运维要求：

- ledger 文件及 `-wal`/`-shm` 只允许服务账号访问；备份时使用 SQLite 一致性快照，不直接复制活跃 WAL 的单个主文件。
- 多实例必须共享同一具备正确文件锁语义的持久卷；不支持 SQLite 锁的网络文件系统不得使用。
- 外部 signer 调用失败会释放未消费的 package reservation，允许相同材料重试；已写入 issuance 后不得人工删账来“重试”。
- signer、verifier、ledger 或 completion context 任一缺失都应得到 blocker；不得切换 dev 继续毕业。

## 4. 故障与恢复

1. 先保存 `run_id`、`audit_ref`、package digest、`key_id` 和 ledger record，不记录私钥或 bearer token。
2. signer 暂时不可用：保持 `blocked_with_progress`，按外部服务策略有界重试。
3. ledger 不可用/锁超时：停止签发与 repair side effect，禁止内存账本作为 production fallback。
4. completion 已消费：重放必须返回 incomplete；如需新修复，创建新 Authority-issued attempt/context，不复制旧 nonce。
5. 恢复后运行 `scripts/run_m5_staged.ps1`，再由独立审计决定是否进入下一评审阶段。
