# Discovery Agent 后续工程路线图（@lead 技术总成）

元信息：

- 角色：`@lead` 技术总成
- 日期：2026-07-22
- 决策基线：`LOCKED_PLAN.md` 的方案 2（灵活智能层 + 薄 Authority Plane）
- 业务完成：仅当材料符合科学要求且可进入 dataset build，即 **build-ready**，才算成功；候选发现与审查只是进行中进度
- 状态依据：Wave 1–4、Wave 6、Wave 2–3 peer audit、Wiring A、Wiring Continue、Wave B 的报告与 Grok 复核
- 本文性质：后续工程建议，不修改既有锁定语义，也不表示系统已可生产发布

## 0. 总结判断

当前系统已经具备一条可离线验证、默认 fail-closed 的 Authority 合约主干，并已把 publication、repair、UI 成功语义和对话议程接入真实代码路径。它能正确表达“32 个候选、0 个可构建项目”为有进展但未毕业，也不会把 Runner 返回、repair attempt 结束、manifest 被选中或 HTTP 成功响应当作业务成功。

剩余缺口集中在生产输入和生产信任根，而不是缺一套新的科学规则引擎：真实任务尚缺稳定的 build-ready materialization 流、外部 production signer 与 durable ledger，部分 capability 仍无安全 adapter，完整依赖环境和 live staged E2E 也尚未封板。因此系统现在更接近“能够诚实阻塞并证明为何阻塞”，还不是“在生产环境能从检索一路跑到 dataset builder handoff”。

后续应继续保持目标体感为 Codex 级自主 Agent：LLM 负责对话、搜索与修复策略；Authority Plane 只验证 hard constraints、evidence、package、metric delta 与唯一业务毕业证。

## 1. 现状能力地图

### 1.1 已接线

| 能力 | 当前落点 | 已成立的行为 |
|---|---|---|
| Publication 决策进入 run record | `AgentRunRecord.business_completion`、`openai_agents.py` audit 持久化路径、web summary/API | audit 后调用 `PublicationContractRegistry.evaluate(snapshot)` 并持久化 typed decision；没有合法 authority/inventory 时保持 progress/blocked |
| build-ready 唯一成功门禁 | `publication.py`、`openai_agents.py`、`web/app.py`、frontend | 只有 issued、可验证、`package_kind=build_ready` 且 `success_ui_allowed=true` 的 completion 能形成业务成功；候选数、审查数、Runner 文案和 legacy repair completed 均不能代替它 |
| Authority package 防自证 | `publication.py` + peer-audit/property tests | package、audit、manifest、EvidenceStore、membership、builder、observations 与签发材料需一致；package substitution、伪造 refs、hard constraint 降级和 replay 均 fail-closed |
| Evidence scope | `evidence_store.py` | versioned observations 与 refs 校验已存在；project evidence 无 verified membership 不会自动下沉为 file evidence |
| RepairAuthority 单轨 | `openai_agents.py`、`repair.py`、`capabilities.py` | v1 action 可升级为 v2 `RepairProposal`；经过 LP6 issue policy、metric 白名单、parameter schema、risk/budget admission 后才 dispatch；Runner 返回不授予成功 |
| repair delta 与有界停止 | `repair.py`、run record repair state | Authority capture pre/post，计算 delta；同 signature 无进步默认 2 次停止；idempotency key 在 dispatch 前记录；attempt finished 与 repair succeeded 分离 |
| repair 后重新审计与 publication | `openai_agents.py` | 已形成 proposal → admission → execution → metric capture → re-audit → publication → issued event 的单轨顺序 |
| dev/test publication signer | `publication.py::issue_dev_publication_authority` | 仅显式测试调用或 `DISCOVERY_AUTHORITY_DEV_SIGN=1` 时启用；进程内临时 Ed25519 key 或显式 PEM，不序列化私钥；默认关闭 |
| 对话议程薄委托 | `web/app.py::_discovery_critical_decision_agenda` → `discovery.agenda.agenda_for_manager` | Manager 保持唯一 writer；动态单问题、open=已解决、browse-only 不被训练议程阻塞、chimeric provenance 优先级保持 |
| 离线门禁测试 | peer-audit、publication、repair、evidence、property、replay、wiring、agenda suites | 已覆盖 soft 不变 hard、hard unknown 不 pass、membership 边界、no-progress、issuance/replay、32/0 非成功等核心不变式 |

### 1.2 半接线

