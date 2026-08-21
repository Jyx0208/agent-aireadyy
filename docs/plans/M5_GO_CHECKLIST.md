# M5 staged E2E / GO 评审清单

此清单必须由人和独立审计共同签署。自动化测试通过只允许进入评审，不自动产生 product GO。

## L1 离线合约

- [x] 固定 M1 gate 无 fake skip/xfail，Authority/peer/wiring/agenda/web 全绿。
- [x] production 模式拒绝 dev/legacy token，缺 signer 不降级。
- [x] package substitution、bad membership、32/0、hard unknown/conflict fail-closed。
- [x] idempotency、metric pair、completion 跨重启重放被拒绝。
- [x] no-progress 相同 signature 两次后停止，无 `repair_succeeded`。

## L2 完整开发环境

- [x] `.venv` 可运行 M1 与 M5 staged scripts。
- [x] `cryptography` 为显式项目依赖，不依赖偶然传递安装。
- [ ] CI/Docker 在干净环境重建并复跑相同 gate。
  - 当前：已提供 `.github/workflows/production-gate.yml`、`scripts/run_production_gate.ps1` 与无缓存安装；仍须首次远端 workflow/镜像日志。阻塞 owner：CI/平台维护者。
- [ ] 浏览器/API/build stamp 由独立部署验收确认一致。
  - 阻塞 owner：部署与 UI 验收；本波未伪造浏览器证据。

## L3 生产等价基础设施

- [x] 进程外 signer client seam、key id/digest 校验和 HTTPS 约束已实现。
- [x] durable SQLite ledger 与 active/retired/revoked verifier 行为有离线测试。
- [x] builder dry-run 验证 issued package、digest、key、entrypoint、preflight 和 receipt；裸 HTTP 200 不算接受。
- [x] production mode 已注入真实 discovery/repair run path；ledger/verifier/signer 缺配置时 fail-closed，且不降级 dev。
- [x] builder dry-run typed result 可由受控 adapter 注入并持久化到 `AgentRunRecord`，不依赖 Runner 或 HTTP 文案。
- [x] 本地 production-equivalent smoke 使用一次性测试 key 与临时 ledger，且有统一 production gate；仅证明机制。
- [x] lab-only HTTPS signer 以运行时自签证书/临时私钥完成真实 TLS、Bearer、key/digest/signature 对接；明确不是 KMS/HSM。
- [x] 本机多进程争用同一 SQLite 路径时，idempotency reserve 与 metric pair consume 均仅一个 worker 成功，重启后 replay 失败。
- [x] L3 evidence collector 只输出白名单字段和 token/nonce/idempotency 指纹；`L3_SIGNOFF.md` 模板保持 `NOT_APPROVED`。
- [ ] staging secret manager/KMS/HSM 实例已接入（不得使用测试内存 key）。
  - 阻塞 owner：安全/平台；需要真实受保护 signer endpoint 与 secret injection 记录。
- [ ] 多 worker 共享持久卷、备份恢复、权限和锁语义已实机验证。
  - 当前只完成同机多进程/同路径 SQLite；未证明 K8s/VM 跨 worker 共享卷。阻塞 owner：SRE/平台；需按证据模板留存并发、备份与 restore/replay。
- [ ] 真实只读 repository → materialize → production-equivalent sign → builder preflight 已留下脱敏 receipt。
  - 阻塞 owner：staging 运维 + 科学负责人；不得用 synthetic receipt 代替。
- [ ] 安全/运维/科学负责人已审阅 blocker、预算、回滚和 key rotation。
  - 阻塞 owner：对应负责人；签署模板仍为空。

## 裁决

- 当前自动化产物可提交 Grok/独立审计复核。
- 未勾选 L2/L3 项是 production GO 的真实阻塞；不得以 `READY_FOR_GROK` 替代产品上线批准。
