# Production Environment 可编码条件报告

## 结论

已完成 `PRODUCTION_ENV_CODE.md` 要求的全部可编码交付：L3 脱敏证据采集、同机多进程 SQLite 争用、lab-only HTTPS signer 对接、GitHub production gate workflow、L3 三方签署模板及 checklist 更新。

这些结果证明代码和本地实验室协议具备进入真实环境验收的条件，不证明真实 KMS/HSM、跨主机共享卷、live repository→builder receipt 或人工签署。产品状态继续 NO-GO。

## 1. L3 证据采集

新增 `scripts/collect_l3_evidence.ps1`。

用法：

```powershell
.\scripts\collect_l3_evidence.ps1 `
  -RunJson <run-record-or-summary.json> `
  -OutputPath <l3-evidence-draft.json> `
  -EnvironmentName staging `
  -DeploymentId <deployment-id> `
  -BuildStamp <build-stamp>
```

输出 schema：`l3-evidence-draft/v1`，固定标记：

- `evidence_status=DRAFT_NOT_SIGNED_OFF`
- `product_go=NOT_APPROVED`

脱敏规则：

- prompt、文件 ID、project ID、原始 token、nonce、idempotency key 不输出；
- audit/manifest/EvidenceStore/preflight/receipt/package-id 引用只输出 SHA-256 指纹；
- run/deployment/build/entrypoint/authority/attempt 等标量只有匹配严格安全字符集才保留，否则输出指纹；
- counts 只接受非负整数，`succeeded/accepted` 只接受真实 JSON boolean；字符串、对象、负数等畸形值输出 `null`；
- 输出先写同目录临时文件，再原子替换目标，避免中断留下半份证据草稿。

回归夹具在 URL query/path、多个白名单标量以及 count/boolean typed slots 中植入 `SECRET` 字符串和对象，输出全文不得包含该字符串。

## 2. 同机多进程 durable ledger

新增 `tests/test_discovery_ledger_multi_worker.py`，Windows `spawn` 多进程同时打开同一个 SQLite 路径：

- 6 个进程争用同一 idempotency reservation：恰好 1 个成功；
- 6 个进程争用同一 pre/post metric pair：恰好 1 个原子消费成功；
- 新进程/新 ledger 实例重启后再次消费失败。

范围限定：只证明同机、多进程、同一文件系统路径的 SQLite 锁与 exactly-once。它不等同于 K8s/VM 跨主机共享 PVC/NFS 的锁、权限、备份与恢复验证；该 checklist 项仍未勾。

## 3. LAB-ONLY HTTPS signer

新增：

- `scripts/lab_https_signer/server.py`
- `scripts/lab_https_signer/README.md`
- `tests/test_lab_https_signer.py`

服务只允许 loopback，要求运行时提供自签 TLS cert、TLS key、Ed25519 signing key 和 `LAB_SIGNER_BEARER_TOKEN`。测试材料全部生成在 pytest 临时目录，仓库没有私钥、证书私钥或 bearer token。

真实 `HttpProductionPublicationSigner` 通过 HTTPS 连接该服务，并由 `ProductionPublicationVerifier` 验签。负例覆盖：

- bad bearer → HTTP 401 / client failure；
- wrong key id → conflict / client failure；
- payload digest mismatch → conflict / client failure。

源文件、README、health response 与 CI 说明均明确 `LAB-ONLY / NOT production KMS/HSM`。它不能用于生产部署或勾选真实 KMS 项。

## 4. CI / production gate

`scripts/run_production_gate.ps1` 已纳入 ledger multi-process、lab HTTPS signer 与 L3 collector tests。

新增 `.github/workflows/production-gate.yml`：

- `windows-latest` + Python 3.13；
- 干净 `.venv`、`PIP_NO_CACHE_DIR=1`；
- 安装 `.[dev,web,agents-sdk]`；
- 执行统一 production gate；
- checkout/setup-python 固定到 commit SHA；
- workflow 明确只验证 code/local-lab，不产出 product GO。

workflow 文件存在不等于远端 CI 已通过。首次 GitHub run URL 与日志仍须由 CI/平台 owner 填入 L3 evidence/signoff，因此“干净 CI 已验证”保持未勾。

## 5. 签署与 checklist

新增 `docs/plans/L3_SIGNOFF.md`：科学、安全、运维、独立审计和最终人工 GO 决议五部分；初始状态为 `NOT_APPROVED`，不得由 agent 自动修改为批准。

`M5_GO_CHECKLIST.md` 只新增以下已证实勾项：

- lab-only HTTPS 协议对接；
- 本机同路径 multi-process SQLite 争用；
- L3 collector 脱敏与 signoff 模板。

真实 KMS/HSM、跨 worker durable volume、live repository→builder receipt、远端 CI/部署与正式签署保持未勾，并保留 owner。

## 测试

定向命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_discovery_ledger_multi_worker.py `
  tests/test_lab_https_signer.py `
  tests/test_l3_evidence_collection.py
```

结果：`4 passed`。

最终统一 gate：

```powershell
.\scripts\run_production_gate.ps1
```

结果：

```text
367 passed   # M1
110 passed   # M4/M5 aggregate
27 passed    # production + local L3 focused
4 passed     # staging smoke
exit 0
```

## 仍需人/真实环境完成

- 组织管理的 KMS/HSM/secret manager 与正式 TLS certificate；
- ≥2 实际 worker 共享 durable volume 的并发、权限、备份、restore/replay 证据；
- 真实只读 repository → materialize → production sign → builder receipt；
- 远端 CI/镜像日志、browser/API/build stamp；
- 科学、安全、运维和独立审计签署。

PRODUCTION_ENV_STATUS: READY_FOR_AUDIT

PRODUCT_STATUS: NO-GO