| 能力 | 已有部分 | 尚缺部分 / 当前行为 |
|---|---|---|
| 开放 v2 repair proposal | Authority 已支持开放 envelope 和 capability composition | 主循环提案来源仍以 audit 的 v1 `repair_actions` 升级为主；尚未直接消费第一次 Runner structured output 中任意、合法的 v2 proposal。不得为补此缺口恢复“第二 Runner 返回即成功”双轨 |
| capability dispatch | search、inspect、recompute/re-audit、select，以及诚实 stop/ask-user 已能走已有 service | `materialize_evidence`、`refresh_auth_context` 尚无安全 `DiscoveryToolService` adapter，目前明确返回 `registered_adapter_not_wired`，不能静默成功 |
| build-ready typed material | run record 已有 `build_ready_package_material`、authority observations、membership refs 等承载字段；Registry 能验证 canonical package | 真实 manifest、EvidenceStore、file membership、builder entrypoint 尚无完整生产 producer 把这些材料可靠组装并落入 run store；因此大多数真实任务仍会诚实 blocked |
| signer | dev/test signer 可证明完整材料 happy path | 尚无进程外 production signer、稳定公钥配置、key id/rotation/revocation 和运维审计；dev signer 不能作为生产毕业方案 |
| execution/issuance ledger | 单次 run/Authority 实例内已有 idempotency、metric pair、completion nonce 与防重放约束 | 尚需明确跨进程、重启、并发 worker 下的 durable、原子 ledger；仅依赖进程内状态不足以支撑生产 exactly-once 语义 |
| 端到端测试 | 大量离线 contract、negative、property 与 wiring tests 已绿 | 当前机器缺部分依赖，agent-turn/control-plane/web 完整回归未统一补跑；也没有 live repository 到 dataset builder handoff 的 staged E2E |

### 1.3 未接线

- 生产 signer 服务或 KMS/HSM-backed signing flow，以及公钥分发、轮换、吊销和审计流程。
- 从真实 manifest、EvidenceStore、membership、file/assay evidence 与 builder compatibility 生成 canonical `BuildReadyPackage` 的完整生产 materialization pipeline。
- production durable issuance ledger：跨实例 completion token、metric observation pair、attempt nonce、idempotency reservation 的原子持久化与消费。
- `materialize_evidence`、`refresh_auth_context` 的安全 capability adapters 及其权限、预算、超时、重试和审计契约。
- 第一 Runner structured v2 proposal 直接进入同一个 `RepairAuthority` admission/dispatch 流。
- 无 live PRIDE 的 staged integration，以及受控凭据环境下的 live repository → build-ready → dataset builder handoff 全链验证。
- 统一的完整 Python 测试环境；已知缺口包括 `openai-agents`、`typer`、`fastapi` 及其部分传递依赖。

## 2. 后续工程里程碑 M1–M5

### M1 — 完整环境与回归基线封板

**目标**

建立一套可重复、与生产依赖一致的测试环境，把目前因依赖缺失未收集的 agent-turn、control-plane、web 和 agenda 主路径纳入统一门禁。先证明已有接线在完整环境可运行，再扩展生产功能。

**入口**

- peer-audit、property、publication、repair、evidence、Wiring A/Continue、agenda 离线测试已存在。
- 当前 `E:\anaconda\python.exe` 环境已知缺少 `openai-agents`、`typer`、`fastapi`；部分环境还缺 `annotated_doc` / `annotated_types`。

**出口 / 验收**

- 通过仓库声明的依赖方式创建可复现环境，不以临时全局安装作为唯一说明。
- 以下集合在同一环境无 collection error、无新增 xfail/skip：Authority/peer/property、wiring、`test_discovery_agent_turn.py`、`test_discovery_task_build_plan.py`、agenda、control-plane 与 discovery web suites。
- 保存精确 Python/Node 版本、安装命令、测试命令和输出摘要；形成 CI 可复用命令。
- 所有旧成功断言按 build-ready 语义核对，不能为了兼容旧测试绕开 Authority。

**主要文件**

- 依赖声明与 CI 配置（以仓库现有机制为准）
- `tests/test_discovery_agent_turn.py`
- `tests/test_discovery_task_build_plan.py`
- `tests/test_discovery_agenda.py`
- `tests/test_control_plane.py` 及 discovery web suites
- 既有 Authority/property/wiring suites

**主要风险**

