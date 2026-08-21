# Discovery Agent 多智能体会议共识计划

元信息：

- 会议角色：`@lead` 主席/书记，`@ui`、`@agenda`、`@audit` 委员
- 日期：2026-07-22
- 决策依据：`LOCKED_PLAN.md`、Waves 1–6、Wiring A/Continue、Wave B、四份 `COMMITTEE_*` 分册及 `multi-codex` R1/R2
- 架构：方案 2——灵活智能层 + 薄 Authority Plane
- 唯一业务毕业：材料符合科学要求且能进入 dataset build，即 **build-ready**
- 当前判断：合约与测试门禁可用；产品正式可用、生产上线和 merge-ready 均尚未成立

## 1. 共识结论

1. 现有系统已经接通 publication → run record、RepairAuthority 单轨、UI 成功门禁和 TaskProfile agenda；它能诚实表达进展、阻塞和失败原因，并能防止多类假成功。
2. 候选、审查、judgment-qualified、正 repair delta、Runner 返回、HTTP 200、manifest selected、agenda resolved、strategy confirmed 和 attempt finished 都只是中间状态；它们不能签发业务成功。
3. 业务成功必须同时满足：hard constraints fail-closed 通过、关键 claim 有可信 evidence、canonical package 达到 builder 入口、生产 Authority issuance 可验证，且不是仅候选列表。
4. 当前主要缺口是生产事实链和生产信任根：完整依赖环境、真实 `BuildReadyPackage` materialization、production signer、durable ledger、安全 capability adapters 和 staged E2E。
5. `blocked_with_progress` 不是系统崩溃，也不是业务成功。若系统正确保留进展、列出 blocker 且无假绿，它可算负向“机制试跑通过”；该业务任务和产品正式可用状态仍为 NO-GO。
6. 用户手动试跑始终可选。用户不试不影响本计划有效性，也不要求用户替项目承担验收；相同清单应优先固化为自动化测试。

## 2. 当前真实状态

### 2.1 已接线

- `AgentRunRecord.business_completion` typed 持久化及 API/UI 投影。
- `PublicationContractRegistry` 对 audit、package、manifest、EvidenceStore、membership、builder、hard constraints 和 issuance 的 fail-closed 判定。
- v1 repair action → v2 proposal → LP6 admission → registry-approved dispatch → Authority pre/post → delta → re-audit → publication 的单轨主循环。
- no-progress 默认 2 次停止；idempotency、metric pair、completion nonce 与 replay 负例已有离线门禁。
- dev/test signer 默认关闭；完整材料且显式开启时可验证机制。
- `agenda_for_manager(...)` 已进入对话主路径，保持 Manager one-writer、动态单问题、open 已解决、browse-only 分流及 chimeric feasibility 优先。
- UI 只允许 issued build-ready completion 触发成功；legacy/unknown repair event 和 32/0 不得画绿。

### 2.2 半接线

- Repair proposal 主要由 v1 audit action 升级；第一次 Runner 的原生 structured v2 proposal 尚未完整进入同一 Authority admission。
- search、inspect、recompute/select 等已有受控 service 路径；`materialize_evidence`、`refresh_auth_context` 尚无安全 adapter，当前必须明确 `registered_adapter_not_wired`。
- run record 有 package/evidence/membership typed 字段，Registry 能验证，但缺从真实 store 生成 canonical package 的生产 materializer。
- 实例/run 内防重放已验证；跨进程、重启、多 worker 的 durable/atomic ledger 尚未完成。

### 2.3 未接线或未封板

- 进程外 production signer、KMS/HSM 或等价私钥边界，以及 key id、轮换、吊销、审计和公钥配置。
- 真实 manifest/EvidenceStore/membership/file-assay evidence/builder compatibility → canonical `BuildReadyPackage` 的完整链。
- 完整依赖环境回归；已知缺口包括 `openai-agents`、`typer`、`fastapi` 及部分传递依赖。
- 受控 repository → repair → materialize → sign → builder preflight 的 staged E2E。

## 3. 争议点与主席裁决

### 3.1 “诚实 blocked”算不算试跑通过

裁决：必须区分两个层次。

- 系统机制试跑：如果预期缺 signer/证据/adapter 或 no-progress，系统按合约阻塞、保留 artifacts、列出机器可读 limitation 且无绿勾，可判“该负向机制场景通过”。
- 业务任务毕业：仍失败；只有 issued build-ready completion 才通过。
- 产品正式可用：仍 NO-GO，直至生产 MUST 与正向 builder preflight 完成。

