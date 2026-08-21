# M4 Production Authority 实现报告

## 结论

M4 已形成可独立审计的 production signer seam 与 durable Authority ledger。生产模式不接受 dev/legacy issuance，不因 `DISCOVERY_AUTHORITY_DEV_SIGN=1` 降级；repair 的 idempotency、metric pair 和 completion consume 已接入 SQLite ledger，跨实例/重启重放会被拒绝。

本结论仅表示实现和离线门禁可送 Grok 复核，不表示 product GO、真实 KMS 已部署或 merge-ready。

## 实现文件

- `src/agent/discovery/production_authority.py`
  - `ProductionPublicationSigner`、callback/HTTPS client seam。
  - Ed25519 verifier，校验 `key_id`、payload digest 与 signature。
  - `active` / `retired` / `revoked` key lifecycle；retired 只验历史，revoked 全拒绝。
  - SQLite `DurableAuthorityLedger`：原子 reserve、verify、consume-many、失败 reservation release。
- `src/agent/discovery/publication.py`
  - production package issuance 绑定 canonical package digest、run、audit、key。
  - production completion 强制 Authority-issued recipient/attempt/nonce；同 context 只产生同一确定性 token，消费后再评估 fail-closed。
  - production runtime 无条件拒绝 dev/legacy Authority state。
  - signer 调用/验签失败释放未消费 package reservation，允许有界重试。
- `src/agent/control_plane/repair.py`
  - durable idempotency reservation 在 dispatch 前完成。
  - Authority-captured metric observations 写 ledger，pre/post 原子双消费。
  - completion context 与 issued completion 原子双消费；重启后仍 exactly-once。
  - 未注入 durable ledger 时保留既有内存 v1 测试兼容，不作为 production fallback。
- `pyproject.toml`：显式加入 `cryptography>=45,<50`，避免依赖偶然的传递安装。
- `tests/test_discovery_production_authority.py`：生产签名、mode 隔离、重启 replay、signer retry、key lifecycle 负例。
- `tests/test_discovery_wiring_dev_publication.py`：dev issuance 切 production 后不得毕业。
- `docs/plans/M4_OPS.md`：配置、轮换、吊销、ledger 备份/权限与故障恢复。

## 验证

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_discovery_production_authority.py `
  tests/test_discovery_m5_staged_e2e.py `
  tests/test_discovery_wiring_dev_publication.py `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_repair_controller.py
```

结果：审查前 `62 passed in 3.35s`；代码审查补入 production 拒绝旧 HMAC completion 与 duplicate consume 原子性负例后，扩展 focused gate 为 `88 passed in 4.14s`。

最终统一脚本：

```powershell
.\scripts\run_m5_staged.ps1
```

最终从仓库外 cwd 复跑结果：M1 主体 `367 passed in 44.12s`；M4/M5 focused + peer/property/materialize `110 passed in 4.56s`。独立审计的前一稳定点为 `366 + 109` 且已 PASS；新增两条仅收紧 Authority，不替换或削弱审计用例。

## 已验证的失败语义

- production 配置不全、dev/legacy token、未知/吊销 key：block，不毕业。
- caller 修改 package、digest、membership、key id：验签或合约失败。
- signer 暂时失败：不留下毒化 reservation；相同 package 可重试。
- 同 idempotency key：新 Authority 实例仍拒绝第二次副作用。
- 同 metric pair：第一次结算后，任何实例不得再次发 `repair_progressed`。
- 同 completion context/token：第一次消费可发成功事件，之后只能 incomplete。

## 剩余生产风险

- 尚未连接真实 secret manager/KMS/HSM；测试 key 只在测试进程内动态生成。
- SQLite 多 worker 共享卷的锁、备份、磁盘故障和灾备尚未在目标基础设施实测。
- production signer 的真实认证、限流、告警与审计导出仍需部署侧完成。
- key revoke 会有意令历史 completion fail-closed；正式例外/重签流程需运维审批。

M4_STATUS: READY_FOR_GROK