- 补依赖后会暴露此前 collection 未到达的 legacy 假成功断言；应修测试或真实契约，不得降低 build-ready 门禁。
- 共享 worktree 有多角色未提交修改，必须按文件所有权小步合并，禁止覆盖既有意图。

### M2 — 生产 BuildReady materialization

**目标**

把真实 run store 中的 manifest、EvidenceStore、membership、file/assay evidence 和 builder compatibility 组装成 canonical `BuildReadyPackage`，使“材料齐全”成为可计算事实，而不是调用方自报 JSON。

**入口**

- `AgentRunRecord` 已有 `build_ready_package_material` 等 typed 字段。
- `PublicationContractRegistry` 已能检查 audit/package/inventory digest、evidence observation、membership 和 hard constraint provenance。
- 默认无合法 authority 时会 fail-closed。

**出口 / 验收**

- 新 materializer 只读取确定性 run state 和受控 evidence store，不读取 Runner 自由文本作为事实。
- 对每个 file 建立可验证 membership edge；project evidence 不无条件复制给 files。
- package canonicalization 覆盖 run、audit、manifest、EvidenceStore、builder entrypoint、file/project/URL/size、constraint evidence 等签名相关材料。
- 缺 file evidence、membership、audit ref、builder compatibility 或 hard constraint evidence 时，输出明确 blockers 和进行中指标，不生成可签发 package。
- 使用 synthetic RT/PSM 完整材料可进入待签发状态；32/0 或材料不全仍为未毕业。

**主要文件**

- `src/agent/discovery/publication.py`
- `src/agent/discovery/evidence_store.py`
- `src/agent/control_plane/models.py`
- `src/agent/control_plane/discovery.py`
- `src/agent/control_plane/openai_agents.py` 的 audit 后 materialization 薄接点
- 对应 fixture、publication/evidence/wiring integration tests

**主要风险**

- 最大风险是把“有 manifest”或“有 project URL”误当作 file/material 完整；必须继续按 evidence scope fail-closed。
- canonicalization 若在 signer 与 verifier 两侧不一致会导致不可诊断的签名失败，需单一 canonical serializer 和 golden tests。
- 不应把具体 proteomics 子领域写成 materializer 分支；差异应由 typed constraints/evidence 表达。

### M3 — Capability adapters 与开放 v2 proposal 闭环

**目标**

让 Agent 可以在预算内提出新的、未被业务 kind 穷举的修复意图，同时所有副作用仍映射到注册 capability primitives，并由 Authority admission、metric delta 和 no-progress 上限约束。

**入口**

- v1 → v2 upgrader、LP6 issue policy、metric 白名单、parameter schemas、risk/budget review 和现有 dispatch 主循环已接通。
- `materialize_evidence`、`refresh_auth_context` 当前明确 not wired。

**出口 / 验收**

- 第一次 Runner 的 structured output 可携带 `RepairProposal`，并进入与 v1 upgrader 完全相同的 `RepairAuthority.review_proposal` 路径。
- 为 `materialize_evidence`、`refresh_auth_context` 提供最小安全 adapters：明确输入 schema、允许的数据源、权限、预算、超时、重试、审计事件和幂等键。
- 未知 capability、缺 issue context、不可计算 metric、越权参数或 stale/伪造 pre/post 均 reject/degrade；不得执行 shell 或任意代码/URL。
- 两次同 signature 无进步后停止，展示 limitation/blocker；正 delta 只表示 repair progressed，仍须重新审计和 issued build-ready completion 才成功。
- 删除任何残余第二 Runner success 解释，保证单轨。

**主要文件**

- `src/agent/control_plane/openai_agents.py`
- `src/agent/control_plane/repair.py`
- `src/agent/control_plane/capabilities.py`
- `src/agent/control_plane/discovery.py` / `DiscoveryToolService` adapters
- `src/agent/control_plane/models.py` 的 structured proposal/run state
- repair、wiring、agent-turn integration tests

**主要风险**

- “开放 proposal”若被误实现成任意副作用执行，会绕过 Authority；开放的是 intent，不是执行权限。
- refresh auth 涉及真实凭据边界，必须只刷新受控 grant/context handle，不允许模型读取或回显 secrets。
- adapter 执行与 idempotency reservation 必须原子排序，避免先执行后记账。

### M4 — Production signer 与 durable Authority ledger

**目标**

