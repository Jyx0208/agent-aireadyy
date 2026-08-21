---
document: WAVE_WIRING_A_REPORT
authority: docs/plans/LOCKED_PLAN.md + docs/plans/WIRING_CHECKLIST.md
scope: Authority Plane 到真实 discovery 路径的最小纵切
network: offline
business_completion: build-ready only
status: READY_FOR_GROK
---

# Wiring A 实施报告

## 1. 结论

Wiring A 已完成允许范围内的最小纵切：OpenAI Agents discovery 的确定性 audit 持久化点现在生成并保存 typed `BusinessCompletionDecision`；run summary 与 web record 暴露 `record.business_completion`；manifest selection 和 run-level 完成态均受 issued build-ready completion 门禁；legacy `discovery_quality_repair_completed` 仅表示一次 attempt 已结束，不再携带成功语义。

当前生产路径尚无外部 Authority signer、signed inventory、EvidenceStore/builder package material，因此真实任务会 fail-closed 为进度/阻塞状态。这是预期行为：候选、审查结果和 manifest 仍可作为中间材料展示与下载，但不能触发业务完成或成功绿勾。

本报告只声明 `READY_FOR_GROK`，不声明 merge-ready。

## 2. 变更文件

- `src/agent/control_plane/models.py`
  - `AgentRunRecord` 新增 typed `business_completion: BusinessCompletionDecision | None`；
  - 通过现有 `AgentRunStore` JSON payload 自动实现 save/load round-trip，旧记录缺字段时保持兼容。
- `src/agent/control_plane/openai_agents.py`
  - `_persist_discovery_audit_snapshot` 从 run store 中的 request、audit counts、issue codes 组装 publication snapshot；
  - 为当前 audit 生成 canonical SHA-256 audit ref，调用 `PublicationContractRegistry.evaluate(...)` 并与 audit 一起保存；
  - summary 写入完整 `business_completion`；
  - second Runner 结束后的 legacy event 改为 `attempt_status=finished + audit + business_completion` envelope，event 本身不声明 succeeded；
  - finalization 只在 Registry-issued build-ready completion 通过时发 `run_completed`，否则为 `run_blocked`；closing audit 缺失时主动清空旧 completion，防止 stale decision 继续授权成功。
- `src/agent/control_plane/discovery.py`
  - `select_discovery_manifest` 与 `auto_select_best_manifest` 在已存在 publication decision 时检查唯一 build-ready 门；未毕业时记录 `manifest_selection_rejected`，不把 Runner 结束视为选择成功。
- `src/agent/discovery/publication.py`
  - 新增共享谓词 `business_completion_allows_success(...)`，统一验证 succeeded/status/package kind、正 build-ready project/file 数、package 存在与 Registry issuance token。
- `src/agent/web/app.py`
  - `_public_discovery_record` 最小投影 validated `business_completion` 到 record 顶层；伪造/未签发的 succeeded decision 不对外投影；
  - history record 同样使用该验证边界；
  - blocked run 可回退读取 candidate-pool manifest，从而保留中间进度而不假装交付；
  - legacy repair-completed 文案改为“attempt finished，待/未通过 Authority audit”，不再声称修复完成或通过 delivery gate。
- `tests/test_discovery_wiring_publication_to_record.py`
  - 新增 7 个无网络测试，覆盖 typed round-trip、32/0、缺 inventory、summary 投影、legacy event envelope、伪造 completion 拒绝、issued completion 正向门和 selection fail-closed。
- `docs/plans/TEAM_BOARD.md`
  - 追加 Wiring A 开始/协作消息；完成消息见本报告写入后的板尾。

## 3. 已接线行为

### 3.1 Publication → run record

确定性顺序为：

1. audit 从 `DiscoveryToolService` 产生；
2. `_persist_discovery_audit_snapshot` 重新从 `AgentRunStore` 读取当前 run；
3. 使用 audit JSON 的 canonical SHA-256 作为 audit ref，不读取 Runner 文案作为事实；
4. publication snapshot 只包含当前 run 的 `scientific_constraints`、audit 状态、候选/审查/judgment-qualified counts 与 issue blocker counts；
5. 当前没有 signed inventory/package 时，Registry 必然返回 progress/blocked；
6. audit 与 decision 一次保存回 run record；summary/API 再投影该 typed decision。

32 candidates / 20 judgment-qualified / 0 build-ready 的离线 fixture 结果为：

