# M2 Canonical BuildReadyPackage Materialization 报告

日期：2026-07-22  
角色：`@lead`  
权威：`docs/plans/MEETING_CONSENSUS_PLAN.md` M2

## 1. 交付结论

已实现从 typed、确定性 run state 组装 canonical `BuildReadyPackage` material 的薄 seam，并接入 audit 持久化点。

该 seam **只生成待 Authority 签发的材料**，不签名、不授予成功。即使 package material 完整，只要没有合法 production/dev-explicit authority inventory，`PublicationContractRegistry` 仍返回 `succeeded=false`、`success_ui_allowed=false`。

## 2. 主要改动

### 2.1 `agent.discovery.publication`

新增：

- `BuildReadyMaterializationResult`
  - `package`
  - `evidence_observations`
  - `membership_refs`
  - `blockers`
  - `ready_for_authority_signing`
- `materialize_build_ready_package(snapshot)`
  - 输入 run/audit/manifest/EvidenceStore/membership/builder-preflight/constraints 的 typed snapshot；
  - 输出 canonical package 或 blockers；
  - 不调用 signer，不创建 `BusinessCompletionDecision`。
- canonical refs：
  - `manifest:sha256:*`
  - `evidence-store:sha256:*`
  - `package:sha256:*`

`BuildReadyPackage` 新增可选 `builder_preflight_ref`。旧 v1 package digest 使用 `exclude_none=True` 保持回放兼容；新 package 的非空 preflight ref 会进入 canonical digest。

### 2.2 通用 fail-closed 条件

下列任一缺失都会返回 blocker，且 `package=None`：

- run id；
- audit ready / audit ref，或 audit run 不匹配；
- manifest 缺失/非法、manifest run 不匹配、无项目或无文件；
- builder entrypoint、preflight ready、preflight ref；
- EvidenceStore artifact；
- membership inventory；
- manifest project 没有 build-ready file；
- file 非 `valid`、`needs_review`、缺 URL/size、file role 不支持；
- exact file-scope `builder_file_entry` observation 缺失；
- observation membership 不在 inventory；
- hard constraint observation 缺失、unknown 或 conflict；
- 非法/超限/duplicate hard constraint。

Soft constraint 没有 observation 不会单独阻塞 materialization。

### 2.3 Evidence 与 membership 边界

- file evidence 必须是 `subject_kind=file`、`evidence_scope=file`、`subject_id` 精确匹配 canonical file id；
- project observation 不能冒充 file observation；
- membership ref 必须同时存在于 file observation 和 run 的 membership inventory；
- materializer 不发明 membership；
- hard constraint evidence 必须 dimension/scope/value/operator 可计算通过。

### 2.4 Run record 与 audit 薄接线

`AgentRunRecord` 新增 typed 字段：

- `publication_evidence_store`
- `publication_builder_entrypoint`
- `publication_builder_preflight_ref`
- `publication_builder_preflight_status`
- `publication_materialization_blockers`

`_persist_discovery_audit_snapshot(...)` 在 run 尚无 package material 时：

1. 从 `current_manifest_path` 读取 typed manifest；
2. 从 run record 读取 EvidenceStore、membership 与 builder preflight；
3. 调用 materializer；
4. 持久化 package/observations/membership/blockers；
5. 再按既有逻辑执行 publication evaluate。

只有显式 `DISCOVERY_AUTHORITY_DEV_SIGN=1` 的既有开发路径才可能临时签发；默认和生产未配置 signer 路径继续 fail-closed。

## 3. 测试

新增 `tests/test_discovery_build_ready_materialization.py`，覆盖：

1. 完整 typed material 生成稳定 canonical package，但不自动签名；
2. membership 缺失不发明引用；
3. `weak_keep` 与 hard evidence unknown 阻断；
4. 32 candidates / 20 judgment / 0 files 不生成 package；
5. audit persist 可保存 package material，但默认无 signer 仍不毕业；
6. materialization 阻塞时保留已有 membership inventory，供后续修复重试。

命令：

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_discovery_build_ready_materialization.py `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_wiring_dev_publication.py `
  tests/test_discovery_m1_audit_extra.py
```

结果：加入 inventory-preservation 用例后的同组为 `47 passed`。

扩展回归：

- Authority/peer/wiring/agenda/M1 audit/materializer：`246 passed`；
- agent-turn/task-build-plan/control-plane：`205 passed`；
- 可收集 web：`103 passed`；
- frontend：`191 passed`，production build 通过。

最终将上述 Python 分组合并复跑：`555 passed in 58.30s`。

## 4. 未做项与风险

- 尚无生产 evidence producer 自动填充 `publication_evidence_store`、membership inventory 和 builder preflight；缺这些 typed inputs 时是预期 blocked。
- 尚无进程外 production signer、durable ledger、key rotation/revocation；本轮不解决 M4。
- `builder_preflight_ref` 证明 materializer读取了确定性 preflight artifact 引用；最终真实性仍必须由 production signer/inventory 与 builder service 共同验证。
- 当前 materializer 对 selected manifest 采取严格全包策略：任一项目无 build-ready file 就不产生 package，避免静默丢项目。未来若支持显式 subset，必须由 typed selection contract 表达，不能自动忽略。
- `test_web_discovery.py` 的 `agent.projects` collection 缺口属于 M1，未由 M2 stub 掩盖。
- 没有写入私钥、`.env`、真实 credentials 或 live repository 数据。

M2_STATUS: READY_FOR_GROK
