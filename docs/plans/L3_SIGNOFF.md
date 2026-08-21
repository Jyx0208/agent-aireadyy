# L3 正式环境签署表

状态：`NOT_APPROVED`。本文件是空白模板；任何自动化 agent、测试或单一负责人不得自行将产品改为 GO。

关联证据：

- L3 evidence id：
- deployment / build stamp：
- commit / image digest：
- production gate CI run URL：
- 真实 repository → builder receipt artifact：
- 风险/例外审批编号：

## 前置门禁

- [ ] 干净 CI/镜像 production gate 全绿并归档日志。
- [ ] 真实 KMS/HSM/secret manager；应用无生产私钥。
- [ ] ≥2 production worker 共享 durable volume，并完成并发、备份、restore/replay 演练。
- [ ] 真实只读 repository → materialize → sign → builder receipt 正向证据。
- [ ] signer 断开、坏 membership、hard unknown/conflict、replay 负向证据均为 blocked。
- [ ] 浏览器 build stamp/API/run record/receipt 一致。
- [ ] blocker、预算、回滚、key rotation、告警和最小权限已审阅。

## 科学负责人

- 姓名/组织：
- 审阅时间（UTC）：
- 证据范围：
- 结论：`PENDING`
- 签名/审批记录：
- 限制与例外：

## 安全负责人

- 姓名/组织：
- 审阅时间（UTC）：
- KMS/HSM、证书、secret、key lifecycle 结论：
- 结论：`PENDING`
- 签名/审批记录：
- 限制与例外：

## 运维负责人

- 姓名/组织：
- 审阅时间（UTC）：
- multi-worker、volume、backup/restore、监控结论：
- 结论：`PENDING`
- 签名/审批记录：
- 限制与例外：

## 独立审计

- 审计人/组织：
- 审阅时间（UTC）：
- evidence package hash：
- 结论：`PENDING`
- 审计报告：

## Product GO 决议

- 决议：`NOT_APPROVED`
- 决议人/委员会：
- 决议时间（UTC）：
- 生效环境/版本：
- 回滚条件：

只有上述门禁全部完成且三方与独立审计签署后，人工 GO 评审才可改变本决议。
