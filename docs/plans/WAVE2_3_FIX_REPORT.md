---
document: WAVE2_3_FIX_REPORT
authority: docs/plans/LOCKED_PLAN.md
trigger: docs/plans/_CODEX_PEER_AUDIT_W2W3.md
scope: Wave 2/3 fail-closed remediation
business_completion: build-ready only
status: READY_FOR_PEER_AUDIT
---

# Wave 2/3 Peer Audit 修复报告

## 1. 状态更正

编排已裁决此前 Wave 2/3 为 **FAIL**。首轮 7 个负例修复后，第二轮 peer audit 又发现 R1–R6 共 6 个 residual fail-open。本文已纳入两轮修复；当前仅声明 residual 修复完成并等待 @audit/Grok 复审，不声明 merge-ready 或 PASS。

## 2. 修复内容

### 2.1 Publication：毕业只由已验证 BuildReadyPackage 驱动

修改 `src/agent/discovery/publication.py`：

- 新增 `BuildReadyPackage`、`BuildReadyFile`、`BuildReadyConstraintEvidence`；
- audit 必须显式为 `ready` 且带 `latest_audit_ref`，缺失 audit/provenance 一律 fail-closed；
- 裸 `build_ready_count/projects/files` 不再签发毕业；build-ready project/file 数只从通过 Registry 校验的 package 派生；
- package 必须带 Authority issuer/run、audit/manifest/EvidenceStore refs、builder entrypoint、唯一文件、项目 membership、文件 evidence refs，且无 unresolved；
- `weak_keep` 不在 `BuildReadyFile.validity_status` 合约内，不能派生 build-ready；
- package 数与 state 显式计数冲突时 fail-closed；
- hard constraint 只认 package 中 exact `constraint_id + dimension + scope + operator + observed_value + source_refs` 的 materialized observation，并重新执行 operator/value 比较；
- 自报 `constraint_assessments=pass` 或 `evidence[scope]=[dimension]` 不再构成 hard 通过证据；自报 fail/unknown 仍保守地阻断；
- `BusinessCompletionDecision` 升为 `business-completion/v2`，嵌入通过校验的 package；只有该对象可令 `success_ui_allowed=true`。

正向 synthetic fixture 已升级为包含完整、脱敏的 verified package，不再通过裸计数维持旧绿测。

### 2.2 EvidenceStore：归一化 provenance 与 membership edge

修改 `src/agent/discovery/evidence_store.py`：

- `source_refs` strip/dedupe 后再次检查非空；`["   "]` 不能绕过 `min_length`；
- `EvidenceStore` 增加 Authority 提供的 `available_membership_refs`；
- observation 可记录调用者声明的 membership，但跨 scope resolve 只有在该 edge 同时存在于 Authority verified membership 集合时才成立；
- invented `file:<id>` 不再把 assay/project evidence 提升为 file evidence。

### 2.3 Repair：LP6 policy、参数 schema、可信 delta 与成功事件

修改 `src/agent/control_plane/capabilities.py`、`repair.py`：

- `IssueCapabilityPolicy` 增加 `minimum_evidence_scope`；
- review 从 context 读取 `issue_code_set/issue_codes`，未知 issue、越界 capability、metric、risk ceiling 或缺最低 evidence scope均解释性 reject；
- `CapabilityPrimitive.parameter_schema` 从字符串标记改为实际字段/JSON 类型 schema；composition 逐字段校验，`delete_database` 等未注册参数被拒绝；
- decision 返回稳定 `idempotency_key`，并对 context 中已执行的等价 key 拒绝重复副作用；
- 新增 `AuthorityMetricObservation`。只有 actual typed pre/post observations 同时匹配 registry metric 的 source、aggregation 与 scope fingerprint，才能计算 delta 并发 `repair_progressed`；
- 调用者/Runner 直接提交 raw `pre/post/delta` 不再成为事实：记为 `untrusted_metric_observation` 和 no-progress，绝不产生 progressed/succeeded；
- `events_for_finished_attempt` 只允许 `attempt_event=repair_attempt_finished` 进入成功判定；
- 成功判定只接受 `PublicationContractRegistry` 产出的 typed `BusinessCompletionDecision`，并复核 embedded `BuildReadyPackage` 与 progress counts；普通 mapping、`runner_returned` 和 legacy completion event 最多得到 `repair_incomplete`。

