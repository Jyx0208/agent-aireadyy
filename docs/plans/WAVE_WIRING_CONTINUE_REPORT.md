---
document: WAVE_WIRING_CONTINUE_REPORT
authority: docs/plans/LOCKED_PLAN.md + docs/plans/WIRING_CHECKLIST.md
base: docs/plans/WAVE_WIRING_A_REPORT.md
scope: RepairAuthority 主循环 + 显式 dev publication signer
network: offline
business_completion: build-ready only
status: READY_FOR_GROK
---

# Wiring Continue 实施报告

## 1. 结论

Wiring Continue 已把 Wiring A 的两个主要未做项接入：

1. quality audit 的 repair 路径不再启动第二次 Runner，也不再存在“第二 Runner 返回即修复完成”的双轨；v1 audit action 先升级为开放 `RepairProposal` v2，再统一进入 `RepairAuthority` admission、execution reserve、Authority metric pre/post、delta、re-audit、publication 与 terminal event 链。
2. 新增显式开启的 dev/test publication signer。默认产品路径不签发；只有 `DISCOVERY_AUTHORITY_DEV_SIGN=1` 或测试显式调用允许生成进程内临时 Ed25519 签名。私钥不写入 run store、fixture、日志或仓库。

唯一业务毕业仍为 issued build-ready completion。候选、审查、正 delta、Runner 返回、repair attempt finished 均不能单独触发成功。

当前只声明 `READY_FOR_GROK`，不声明 merge-ready。

## 2. 变更文件

- `src/agent/control_plane/models.py`
  - `AgentRunRecord` 新增可序列化 Authority state：
    - `build_ready_package_material`；
    - `publication_authority`；
    - `publication_evidence_observations`；
    - `publication_membership_refs`；
    - `repair_execution_keys`；
    - `repair_no_progress_signature/count`。
- `src/agent/control_plane/repair.py`
  - v1 upgrader 不再把空 `project_accessions/constraint_ids` 塞入 v2 参数，避免合法 stop action 被空数组 schema 误拒绝。
- `src/agent/control_plane/openai_agents.py`
  - 新增 `run_authority_repair_cycle(...)`；
  - 新增 Authority-owned metric reader、issue-policy compatible context、evidence scope 投影、capability dispatcher、no-progress signature 持久化；
  - quality repair 主循环删除第二 Runner 调用，只保留第一次智能层 Runner；
  - reject/degrade/approve、attempt started/finished、progress/no-progress/incomplete/success 均写权威事件；
  - idempotency key 在 dispatch 前保存到 run record；
  - post action 重新 audit + publication；只有当前 RepairAuthority completion nonce 对应的 issued completion 可产生 repair/build-ready success；
  - `_persist_discovery_audit_snapshot` 可读取 run store 中的完整 package/evidence/membership material，并在显式 dev 开关下签发临时 inventory。
- `src/agent/discovery/publication.py`
  - 新增 `issue_dev_publication_authority(...)` 与 `dev_publication_signing_enabled()`；
  - 默认关闭；可使用进程内临时 Ed25519 key，或从 `DISCOVERY_AUTHORITY_SIGNING_KEY` 读取外部 Ed25519 PEM；
  - dev verifier 只保存进程内 public key；签名绑定 canonical Authority inventory 与 package digest；进程重启后旧临时签名自然 fail-closed。
- `tests/test_discovery_wiring_repair_authority.py`
  - v1→v2→Authority→metric/delta→events 纵切；
  - 持久化 idempotency + 第二次相同 signature 无进步达到上限 2，禁止再次 dispatch/假成功。
- `tests/test_discovery_wiring_dev_publication.py`
  - 默认无签名仍 blocked；
  - 显式 dev sign + 完整 package/evidence/membership 可毕业；
  - 未显式开启时直接 helper 调用被拒绝。
- `tests/test_discovery_runtime_provenance.py`
  - 两个旧“第二 Runner/至少两模型 turn”测试改为新单轨契约：只运行一次 Runner，Authority repair 不消耗第二模型 turn，未知 LP6 issue fail-closed。
- `docs/plans/TEAM_BOARD.md`
  - 追加开始与完成/复审消息。

## 3. RepairAuthority 主循环

### 3.1 Admission

每个 audit `repair_action` 先经 `upgrade_v1_repair_action(...)` 形成 `discovery-repair-proposal/v2`。主循环从 audit issues 中只选择同时满足以下条件的 LP6 context：

- issue policy 允许 proposal capability；
- issue policy 允许 proposal metric；
- 当前 run 的 Authority evidence scope 达到最低要求。

缺 issue context、未知 issue/capability/metric、参数 schema 不合法、risk/预算超限、hard bypass 或重复 idempotency 均 fail-closed，并写 `repair_proposal_rejected`/`discovery_quality_repair_stopped`。

### 3.2 Authority metric 与 delta

metric reader 只从当前 `AgentRunStore` 的 typed run/audit/publication state 读取：

