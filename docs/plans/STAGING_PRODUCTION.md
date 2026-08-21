# 本地 production-equivalent Authority staging

本流程只验证 production mode 的配置、materialization、signer seam、durable ledger、repair completion 与 builder dry-run 合约。它使用测试进程内的临时 Ed25519 key，不是 KMS/HSM，不构成 product GO。

## 快速运行

```powershell
.\scripts\run_staging_authority_smoke.ps1
```

脚本会：

1. 在系统临时目录建立独立 Authority SQLite ledger；
2. 设置 `DISCOVERY_AUTHORITY_MODE=production` 和稳定的 staging repair authority id；
3. 生成一次性 Ed25519 raw private key，只通过当前 PowerShell 进程环境变量 `DISCOVERY_STAGING_TEST_ED25519_PRIVATE_KEY_B64` 传给测试 callback seam；
4. 运行 materialize → production sign → durable normal-publication attempt → builder dry-run，以及真实 `run_authority_repair_cycle` ledger 共用测试；caller 自造 publication context 的负例也必须阻塞；
5. 在 `finally` 中恢复原环境并删除临时 ledger/key 环境值。

测试 key 不写文件、不进入 fixture、日志或 Git。production 产品代码不会读取该测试环境变量；真实运行只允许 `HttpProductionPublicationSigner` 或显式注入的受控 callback seam。

## 真实 staging 环境变量

真实 staging 服务进程至少需要：

```text
DISCOVERY_AUTHORITY_MODE=production
DISCOVERY_AUTHORITY_LEDGER_PATH=<persistent-volume>/authority.sqlite
DISCOVERY_AUTHORITY_TRUSTED_PUBLIC_KEYS={"key-id":{"public_key":"BASE64","status":"active"}}
DISCOVERY_AUTHORITY_SIGNER_ENDPOINT=https://signer.staging.example/v1/sign
DISCOVERY_AUTHORITY_SIGNER_KEY_ID=key-id
DISCOVERY_AUTHORITY_SIGNER_BEARER_TOKEN=<secret-manager injection>
DISCOVERY_AUTHORITY_SIGNER_TIMEOUT_SECONDS=15
DISCOVERY_REPAIR_AUTHORITY_ID=repair-authority:staging
```

`DISCOVERY_AUTHORITY_SIGNER_BEARER_TOKEN` 必须由 staging secret manager 注入，不得写入 `.env`、compose 文件、测试 fixture 或报告。endpoint 必须为 HTTPS。缺任一 production 配置时 run path 记录 blocker 并 fail-closed，不会切到 dev signer。

## 干净环境/CI

推荐在无缓存 runner 中：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,web,agents-sdk]"
.\scripts\run_production_gate.ps1
```

CI 应使用临时工作目录和临时 ledger；真实 bearer token 只用于受保护 staging job。普通 PR gate 使用 callback smoke，不接真实外网 signer。

本波未添加 Docker Compose signer profile：仓库没有可安全代表真实 HTTPS/KMS 信任边界的 signer 服务，加入明文测试 signer 容器会制造错误的生产等价感。