### 3.2 dev signer 能否作为正向验收

裁决：dev signer 只能证明 issuance/verification 机制。`DISCOVERY_AUTHORITY_DEV_SIGN=1` 不得作为 production GO、业务正式毕业或上线证据；生产必须使用进程外 signer 和 durable ledger。

### 3.3 优先做开放 Agent 还是生产材料链

裁决：保持 Agent 灵活，但“可真跑”的前三优先级为完整环境、真实 materialization、production signer/ledger。开放 v2 intake 与 materialize/refresh adapters 紧随其后；开放的是 intent，不是任意副作用权限。

### 3.4 UI 和议程的中间状态语言

统一为：

| 状态 | 用户语言 | 颜色/语义 |
|---|---|---|
| agenda 清空 | 关键决策已齐，可确认策略 | 中性，不是完成 |
| strategy confirmed | 策略已确认，准备/正在执行 | 中性，不是交付 |
| candidates found/reviewed | 已有候选进展，尚未 build-ready | 中性进展 |
| blocked_with_progress | 有进展但受阻，并列出 blocker | 非绿；不笼统等同程序崩溃 |
| issued build-ready | 可进入数据集构建 | 唯一允许成功绿的状态 |

## 4. 三层 staged 出口

### L1：离线合约出口

必须同时满足：

- peer-audit、Authority、publication、repair、evidence、property、replay、wiring、agenda 与 frontend 门禁全绿，无为消红新增的 xfail/skip。
- 32/0、hard unknown/conflict、project evidence 无 membership、package substitution、metric/completion replay、no-progress=2 均保持 fail-closed。
- Runner/HTTP/legacy event/candidate count 不得触发成功。
- dev-sign happy path仅标为机制验证。

L1 通过只能说“离线合约门禁可用”，不能说产品正式可用。

### L2：完整依赖与跨层出口

必须同时满足：

- 在同一受支持环境安装并锁定 `openai-agents`、`typer`、`fastapi`、web/runtime 与前端依赖。
- agent-turn、task-build-plan、agenda、control-plane、web discovery、frontend、Authority/Wiring 全部可收集并通过。
- Docker/Vite/API 的 build/version 可核对，避免旧静态 bundle 造成假结果。
- 浏览器 A/B/C 场景、确认边界、`record.business_completion`、桌面/移动与刷新恢复一致。
- Manager one-writer、每轮一个 `next_decision`、numeric option、consultation、open 和 browse-only 不回归。

L2 通过只能说“完整开发环境跨层可运行”，仍不等于生产正式可用。

### L3：受控凭据与生产基础设施出口

必须同时满足：

- 真实 repository → EvidenceStore/membership → canonical package → 进程外 signer + durable ledger → builder preflight 形成闭环。
- Authority metric reader 从确定性 typed store capture，dispatcher 在副作用前原子 reserve idempotency。
- 所有宣称支持的 capability 均有安全 adapter；未支持的能力明确 unavailable。
- 正向试跑留下 `run_id`、`audit_ref`、canonical `package_digest`、`key_id`、builder preflight result、版本信息；不保存私钥。
- 至少通过一轮完整负向矩阵与一轮 production-equivalent 正向 builder preflight。

只有 L1–L3 均满足，才可进入 production GO 评审；仍不得由单个测试或单个委员自行宣称正式可用。

## 5. 工程里程碑 M1–M5

### M1 — 完整环境与统一回归

**目标：** 消除 collection 空档，建立单一可复现测试基线。

**入口：** L1 离线 suites 已存在；当前部分解释器缺依赖。

**工作：** 固化 Python/Node 版本与安装方式；补齐依赖；运行 Authority、agent-turn、task-build-plan、agenda、control-plane、web、frontend 和 build；记录精确命令与结果。

**出口：** 达到 L2 的依赖/收集/版本门禁，无新增掩盖性 skip；legacy 假成功断言按 build-ready 修正而非绕过 Authority。

**主要文件：** 依赖/CI 配置、相关 test suites；不需要大改产品路径。

**风险：** 完整 collection 可能暴露隐藏回归；共享脏 worktree 不得 reset/clean 或覆盖其他角色修改。

### M2 — 真实 BuildReady materialization

