# M3 Overnight 最小切片报告

日期：2026-07-23  
状态：`M3_OVERNIGHT_STATUS: IMPLEMENTED_AND_TESTED`（含 V2 安全 adapters）

## 本轮切片

### P2 — Runner v2 proposal intake

Runner final output 现在只有在显式提供 `discovery-repair-proposal/v2` envelope（单个 proposal 或 `repair_proposals` 数组）时，才会作为 structured v2 proposal 被接收。普通文本、非 JSON 和非 v2 envelope 不会被解释成 repair 权限。

有效 v2 proposal 被传入现有 `run_authority_repair_cycle(..., proposals=...)`，并继续经过同一个 `RepairAuthority.review_proposal` admission、idempotency reservation、Authority-owned metric observation、dispatch、re-audit、no-progress 与 publication 流程。它没有独立 dispatch 或成功通道。

### V2 — 安全 capability adapters

`_dispatch_authority_repair` 现接线两个此前 `registered_adapter_not_wired` 的原语：

1. **`materialize_evidence`**  
   - 仅从 run 上已有 `publication_evidence_store` 按 `observation_ids` 提升 observation；  
   - 经 `EvidenceStore.materialize` 校验 source/membership refs；  
   - 不发明 observation、不改 package、不签名；  
   - 冲突 id / 未知 id / 缺 store → `blocked` + 明确 reason。

2. **`refresh_auth_context`**  
   - 仅当 run 已持有**更新**的 `latest_candidate_search_id`（及可选 `active_grant_id`）且与 `stale_*` 参数不同时，才清除 `stale_context` issue；  
   - 计入 `auth_refresh_attempts`（上限 1）；  
   - 不 mint 凭据、不外呼 auth、不回显 secrets；  
   - active 仍等于 stale → `refresh_context_still_stale`。

两者都可产生 `repair_progressed` metric delta，**不能**单独产生 `repair_succeeded` / `build_ready_succeeded`。

## 安全边界

- 未注册 capability 在 Authority admission 阶段 fail-closed，dispatch 不发生；
- issue policy、参数 schema、risk/budget、idempotency 与 metric whitelist 没有放宽；
- Runner 输出本身不能生成 `repair_succeeded` 或 `build_ready_succeeded`；
- 无 metric progress 时仍写 `repair_no_progress` / `repair_incomplete`；
- 唯一业务成功仍须 Registry/Authority issued build-ready；
- 未实现完整 Project subsystem、生产 signer、durable ledger。

## 验证

```text
.\.venv\Scripts\python.exe -m pytest -q tests/test_discovery_wiring_repair_authority.py
# 9 passed

.\scripts\run_m1_gate.ps1
# 365 passed in 43.50s
```

新增覆盖：store 内 observation 提升无假成功；未知 observation fail-closed；fresher context 清除 stale 无假成功；context 仍 stale 时 blocked。

### V2 续 — materializer 真实路径 integration

`tests/test_discovery_build_ready_materialization.py` 现共 13 用例；在纯 unit 之上增加跨 seam 路径：

- promote → preflight-blocked audit → preflight-ready re-audit 物化 package，全程 `succeeded=false` / 无 success UI；
- promote-only 不写 package / business_completion；
- project-scope 不能替代 file-scope builder entry；
- soft constraint 缺 observation 不阻塞；
- 已有 package 二次 audit 保持稳定；corrupt manifest 与 preflight pending fail-closed。


## 非声明

本报告不表示生产 signer/durable ledger、L3、Project wave 或产品 GO。产品状态继续 **NO-GO**。