默认同一 no-progress signature 连续 2 次停止的既有行为保持不变。

### 2.4 第二轮 R1–R6：从公开结构升级为 Authority issuance

- **R1 package self-certification**：新增 `PublicationAuthorityState`，evaluation 会把 package 的 run/audit/manifest/EvidenceStore/builder refs、每个 file membership、file observation、hard constraint observation 与 Authority inventory 逐项交叉验证；调用方填写 `validated=true` 或非空字符串不再足够。
- **R2 hard constraint 丢失/伪证据**：normalize 前检查 raw constraint 列表；非法或重复 hard binding 会变成 `hard_unknown` blocker，不能被兼容适配器静默丢弃。hard observation 必须与 Authority EvidenceStore inventory 中相同 observation id 的 dimension/scope/value/source refs 完全一致。
- **R3 metric self-issuance**：`RepairAuthority` 改为注入 `AuthorityMetricReader`；`capture_metric_observation(...)` 不再接受调用方 value，而是从 reader 读取并写入实例 issuance ledger。公开构造的同型 `AuthorityMetricObservation` 没有 ledger issuance，不能产生 `repair_progressed`。
- **R4 issue context omission**：活动 repair 缺 `issue_code_set/issue_codes` 时返回 `missing_issue_context`，不能绕过 LP6 admission。
- **R5 idempotency metric bypass**：operation key 按每个 primitive 的 `idempotency` 声明生成；`parameter_hash` 策略只绑定 primitive composition 与参数，不包含 metric/issue 文案。Authority 持有已执行 key ledger，并可在 dispatch 前用 `mark_execution_started(...)` 原子保留；只换 metric 无法获得新 key。
- **R6 typed completion forgery**：成功的 `BusinessCompletionDecision` 由 `PublicationContractRegistry` 对完整 payload 签发进程内 HMAC seal。`events_for_finished_attempt(...)` 验证 seal；普通 mapping、调用方自行构造 typed decision，或篡改 package run/audit/ref 后保留旧 token 均不能产生成功事件。

### 2.5 第三轮 S1–S4：opaque provenance 与一次性 attempt 结算

- **S1 package/inventory 同源自报**：`PublicationAuthorityState` 新增 Authority issuance token。产品只内置 RSA 公钥并验证 PKCS#1 v1.5 SHA-256 signature，私钥不在 caller/Runner 或产品验证路径中；package 与 snapshot inventory 即使同步改成互相吻合的假 refs，旧签名也会失效。Registry 只有在 opaque inventory signature、run/audit/manifest/EvidenceStore/builder refs、membership 和 observations 全部通过后才签发 completion。
- **S2 normalization 100 条上限**：raw constraints 超过当前兼容上限时整体产生 `constraint_limit_exceeded` hard-unknown blocker；不会先截断再忽略第 101 个及之后的 hard constraint。
- **S3 completion replay**：每个 `RepairAuthority` 有独立随机 authority id；调用 publication 前必须通过 `completion_context(attempt_id)` 预登记 recipient/attempt。Registry 把 authority id 和 attempt id 纳入 completion seal，`events_for_finished_attempt(...)` 只接受当前实例的 pending attempt，结算后消费 token。旧 run 的合法 completion 不能交给新 Authority 或第二次 attempt 重放。
- **S4 metric pair replay**：pre/post observation token 只有在同一实例 ledger 中存在、digest/source/aggregation/scope 全匹配时才可结算；第一次 record 后原子移除两枚 token。重复提交同一 pair 不再发 `repair_progressed`。

离线正向 fixture 使用独立 Authority 签发的 inventory signature；测试与产品仓库不包含签发私钥。生产部署须由确定性 Authority Plane signer 生成同一 canonical contract 的 token，Runner 仅能提交 opaque handle。