- `progress.candidate_projects = 32`；
- `progress.judgment_qualified_projects = 20`；
- `progress.build_ready_projects = 0`；
- `status = blocked_with_progress`；
- `succeeded = false`；
- `success_ui_allowed = false`。

### 3.2 Selection 与 run completion 门禁

- audit ready 只是必要条件，不是业务毕业；
- `manifest_selected` 是材料选择事件，不是 build-ready success event；
- 已持久化 publication decision 但未通过 issued build-ready 全门槛时，manual/auto selection 均 fail-closed；
- run-level `completed`/`run_completed` 只认 `business_completion_allows_success(...)`；
- closing audit 失败或缺失时清空可能陈旧的 completion，并记录 `closing_publication_audit_missing` blocker。

### 3.3 Repair 最小接线

本轮采用任务授权的最小方案，没有实现完整自动 repair dispatcher：

- 保留现有 bounded second Runner 尝试；
- second Runner 返回后重新 audit + publication evaluate；
- legacy `discovery_quality_repair_completed` 仅形成 attempt-finished envelope；
- 无 Authority metric reader、无 issued completion 时不能产生 progressed/success UI 语义；
- finalization 始终重新检查 publication decision。

## 4. 测试命令与结果

### 4.1 Wiring A + Authority focused

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_wiring_publication_to_record.py `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_authority_properties.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_evidence_store.py
```

```text
88 passed in 2.21s
```

### 4.2 最终离线组合回归

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_wiring_publication_to_record.py `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_authority_properties.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_evidence_store.py `
  tests/test_discovery_constraint_bindings.py `
  tests/test_discovery_quality_audit.py `
  tests/test_discovery_maximize_backfill.py `
  tests/test_discovery_project_judgment.py `
  tests/test_discovery_runtime_provenance.py `
  tests/test_discovery_scientific_constraint_validity.py `
  tests/test_discovery_mixed_acquisition_policy.py `
  tests/test_discovery_sdrf_assay_evidence.py
```

```text
247 passed in 23.55s
```

全部 peer-audit/property 负例保持启用，无 xfail/skip；测试无网络、无 live PRIDE、无真实密钥。

### 4.3 静态检查与环境限制

```powershell
& 'E:\anaconda\python.exe' -m py_compile `
  src/agent/control_plane/models.py `
  src/agent/control_plane/discovery.py `
  src/agent/control_plane/openai_agents.py `
  src/agent/discovery/publication.py `
  src/agent/web/app.py `
  tests/test_discovery_wiring_publication_to_record.py
```

```text
exit code 0
```

当前 `E:\anaconda\python.exe` 缺 `typer` 与 `fastapi`，因此 `tests/test_control_plane.py` 在 collection 阶段报 `ModuleNotFoundError: typer`，web test 模块也不能在本环境收集。没有用 skip/xfail 掩盖；`app.py` 已通过 `py_compile`。`ruff` 同样未安装。

## 5. 未做项与风险

- 未接完整 v2 repair 自动循环：尚未把 v1 `repair_actions` 在主循环逐项升级为 `RepairProposal`、执行 `RepairAuthority.review_proposal/mark_execution_started`、持久化 idempotency reservation、以 `AuthorityMetricReader` capture pre/post、`record_attempt` 和消费 issued completion。现有 second Runner 只是一轮受限尝试，不获得 Authority success 权力。
- 未实现 production signer/inventory producer，也未把 EvidenceStore membership、builder entrypoint 与 canonical package material装入 run store；因此当前真实路径只能诚实停在 progress/blocked。不得通过放松 Registry 来“恢复绿勾”。
- 完整依赖环境需要补跑 `tests/test_control_plane.py` 和 web discovery suites；其中历史上把“有 manifest”断言为 `completed` 的 legacy 用例，必须按 build-ready 唯一毕业语义审查，不能通过绕过 Authority 来维持旧期望。
- `src/agent/web/app.py` 原有未提交 timeout 修改属于其他意图；本轮只修改 business-completion 投影、candidate-pool fallback 与 legacy repair 文案，未覆盖或重排无关 diff。
- 未修改 frontend、Wave 5 agenda、live repository adapter 或 secrets；未调用外部模型/网络。
- 共享 worktree 中 `publication.py` 等前序 Wave 文件本来未跟踪，无法安全制作只含 Wiring A 的独立 commit；本轮未 stage/commit 他人历史改动。

WIRING_A_STATUS: READY_FOR_GROK
