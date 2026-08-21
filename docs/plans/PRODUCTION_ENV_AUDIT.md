# Production Environment 可编码条件独立审计

审计角色：`@audit`  
日期：2026-07-23  
权威：`PRODUCTION_ENV_RUNBOOK.md`、`PRODUCTION_ENV_CODE.md`、`PRODUCTION_ENV_REPORT.md`

## 裁决

本轮在“可编码条件 + 本地实验室验证”范围内通过：lab-only HTTPS signer、同机多进程 SQLite 争用、production gate CI workflow、脱敏 L3 evidence collector、签署模板与诚实 checklist 均已落地并由启用中的测试覆盖。

该 PASS 不表示真实生产环境已验收。lab signer 不是 KMS/HSM；同机多进程不是跨主机共享持久卷；workflow 文件存在不等于远端 CI 已通过；evidence draft 与空白签署表不等于 L3 已签署。因此产品继续 `NO-GO`。

## 独立复跑

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -ra --strict-markers --runxfail `
  tests\test_discovery_ledger_multi_worker.py `
  tests\test_lab_https_signer.py `
  tests\test_l3_evidence_collection.py

.\scripts\run_production_gate.ps1
```

终态结果：

- 新增 production-environment 测试：`4 passed in 4.95s`。
- Production gate：M1 `367 passed in 49.74s`；M4/M5 aggregate `110 passed in 4.60s`；production + local-L3 focused `27 passed in 8.82s`；staging smoke `4 passed in 2.81s`；退出码 0。
- 新增测试没有 `skip`、`skipif`、`xfail` 或 `pytest.skip`。

## 规格轴

### Lab-only HTTPS signer

- `scripts/lab_https_signer/server.py` 只允许 loopback，要求运行时 TLS certificate/TLS key、Ed25519 signing key 与 bearer token；代码、README、health/sign response 均带 lab-only 标识。
- 请求必须通过 Bearer、`key_id`、payload digest 校验；响应 signature 由真实 `HttpProductionPublicationSigner` 客户端接收并由 trusted public key 验证。
- 负例覆盖 bad bearer、wrong key id 和 payload digest mismatch，均由 HTTP/client failure 阻断。
- 服务关闭默认请求日志，不打印 authorization、payload 或 signing key。
- 源文件与 README 明确写有 `LAB-ONLY / NOT production KMS/HSM`，checklist 的真实 KMS 项保持未勾；没有把 lab signer 标成 KMS、HSM 或正式 signer。

### 同机 multi-process ledger

- Windows `spawn` 启动 6 个独立进程，同时打开同一个 SQLite 路径。
- 同一 idempotency reservation 恰好一个进程成功；同一 metric pre/post pair 恰好一个进程原子消费成功；新实例重启后 replay 仍失败。
- 报告与 checklist 明确限定为“同机、多进程、同一路径”，没有据此勾选 K8s/VM 跨主机共享卷、备份或 restore/replay 实机项。

### CI production gate

- `.github/workflows/production-gate.yml` 使用 Windows + Python 3.13、干净 `.venv`、`PIP_NO_CACHE_DIR=1`，执行统一 `run_production_gate.ps1`。
- `actions/checkout` 与 `actions/setup-python` 固定到 commit SHA；workflow 权限仅 `contents: read`。
- workflow 无 secret/private-key 配置，并在 `always()` 步骤明确输出 code/local-lab 非 KMS、非 shared-volume、非 live-repository、非 product GO。
- 尚无真实远端 workflow URL/日志，因此 `M5_GO_CHECKLIST.md` 的干净 CI/Docker 项仍未勾。

### L3 evidence collector / signoff

- `collect_l3_evidence.ps1` 使用字段白名单；prompt、file/project IDs 和原始 token/nonce/idempotency key 不输出。
- audit/manifest/EvidenceStore/preflight/receipt/package-id refs 只输出 SHA-256 指纹；普通标量仅在安全字符集内保留，否则转为指纹。
- counts 只接受非负整数，`succeeded/accepted` 只接受真实 boolean；恶意字符串或对象收敛为 `null`，不会原样泄露。
- 测试在 prompt、URL path/query、ref、count、boolean 与 key slots 植入 `SECRET`，生成 evidence 全文不得含 `SECRET` 或文件 ID。
- 输出固定为 `DRAFT_NOT_SIGNED_OFF`、`product_go=NOT_APPROVED`，经同目录临时文件后替换目标。
- `L3_SIGNOFF.md` 的科学、安全、运维、独立审计与 GO 决议均保持 `PENDING/NOT_APPROVED`。

## 门禁/安全轴

- **无私钥泄露**：全仓扫描未发现 PEM/private-key block、已加入的 `.pem`/`.key`/`.env` 文件或生产 bearer secret。测试的 TLS/signing private keys只生成在 pytest 临时目录，lab server 通过运行时路径读取。
- **无 KMS 冒充**：所有 lab signer 文案均显式否定 production KMS/HSM；真实 KMS checklist 未勾。
- **无假 multi-worker 证据**：只声明本机多进程 SQLite 锁语义，未声称跨主机 PVC/NFS 已验证。
- **无 product GO 偷渡**：Lead 报告为 `PRODUCT_STATUS: NO-GO`；signoff 为 `NOT_APPROVED`；远端 CI、真实基础设施和人工签署仍是阻塞。
- **无案例特判/门禁削弱**：新增逻辑和测试只处理 TLS signer contract、ledger 原子争用与 evidence 脱敏，没有 accession/科学案例分支，也未修改 Authority 成功门。

## 仍未闭合的真实生产条件

1. 组织管理的 KMS/HSM/secret manager 与正式 TLS certificate；
2. 至少两个实际 worker 共享 durable volume 的锁、权限、备份和 restore/replay 证据；
3. 真实只读 repository → materialize → production sign →真实 builder receipt；
4. 远端 CI/镜像日志和 browser/API/build stamp 验收；
5. 科学、安全、运维、独立审计及最终人工 GO 签署。

以上任一项缺失时，lab/CI/local test 全绿都不能改变产品状态。

PRODUCTION_ENV_AUDIT: PASS

PRODUCT_STATUS: NO-GO