### 2.6 第四轮 T1–T3：package material、hard identity 与私有 nonce

- **T1 signed inventory package substitution**：Authority inventory 新增 `authorized_package_digest`，其值是 canonical `BuildReadyPackage` 全量 JSON（包括 project ids、file ids、project membership、URL、size、role、evidence refs 等）的 SHA-256；该 digest 自身受 Authority RSA signature 覆盖。Registry 在验证 inventory signature 后重新计算实际 package digest，替换任何 builder material 都会返回 `build_ready_package_material_mismatch`，不能借用旧 inventory 毕业。
- **T2 duplicate hard downgrade**：raw constraint 审计不再只追踪 hard 分支。所有可识别 constraint id 先按 raw 输入分组；任一重复组只要包含 hard，就产生 `duplicate_hard_constraint` blocker，无论后项是合法 soft、open 或另一个 hard。全局兼容 normalizer 的 last-write-wins 不再能降低用户 hard。
- **T3 recipient self-certification**：`completion_context(attempt_id)` 现在为每次 pending attempt 生成实例私有随机 nonce，ledger 保存 `attempt_id → nonce`。Registry 将 `repair_authority_id + repair_attempt_id + repair_attempt_nonce` 全部纳入 completion HMAC；结算时逐项比对并一次性删除。复制公开 `authority_id`、重建同名 attempt 只能得到不同 nonce，不能消费旧 completion。

本轮红测直接对应 `LOCKED_PLAN` 的 build-ready material 真实性、hard fail-closed 与 Runner≠success 三项生产威胁；未扩大到无关密码学或 Python 私有字段攻击。

## 3. 测试变更

- `tests/test_discovery_authority_peer_audit.py`：审计方 7 个红灯未削弱、未 xfail；现已全部转绿。
- `tests/test_discovery_publication_contracts.py`：
  - 正向状态改用 verified package；
  - 新增 hard 自报 pass/同名 dimension 不构成证据的回归。
- `tests/test_discovery_evidence_store.py`：正向跨 scope resolve 显式提供 verified membership edge。
- `tests/test_discovery_repair_controller.py`：
  - raw pre/post 改断言为 untrusted；
  - 新增 typed Authority observations 的正向 delta；
  - 新增真实参数 schema 负例；
  - 成功事件正例改为 Registry 产生的 typed completion。
- `tests/fixtures/discovery/synthetic_rt_psm_build_ready_transition.json`：build-ready control 增加完整 package/audit/evidence provenance。

全部测试离线，不访问 live repository、网络或 secrets；未加入案例特判。

## 4. 验收命令与输出

### 4.1 编排指定四文件

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_evidence_store.py
```

```text
57 passed in 1.19s
```

### 4.2 扩展相关 sacred 回归

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_evidence_store.py `
  tests/test_discovery_constraint_bindings.py `
  tests/test_discovery_quality_audit.py `
  tests/test_discovery_scientific_constraint_validity.py `
  tests/test_discovery_mixed_acquisition_policy.py `
  tests/test_discovery_sdrf_assay_evidence.py
```

```text
196 passed in 18.81s
```

## 5. 边界与待复审事项

- 本轮没有改 `src/agent/web/app.py` 或 frontend，没有开始 Wave 4。
- `AuthorityMetricObservation` 现在必须由注入的 Authority reader capture 并登记到当前 Authority 实例 ledger；生产主循环仍需把 reader 接到权威 state store。
- idempotency 由 Authority 实例 ledger 和持久化 context 的 executed keys 共同校验；跨进程 durability 仍依赖 run store 接线。
- package provenance 由带非对称 signature 的 `PublicationAuthorityState` inventory 交叉验证，成功 completion 再绑定当前 repair authority/attempt 并一次性消费；生产接线必须由确定性平面持有私钥并签发，禁止 Runner 写入。
- 完整 Python 3.13 + `openai-agents` 环境的既有 W1-N1/W2-N1 仍待补跑。

WAVE2_3_FIX_STATUS: READY_FOR_PEER_AUDIT