建立生产信任根和跨进程防重放语义，让 M2 产生的 package 可被独立 Authority 签发，并让 completion、metric pair、attempt nonce 与 idempotency 在重启和多 worker 下仍可信。

**入口**

- dev signer 已证明签名/验证与完整材料 happy path，但默认关闭且进程重启后失效。
- peer audit 已定义 package substitution、token replay、copied authority id、metric replay 等负例。

**出口 / 验收**

- 私钥仅存在于进程外 signer、KMS 或等价秘密管理边界；应用侧只持公钥/key id 和 opaque signed inventory。
- 定义 key id、active/retired/revoked 状态、轮换窗口、公钥发布和审计日志；旧签名验证策略明确。
- durable ledger 对 issuance、approved attempt、metric pair、completion nonce、idempotency reservation 提供原子写入与一次性消费。
- token 绑定 run/audit/package/attempt/recipient；跨实例复制、重放、并发重复消费均不能再次发出 progressed/success。
- signer/ledger 不可用时任务保持 progress/blocked，并给出可解释 limitation；不得降级为 dev sign 或调用方自证。

**主要文件**

- 新的 signer client / authority repository seam（具体模块在设计评审时确定）
- `src/agent/discovery/publication.py` verifier 接口
- `src/agent/control_plane/repair.py` durable ledger 接口
- `src/agent/control_plane/store.py` 或专用 durable store adapter
- deployment/secret/config 文档与集成测试

**主要风险**

- 私钥误入 `.env`、fixture、日志或 run store；这是发布阻断级风险。
- 多 worker 下 check-then-write 竞态会绕过 idempotency/nonce 一次性消费，必须由存储层原子保证。
- key rotation 设计不完整会让合法历史 run 无法验证或让已吊销 key 继续毕业。

### M5 — 分阶段真实 E2E 与发布门

**目标**

在不削弱离线门禁的前提下，逐级证明真实任务可从 repository search/inspect 经 repair、materialization、signing 走到 dataset builder handoff；并验证中间进展仍不会假成功。

**入口**

- M1 完整环境回归通过。
- M2 materializer、M3 adapters、M4 production signer/ledger 均有独立 integration tests。

**出口 / 验收**

- Stage 1：完全离线 synthetic E2E，完整 RT/PSM 材料可获得 issued build-ready completion；缺 file/assay evidence 的对照保持 blocked。
- Stage 2：sandbox/fake repository 与 fake signer/ledger E2E，覆盖 stale context、refresh、no-progress=2、重试和并发 replay。
- Stage 3：受控 live repository 凭据下只做 search/inspect/materialize，不自动发布；确认 provenance 与预算事件。
- Stage 4：经人工批准的 production-like signer + builder dry-run，验证 package 确实能被 dataset builder 接收。
- 32/0 场景始终显示候选/审查进展和 blockers，但不出现业务完成/修复成功绿勾。
- 发布门同时要求：完整回归绿、Authority negative/property 绿、无 secrets、可观测事件一致、rollback/runbook 完整。

**主要文件**

- `tests/fixtures/discovery/` 与新的 staged integration/E2E tests
- repository/service adapters
- publication materializer、signer client、durable ledger
- dataset builder dry-run adapter/contract
- CI/staging 配置、runbook 与验收报告

**主要风险**

- live 数据和外部服务具有漂移、限流、授权过期与网络不稳定；live 测试不得替代 deterministic offline gates。
- “builder 接受”必须是明确 contract/dry-run 结果，不能由 Agent 文案或 HTTP 200 推断。
- staged E2E 可能触及成本与外部副作用，必须有预算、只读/写入边界和人工批准。

## 3. 与 `WIRING_CHECKLIST.md` 剩余项对齐

`WIRING_CHECKLIST.md` 写于 Wave 6 后，其尾部 `DOCUMENTED_NOT_WIRED` 是当时快照。Wiring A、Wiring Continue 与 Wave B 已完成其中一部分，因此应按下表理解，而不是重新实现整张清单。