**目标：** 从确定性 run state 生成可签发 canonical package。

**入口：** typed run fields 与 Registry verifier 已存在。

**工作：** 汇聚 manifest、EvidenceStore、file membership、project/file/assay evidence、URL/size/role、hard constraint observations 和 builder compatibility；使用单一 canonical serializer。

**出口：** 完整 synthetic 材料可进入“待生产签发”；缺 membership/file evidence/audit/builder ref 时输出 blockers，不生成可签 package；32/0 继续未毕业。

**主要文件：** `publication.py`、`evidence_store.py`、control-plane models/discovery/openai_agents 的薄接点及 integration tests。

**风险：** project evidence 被错误下沉、调用方 JSON 自证、signer/verifier canonicalization 漂移、领域字符串特判。

### M3 — 开放 v2 proposal 与安全 capability adapters

**目标：** 提高 Agent 自主修复能力，同时保持 Authority 控制副作用和 delta。

**入口：** v1 upgrader、LP6 admission、metric whitelist、no-progress 和现有 dispatcher 已接。

**工作：** 第一次 Runner structured v2 proposal 进入同一 admission；实现 `materialize_evidence`、`refresh_auth_context` 安全 adapters，声明 schema、权限、预算、风险、超时、重试、回滚和 idempotency。

**出口：** 未知/越权/缺 issue context/不可计算 metric 均 reject/degrade；两次无进步停止；正 delta 仅为进展；不执行任意 shell、代码或 URL。

**主要文件：** `openai_agents.py`、`repair.py`、`capabilities.py`、`DiscoveryToolService` adapters、models 和 tests。

**风险：** 把开放 intent 误成自由执行；refresh 泄露 grant/secret；先执行后记账；恢复第二 Runner success 双轨。

### M4 — Production signer 与 durable Authority ledger

**目标：** 建立生产信任根和跨实例 exactly-once 防重放。

**入口：** dev signer 与 peer-audit 负例已验证机制。

**工作：** 进程外 signer/KMS；公钥/key id/轮换/吊销/审计；原子持久化 issuance、attempt、metric pair、completion nonce、idempotency reservation。

**出口：** token 绑定 run/audit/package/attempt/recipient；重启、并发、跨实例和二次消费均安全；signer/ledger 不可用时 fail-closed，绝不降级到 dev sign。

**主要文件：** signer client/authority repository seam、publication verifier、repair ledger interface、durable store adapter、部署和 secret 文档。

**风险：** 私钥入库/日志、check-then-write 竞态、轮换导致历史签名不可验证或吊销 key 继续有效。

### M5 — Staged E2E 与 production GO 评审

**目标：** 证明真实任务可走到 builder handoff，并验证所有负向场景无假成功。

**入口：** M1–M4 各自退出门禁通过。

**工作：** 依次运行离线 synthetic、sandbox repository/fake infrastructure、受控只读 live repository、production-equivalent signer + builder dry-run；执行可选 UI/agenda 场景和审计矩阵。

**出口：** L1–L3 全部通过；一轮正向 build-ready + builder preflight 和完整负向矩阵有脱敏证据；run record/API/UI 一致；rollback/runbook/预算/权限就绪。

**主要文件：** staged integration/E2E tests、repository adapters、builder dry-run contract、CI/staging 配置和运行手册。

**风险：** live 漂移、限流、费用和副作用；HTTP 200 冒充 builder 接受；把单次试跑成功宣传为产品正式可用。

## 6. 可选试跑清单

以下均由用户自选，可以全部不做。建议项目方将其自动化，用户无需替项目点页面。

### 6.1 启动前

- [ ] 确认当前工作树、后端 health、前端 build/version；避免旧 clone/静态 bundle。
- [ ] 使用隔离 workspace、最小权限凭据、明确预算和可清理输出目录。
- [ ] 默认关闭 `DISCOVERY_AUTHORITY_DEV_SIGN`；不得把 key/secret/dialogue DB 写入截图、响应或日志。
- [ ] 同时观察对话区、策略卡、运行卡/审计入口和 Network 的 `record.business_completion`。

### 6.2 A：browse-only

- [ ] 输入“只浏览公共蛋白质组数据，先找候选，不做训练，也不要宣称可构建”。
- [ ] 确认不出现训练 labeling/acquisition 问卷，确认前不启动 discovery。
- [ ] 候选增长可显示进展，但 `build_ready=0`、`succeeded=false`、无绿勾。

