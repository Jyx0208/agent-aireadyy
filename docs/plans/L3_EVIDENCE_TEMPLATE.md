# L3 脱敏证据模板

每次 production-equivalent 或受控生产试跑复制一份本模板。不得填写私钥、bearer token、完整用户输入、真实样本敏感字段或未脱敏 URL query。

## 运行身份

- evidence_id：
- UTC 时间：
- 环境 / deployment id：
- commit / build stamp：
- run_id：
- workflow：discovery
- Authority mode：production

## 科学与 publication

- audit_ref：
- audit_status：
- hard constraint 结果（仅 code + pass/conflict/unknown）：
- evidence_store_ref：
- manifest_ref：
- membership inventory ref：
- candidate / reviewed / judgment-qualified / build-ready counts：
- unresolved blockers：

## Package / signer

- package_id：
- canonical package_digest：
- project_count / file_count：
- builder_entrypoint：
- builder_preflight_ref：
- signer key_id：
- key lifecycle（active/retired/revoked）：
- publication issuance token 指纹（仅 SHA-256，不贴 token）：

## Repair exactly-once（如适用）

- repair_authority_id：
- repair_attempt_id：
- completion nonce 指纹（仅 SHA-256）：
- idempotency key 指纹：
- metric_id / pre / post / delta：
- no-progress count：
- replay negative result：

## Builder receipt

- dry-run accepted：
- receipt_ref：
- receipt package_digest：
- receipt key_id：
- receipt entrypoint / preflight ref：
- HTTP status（仅诊断，不能作为 accepted 证据）：
- receipt artifact SHA-256：

## 基础设施证据

- signer 类型（KMS/HSM/受控服务；不写 secret）：
- ledger durable volume / backup snapshot id：
- worker 数与并发测试：
- restore/replay 演练结果：
- 最小权限审查 ticket：
- 告警/审计日志 ref：

## 人工签署

- 科学负责人 / 时间 / 结论：
- 安全负责人 / 时间 / 结论：
- 运维负责人 / 时间 / 结论：
- 独立审计 / 时间 / 结论：
- product GO 决议编号（未正式批准必须写 `NOT_APPROVED`）：