| Checklist 项 | 当前状态 | 后续里程碑 |
|---|---|---|
| Publication 1：从 run/audit/manifest/evidence/membership/builder 状态组 snapshot | **半完成**：audit、counts、constraints 和 typed material 字段已接；真实 material producer 不完整 | M2 |
| Publication 2：进程外 signer、产品只验 opaque signature | **未完成**：仅有默认关闭的 dev signer | M4 |
| Publication 3：canonical package 与 signed digest 一致 | **验证器已完成，生产输入半完成**：substitution 负例已绿；真实 package producer 待补 | M2 + M4 |
| Publication 4：evaluate 并持久化 typed decision | **已完成** | M1 回归守护 |
| Publication 5：Runner 不得驱动成功 | **已完成** | M1/M5 回归守护 |
| Publication 6：API 输出 `record.business_completion` | **已完成** | M1 web 回归守护 |
| Repair 1：Authority-owned issue context | **已接**；真实全链 provenance 继续受 M2/M5 验证 | M1 + M5 |
| Repair 2：开放 proposal 统一 admission | **半完成**：v1 upgrader 已走 Authority；第一次 Runner 原生 v2 intake 待补 | M3 |
| Repair 3：dispatch 前 reserve idempotency | **进程/run 内已接，生产 durable/atomic 待补** | M4 |
| Repair 4：Authority metric pre capture | **已接**；需在真实 material/state reader 上做 integration | M3 + M5 |
| Repair 5：仅 registry-approved adapters | **半完成**：基础 adapters 已接；materialize/refresh 待补 | M3 |
| Repair 6：post capture、delta、pair consume | **实例内已接，跨实例 durable 待补** | M4 |
| Repair 7：no-progress=2、delta 不等于成功 | **已完成** | M1/M5 回归守护 |
| Repair 8：re-audit、completion context、publication | **已完成最小单轨** | M2/M4/M5 以真实材料验证 |
| Repair 9：issued completion、replay 防护 | **实例内与离线负例已完成，生产 durable 待补** | M4 |
| 前置：secrets 不入库、无主题字符串分支、soft/hard/evidence scope、one-writer | **现有门禁已覆盖** | 所有里程碑的持续退出条件 |

## 4. 建议优先级：对“可真跑”最关键的前三件事

### P0-1：完整依赖环境和统一回归命令

这是最先做的工程门。当前已有代码和测试不能在同一完整环境全部收集，继续扩展会放大“局部绿、整体未知”。先完成 M1，才能可信判断后续接线是否破坏 agent-turn、web、Authority 或 agenda。

### P0-2：真实 `BuildReadyPackage` materialization + builder-entry 校验

这是从“能诚实 blocked”走向“有可能毕业”的关键数据链。没有它，真实任务即使搜到正确材料，也无法把 evidence、membership、manifest 和 builder compatibility 变成 Authority 可签发的 canonical package。

### P0-3：production signer + durable ledger

这是生产成功事件的信任根。没有外部 signer 和跨进程 ledger，要么系统永远 fail-closed，要么只能依赖 dev signer/进程内状态，而后者明确不能用于生产毕业。

M3 capability adapter 与原生 v2 proposal 应紧随前三项推进；它显著提升自主修复能力，但不能替代 package 事实和生产签发。建议在 M2 接口稳定后与 M4 的基础设施工作并行开发，在 M5 前共同封板。

## 5. 明确非目标

- 不在本路线图中执行 dataset build、模型训练或发布数据集；只验证材料已达到可进入 builder 的 build-ready 合约。
- 不恢复 Q1–Q10 固定问卷，不把 Dialogue Manager 改成规则问答机，不破坏 Manager one-writer。
- 不让 Authority Plane 决定科学探索路线；它只验证毕业、证据、hard constraints、能力边界和 metric delta。
- 不为 32/0、immunopeptidomics、特定 accession 或任何单一项目写案例特判。
- 不把 soft preference 自动提升为 hard constraint；不把 hard unknown、缺证据或缺 membership 当作 pass。
- 不把 Runner 返回、第二次 Runner、repair attempt finished、manifest selected、候选数、HTTP 200 或 dev signature 当作生产成功。
- 不允许开放 `RepairProposal` 变成任意 shell、任意代码、任意 URL 或未注册副作用执行。
- 不为追求绿测削弱 peer-audit/property/replay tests，不以 xfail/skip 隐藏行为错误或依赖缺失。
- 不大改 frontend 或重写 agenda 树；UI 继续只消费 Authority 决策，agenda 继续薄委托现有 domain API。
- 不把真实私钥、repository credentials、grant token、`.env` 或 dialogue DB 写入仓库、fixture、报告或日志。

## 6. 依赖与运行条件预估

### 6.1 环境

