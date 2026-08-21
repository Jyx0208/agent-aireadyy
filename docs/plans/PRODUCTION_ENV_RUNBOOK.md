# 达到生产环境条件 — 总指南（人 + 系统）

受众：你（产品负责人）+ 运维/安全 + 工程。  
代码侧生产契约已具备；**本指南把「还差的环境条件」拆成可执行步骤。**  
**任何自动化绿 ≠ product GO。** GO 仅当 L3 证据齐 + 负责人签署。

---

## 0. 你现在在哪

| 层 | 状态 |
|----|------|
| 内测 | PASS |
| M4/M5 机制 + 真路径注入 + 本地 staging | PASS（代码） |
| 真 KMS / 多 worker 实机 / live 回执 / 负责人签 | **未完成 → 产品 NO-GO** |

检查进度：`docs/plans/M5_GO_CHECKLIST.md`

---

## 1. 正式可用的最低条件（缺一不可）

1. **密钥**：生产私钥只在 KMS/HSM/secret manager，应用只拿 **HTTPS 签名 API + 公钥验签**
2. **账本**：多实例共享、可备份恢复的 durable ledger（当前实现 SQLite 文件；生产需共享卷或后续换 DB）
3. **配置**：`DISCOVERY_AUTHORITY_MODE=production` + 完整 env（见下）
4. **链路证据**：真实只读仓库 → materialize → 生产签名 → builder 回执（脱敏）
5. **门禁**：干净 CI/镜像复跑 `run_production_gate.ps1`
6. **签署**：安全 + 运维 + 科学 三方在 `L3_SIGNOFF.md` 签字

---

## 2. 环境变量清单（生产 / 准生产）

在 secret manager 注入（**禁止提交仓库**）：

```text
DISCOVERY_AUTHORITY_MODE=production
DISCOVERY_AUTHORITY_LEDGER_PATH=/var/lib/discovery/authority.sqlite
DISCOVERY_AUTHORITY_TRUSTED_PUBLIC_KEYS={"prod-key-1":{"public_key":"<base64-spki-or-raw-per-code>","status":"active"}}
DISCOVERY_AUTHORITY_SIGNER_ENDPOINT=https://signer.your-org.example/v1/sign
DISCOVERY_AUTHORITY_SIGNER_KEY_ID=prod-key-1
DISCOVERY_AUTHORITY_SIGNER_BEARER_TOKEN=<from-secret-manager>
DISCOVERY_AUTHORITY_SIGNER_TIMEOUT_SECONDS=15
DISCOVERY_REPAIR_AUTHORITY_ID=repair-authority:prod
```

可选：轮换时旧 key 设 `status: retired`（只验历史）；泄露设 `revoked`。

自检：

```powershell
# 缺任一项时 production 路径应 blocker，不能 dev 毕业
.\scripts\run_staging_authority_smoke.ps1   # 仅机制
.\scripts\run_production_gate.ps1           # 总门禁
```

---

## 3. 分阶段落地路线图（建议顺序）

### Phase A — 本机/实验室（你现在就能做）

| 步骤 | 动作 | 完成标志 |
|------|------|----------|
| A1 | 按 `INTERNAL_BETA_RUNBOOK.md` 起服务，内测路径 OK | health 200，无假绿 |
| A2 | 跑 `run_production_gate.ps1` | 全绿 |
| A3 | 跑 `run_staging_authority_smoke.ps1` | 全绿 |
| A4 | 读 `M4_OPS.md`、本文件，指定 3 个负责人 | 名字写在 L3_SIGNOFF |

### Phase B — 准生产 staging（需要运维账号）

| 步骤 | 动作 | 完成标志 |
|------|------|----------|
| B1 | 部署 **HTTPS 签名服务**（或云 KMS 封装 HTTP） | 内网 curl 可签，返回 key_id+sig |
| B2 | Secret manager 注入 bearer + 公钥 JSON | 进程 env 无明文进 Git |
| B3 | 持久卷挂载 `DISCOVERY_AUTHORITY_LEDGER_PATH` | 重启后 ledger 文件仍在 |
| B4 | 起 **≥2 worker** 同一 ledger 路径 | 并发 smoke / 重放仍 fail-closed |
| B5 | 备份+restore 演练 | 恢复后旧 token 不能二次消费 |
| B6 | 填 `L3_EVIDENCE_TEMPLATE.md` 一节「staging」 | 脱敏附件 |

### Phase C — 科学真实数据（只读）

| 步骤 | 动作 | 完成标志 |
|------|------|----------|
| C1 | 最小权限只读 PRIDE/仓库账号 | 无写权限 |
| C2 | 选 1 个可构建目标任务（预算封顶） | run_id |
| C3 | production mode 跑完 → materialize → sign → builder dry-run/真 builder | receipt |
| C4 | 负向：关 signer / 断网 / 坏 membership 各一次 | 全是 blocked 非假绿 |
| C5 | 证据包归档 | 模板字段齐 |

### Phase D — GO 评审

| 步骤 | 动作 |
|------|------|
| D1 | 干净 CI 日志 + Docker 镜像 digest 留存 |
| D2 | 浏览器 build stamp 与部署一致 |
| D3 | 三方签署 `L3_SIGNOFF.md` |
| D4 | **仅此时** 可改对外状态为正式可用（人工决策） |

---

## 4. 签名服务对接要点（给后端/安全）

应用侧已有：`HttpProductionPublicationSigner`  
请求侧应满足（以代码为准，对接时对照 `production_authority.py`）：

- HTTPS only  
- 请求绑定 **key_id + payload digest**  
- 响应签名可被 `ProductionPublicationVerifier` 用 **trusted public key** 验过  
- 失败：应用 **fail-closed**，不降级 `DEV_SIGN`

**不要做：** 把私钥放进 Agent 容器环境变量当「生产」。

---

## 5. 账本运维要点

- 路径固定在 **共享持久卷**
- 权限：仅 app 用户可写  
- 备份：热/冷拷贝 sqlite 前按 `M4_OPS.md`  
- 恢复后跑：重放旧 completion 必须失败（证明 exactly-once）  
- 多 worker：同一 `LEDGER_PATH`；先用实验室并发脚本验证  

---

## 6. 你（负责人）每周检查清单

- [ ] `run_production_gate` 在 CI 仍绿  
- [ ] staging 签名 endpoint 健康  
- [ ] ledger 备份成功  
- [ ] 无 dev 模式误开在生产集群  
- [ ] 新 key 轮换演练（季度）  

---

## 7. 工程仍可自动推进的（本波继续做）

见 `PRODUCTION_ENV_CODE.md`：多 worker ledger 压测、L3 证据采集脚本、CI workflow、实验室 HTTPS signer（**标明 lab-only**）、签署表。

## 8. 工程做不到、必须你/公司做的

| 项 | 谁 |
|----|-----|
| 买/开 KMS、域名证书、内网 HTTPS | 平台/安全 |
| 共享盘 / K8s PVC | SRE |
| PRIDE 只读账号与预算 | 科学/你 |
| 三方签字放行 | 安全+运维+科学 |

---

## 9. 一句话

**代码已能「按生产规矩跑」；生产环境 = 密钥托管 + 共享账本 + 真数据回执 + 干净部署 + 人签字。**  
按 Phase A→D 推进；未完成前对外只说「内测/准生产」，不说「正式无脑用」。