### 6.3 B：材料不齐 / 32/0

- [ ] 请求候选、审查、file/assay evidence 和可构建清单，但保持 production signer 缺失或材料故意不全。
- [ ] 确认 searched/inspected/judgment 可见，build-ready 为 0，状态为 progress/`blocked_with_progress`。
- [ ] blockers 与 audit/limitations 一致；legacy repair completed 只显示 attempt finished。

### 6.4 C：议程

- [ ] browse-only：不被训练议程阻塞。
- [ ] chimeric：label provenance/relabel tolerance 先于 optional labeling。
- [ ] 显式 open RT：open 被视为已解决，不重复追问。
- [ ] 每轮只有一个 `next_decision`；咨询不被当 commitment；agenda 清空只进入待确认。

### 6.5 D：Authority 负向矩阵

- [ ] 缺 signer：完整 material 仍 fail-closed。
- [ ] unwired capability：明确 `registered_adapter_not_wired`，不静默成功。
- [ ] no-progress：同 signature 两次零 delta 后停止并给 limitation。
- [ ] replay：旧 package、metric pair、completion 或 copied authority id 均拒绝。
- [ ] hard unknown/conflict、duplicate hard、overflow、membership 缺失均不得毕业。

### 6.6 E：正向 production-equivalent

- [ ] 使用进程外 signer、durable ledger、完整 evidence/membership 和生产同版本 builder preflight。
- [ ] 验证 issued `BusinessCompletionDecision` 的 `succeeded=true`、`package_kind=build_ready`、`success_ui_allowed=true`，且 build-ready projects/files 非零。
- [ ] 保存脱敏的 run id、audit ref、package digest、key id、builder preflight、代码/依赖版本。
- [ ] API/run record/UI 三处一致后，才判该业务任务试跑毕业。

## 7. NO-GO 红线

- `build_ready=0`、audit 非 ready、package 未签发或 builder 未验证时出现 completed/绿勾。
- 用 Runner、HTTP 200、candidate/review/judgment 数、positive delta、legacy/unknown event 或 agenda 状态代替成功。
- 生产启用 dev signer，或私钥/真实凭据进入仓库、fixture、prompt、日志、普通 `.env` 或 UI。
- 跳过 canonical digest、接受 unsigned/self-certified inventory，或允许 package/metric/completion replay。
- 把 hard unknown 当 pass、soft 变 hard、project evidence 无 membership 下沉 file。
- dispatcher 执行未注册 capability、任意 shell/代码/URL，或先执行后 reserve idempotency。
- 为单一科学主题、accession、候选数量或事故写业务特判。
- 删除、xfail、放宽 peer-audit/property tests 来恢复绿色结果。
- 恢复第二 Runner 返回即 repair/business success。
- 把一次受控试跑、L1/L2 通过或机制 blocked 正常误报为产品正式可用。

任一红线触发即 NO-GO；正确处置是回滚成功宣称、保留证据并修复事实链，不能削弱 Authority。

## 8. 用户可选动作

用户可任选其一，不选择也完全可以：

1. **只审文档：** 以本计划作为后续开发和 Grok 验收基线，不进行手工试跑。
2. **只跑离线门禁：** 验证 L1，不接网络、模型、live repository 或真实 key。
3. **跑 UI/Agenda A–C：** 验证进度语言、确认边界和动态单问题；该结果不构成生产 GO。
4. **跑受控负向矩阵：** 验证系统在缺 signer/证据/adapter/no-progress/replay 下诚实阻塞。
5. **待 M1–M4 完成后跑正向 E2E：** 使用 production-equivalent signer/ledger 和 builder preflight，作为 L3/GO 评审证据。

## 9. 会议决议与执行顺序

- 工程顺序锁定为 M1 → M2；M3 可在 M2 接口稳定后与 M4 基础设施并行；M5 必须等待 M1–M4 出口成立。
- 对“可真跑”最关键前三项：完整依赖回归、真实 materialization、production signer + durable ledger。
- 每个里程碑都必须保留 build-ready-only、hard fail-closed、soft 不升级、one-writer、no-progress=2 和 issuance/replay 门禁。
- 各里程碑报告必须区分“已接”“半接”“仍 blocked”“测试未覆盖”，不得自称 merge-ready 或正式可用。

MEETING_STATUS: READY