- Python 环境需按仓库声明补齐 `openai-agents`、`typer`、`fastapi` 及其兼容传递依赖；先锁定版本，再形成 CI/开发统一安装方式。
- 保留 `E:\anaconda\python.exe` 现有离线回归作为一个可复现基线，但不要假设它已经覆盖 agent-turn/web collection。
- Node/frontend 环境需保留现有 TypeScript、component tests 和 Vite production build 门禁；本路线图没有 UI 大改需求。
- M5 live stage 需要明确网络出口、repository rate limit、超时、预算和只读凭据；离线 gate 必须始终能在无网络环境独立运行。
- durable ledger 需要支持原子 compare-and-set/unique constraint/transaction 的持久化后端；具体选型应服从现有部署栈，而不是把进程内 dict 包装成“持久化”。

### 6.2 密钥与凭据

- production signing private key 必须位于外部 signer、KMS/HSM 或等价 secret boundary；应用仓库和 run store 不保存私钥。
- 应用只配置 verifier 所需的 public key/key id/trust metadata，并有 active、retired、revoked 和 rotation 策略。
- repository/search grant 使用受控 secret store 与 opaque handle；Agent、事件和 UI 不得获得原始 secret。
- 测试只使用临时生成 key 或明确的非生产 fixture public material，不复用生产 key。

### 6.3 `DISCOVERY_AUTHORITY_DEV_SIGN` 开关

- 默认必须 unset/false；默认无 signer 即 progress/blocked 是正确行为。
- 仅本地开发或隔离测试可设 `DISCOVERY_AUTHORITY_DEV_SIGN=1`，且仍要求完整 package、observations、verified membership 和匹配 audit refs。
- 该开关不能出现在生产部署默认配置，不能作为 readiness probe 的成功条件，也不能用来通过 production E2E。
- 当前进程内 dev key 重启后不可验证属于预期 fail-closed 语义；不要通过持久化 dev private key 修复它。
- 若使用显式 `DISCOVERY_AUTHORITY_SIGNING_KEY` PEM，仅限受控开发/测试 secret injection；不得写进 git、`.env`、fixture、报告或日志。

## 7. 用户可选试跑清单（非强制）

以下试跑只用于增加信心，不要求用户亲自执行，也不替代 Grok/CI 验收。执行者可按环境选择：

1. **Authority 离线回归**：运行 peer-audit、publication、repair、evidence、property 与 wiring tests，确认 package substitution、replay、hard unknown、membership 和 no-progress 负例仍绿。
2. **完整依赖回归**：在补齐依赖的环境运行 `tests/test_discovery_agent_turn.py tests/test_discovery_task_build_plan.py tests/test_discovery_agenda.py`，再运行 control-plane 与 discovery web suites，确认无 collection error。
3. **默认无签名试跑**：保持 `DISCOVERY_AUTHORITY_DEV_SIGN` 未设置，用完整/不完整 fixture 各跑一次；两者都不应凭自报材料毕业，UI 应显示进度或 blocker。
4. **隔离 dev-sign happy path**：仅在临时测试进程显式开启 dev sign，以 synthetic RT/PSM 完整材料验证 issued build-ready；随后去掉开关，确认同路径重新 fail-closed。
5. **32/0 事故回放**：确认 candidate/judgment 指标可见、`build_ready=0`、`succeeded=false`、`success_ui_allowed=false`，且 repair attempt finished 不产生绿勾。
6. **staged adapter 试跑**：M3 完成后使用 fake service 验证 refresh/materialize 的预算、参数 schema、idempotency 和 no-progress=2；不接 live credentials。
7. **builder dry-run**：M2/M4 完成后，让 builder 只验证 package contract，不创建数据集；只有 builder-entry compatible 且签名/ledger 有效才通过。

## 8. 工程推进约束

- 每个里程碑都应先增加或锁定离线负例，再做最小接线，并复跑 peer-audit/property/wiring 基线。
- 任何完成事件必须能追溯到当前 run 的 issued `BusinessCompletionDecision`；无法证明时宁可诚实 blocked。
- 共享 worktree 的修改需按文件所有权协调；禁止 reset/clean，禁止覆盖其它角色未提交意图。
- M2–M4 的接口设计应优先保持 deep module seam：Agent 提 intent，adapter 执行受控 primitive，Authority 验证事实和毕业；不要把三层逻辑堆回 `openai_agents.py` 或 `app.py`。
- 每个阶段报告分别说明“已接线”“仍 blocked”“环境未覆盖”和“是否触碰生产 secrets”，不得自评 merge-ready。

STATUS: READY