- candidate、reviewed、judgment-qualified；
- verified observations；
- unresolved claims、missing build-ready fields；
- hard conflict/unknown；
- build-ready project/file；
- context freshness、audit ready。

调用者/Runner不能提供 pre/post 数字。顺序为：approve → `mark_execution_started` → 持久化 idempotency key → capture pre → dispatch registered primitive → re-audit/publication → capture post → `record_attempt`。

### 3.3 Dispatch 与停止

dispatcher 只调用现有 `DiscoveryToolService` 能力：search、inspect、re-audit/recompute、select；`stop_with_limitations` 与 `ask_user_blocking_question` 只产生诚实停止。当前没有安全 service adapter 的 `materialize_evidence`/`refresh_auth_context` 会明确返回 `registered_adapter_not_wired`，不会执行 shell、任意 URL、代码或静默成功。

同 signature 第一次无 progress 会持久化 signature/count；第二次等价 proposal 被 idempotency 拦截且 count 达到 2，发 `repair_no_progress + repair_incomplete` 并以 `no_progress_limit_reached` 停止，不重复 dispatch。

### 3.4 Terminal success

每个实际 attempt 生成当前 `RepairAuthority` 私有 completion nonce，再重新 evaluation。`events_for_finished_attempt(...)` 只消费与当前 authority/attempt/nonce 一致的一次性 issued completion：

- 材料仍未 build-ready：`repair_attempt_finished + repair_incomplete`；
- metric 正 delta 但未毕业：可有 `repair_progressed`，仍无成功；
- 只有 issued build-ready：`repair_succeeded + build_ready_succeeded`。

legacy `discovery_quality_repair_completed` 继续保留回放兼容，但 payload 只有 attempt finished、audit、business completion 与 authority cycle 摘要；事件名本身不授予成功。

## 4. Dev publication signer

### 4.1 默认关闭

以下条件缺一不可：完整 `BuildReadyPackage`、Authority observations、verified membership refs、匹配的 audit ref，以及显式 dev 开关。默认无开关时，即使 material 完整也不生成 `PublicationAuthorityState`，decision 保持 `succeeded=false`。

### 4.2 显式开启

- `DISCOVERY_AUTHORITY_DEV_SIGN=1`：允许 wiring 自动调用；
- `allow_dev_signing=True`：允许测试显式调用；
- 可选 `DISCOVERY_AUTHORITY_SIGNING_KEY`：从环境加载 Ed25519 PEM，不从仓库或 `.env` 读取固定私钥；
- 未提供 PEM 时每次签发使用进程内临时 Ed25519 私钥，仅 public verifier 留在内存 registry。

签名覆盖完整 Authority inventory；Registry 仍验证 canonical package digest、run/audit/manifest/EvidenceStore/builder refs、membership 与 observation。dev helper 没有绕过 peer-audit 的任一材料门。

## 5. 测试命令与结果

### 5.1 Wiring/Authority focused

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_wiring_repair_authority.py `
  tests/test_discovery_wiring_dev_publication.py `
  tests/test_discovery_wiring_publication_to_record.py `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_authority_properties.py `
  tests/test_discovery_evidence_store.py
```

```text
93 passed in 3.24s
```

### 5.2 最终离线组合

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_wiring_repair_authority.py `
  tests/test_discovery_wiring_dev_publication.py `
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
252 passed in 25.55s
```

全部 peer-audit/property/Wiring A 负例保持启用，无 xfail/skip、无网络、无 live PRIDE、无真实私钥。

### 5.3 静态检查

相关 Python 文件 `py_compile` 通过；`git diff --check` 通过。当前环境仍未安装 `ruff`、`typer`、`fastapi`，因此无法收集 `tests/test_control_plane.py` 与 web suites；没有用 skip/xfail 掩盖该限制。

## 6. 风险与后续

- 当前 repair proposal 来源以 audit v1 action upgrader 为主；开放 v2 envelope 与 capability composition 已进入 Authority，但尚未从第一次 Runner 的 structured final output 直接提取任意新 proposal。不能为此恢复第二 Runner 成功双轨。
- `materialize_evidence` 与 `refresh_auth_context` 尚无现成安全 `DiscoveryToolService` adapter；当前明确 blocked。后续应加注册 adapter 与测试，而不是在主循环写科学案例分支。
- dev signer 只供测试/开发；生产必须使用进程外 signer、durable public-key/ledger 配置与密钥轮换。不得把 dev env 开关作为生产毕业方案。
- 进程内临时 dev signature 重启后不可验证，这是 fail-closed 的预期开发语义。
- `src/agent/web/app.py`、frontend 与 Wave 5 agenda 本轮未修改。
- 共享 worktree 中 `publication.py` 等前序 Wave 文件仍为未跟踪状态，无法安全创建只含本轮的独立 commit；未 stage/commit 他人历史改动。

WIRING_CONTINUE_STATUS: READY_FOR_GROK
