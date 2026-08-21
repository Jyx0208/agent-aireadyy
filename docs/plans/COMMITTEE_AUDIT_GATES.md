---
document: COMMITTEE_AUDIT_GATES
role: "@audit 风险与门禁"
date: 2026-07-22
authority: docs/plans/LOCKED_PLAN.md
business_completion: build-ready only
status: READY
---

# 委员会分册：风险、门禁与试跑验收

## 0. 当前结论

当前状态是：**测试门禁可用，Authority/Wiring 合约已通过；产品正式可用尚未成立。**

已确认的门禁基础包括：Wave 2–3 peer audit PASS、focused 57 passed、扩展 196 passed；Wiring Continue 经编排复跑 74 passed；Wave B 议程接线 PASS。它们证明 build-ready-only、issuance、防 replay、hard fail-closed、no-progress=2、Runner≠success 等契约可执行。

尚未闭合的正式运行条件包括：生产 signer 运维部署、durable key/public-key/ledger 配置、完整依赖环境回归、`materialize_evidence` / `refresh_auth_context` 安全 adapter、以及带真实外部系统的端到端试跑。因此当前不得把“离线测试全绿”宣传成“生产已可正式交付”。

## 1. 「测试门禁可用」vs「产品正式可用」

| 维度 | 测试门禁可用 | 产品正式可用 | 当前判断 |
| --- | --- | --- | --- |
| 成功定义 | 测试已固化：只有 issued build-ready completion 才能成功 | 真实 run 必须产出可被 dataset builder 接受的 package | 门禁 PASS；真跑待验 |
| Publication issuance | 离线 signed inventory、canonical package digest、公钥验签、替换/篡改负例已覆盖 | 需进程外生产 signer、私钥托管、轮换、审计与 durable public-key 配置 | 尚未正式可用 |
| Repair issuance | metric reader seam、token ledger、completion nonce、防 replay、idempotency、no-progress 已覆盖 | metric reader 必须读真实 Authority store；dispatcher 必须原子 reserve；ledger 必须跨进程可靠 | 接线已具骨架，运维待验 |
| Repair capabilities | 已注册能力、参数 schema、risk/budget 与 fail-closed 测试可用 | 所有宣称支持的 capability 必须有安全 adapter；当前两个 adapter 明确未接 | 部分可用 |
| Run record / API / UI | `business_completion` typed 持久化与诚实 UI 路径已有接线/回归 | 必须在完整 web 环境验证真实 API、历史回放、重启与前端展示 | 待完整环境与真跑 |
| Dialogue / agenda | profile-driven 议程、单问题、open/browse-only、无领域特判已有测试 | 需在安装 `openai-agents` 的完整环境跑真实 Manager turn | 待完整环境 |
| 环境与依赖 | 当前离线 Anaconda 环境可跑核心 Authority/Wiring suites | `openai-agents`、`typer`、`fastapi`、web/runtime 依赖与受支持 Python 环境必须完整 | 未满足 |
| 外部系统 | 离线 fixture、无网络、无真实密钥可稳定回归 | 模型、repository、下载、builder、凭据、限流与超时必须经受控试跑 | 未验证 |
| 结论授权 | 可说“合约测试门禁 PASS” | 只有完成下述 MUST 清单和试跑验收后，才可说“产品正式可用” | 当前禁止正式可用宣称 |

## 2. 上线 / 真跑前 MUST 检查清单

以下是上线或受控真跑的硬前置。任一项缺失即 **NO-GO**；不得靠放宽 Authority gate 恢复成功。

### 2.1 环境 MUST

- [ ] 使用项目声明的受支持 Python/依赖锁定环境；确认 `openai-agents`、`typer`、`fastapi` 及 web/runtime 依赖可导入。
- [ ] 在该完整环境补跑此前因依赖缺失未收集的 `tests/test_control_plane.py`、web discovery suites、`tests/test_discovery_agent_turn.py`、`tests/test_discovery_task_build_plan.py` 与 `tests/test_discovery_agenda.py`。
- [ ] 核对模型、repository、下载与 builder 的凭据、endpoint、权限、配额、限流、timeout 和重试策略；凭据不得写入仓库、fixture、日志或 run summary。
- [ ] 使用隔离的试跑 workspace、最小权限账号、可清理输出目录与明确预算；禁止直接对生产数据做首次试跑。
- [ ] 启用结构化日志与 run id，能追踪 audit ref、package digest、issue set、capability、idempotency key、pre/post observation 和 terminal event；日志必须脱敏。
- [ ] 验证进程重启、run-store round-trip、历史回放和失败恢复。临时 dev signature 重启失效必须表现为 fail-closed，而不是静默重新签发成功。

### 2.2 Signer / issuance MUST

- [ ] 生产使用进程外 Authority signer；私钥不得进入应用仓库、测试 fixture、前端、Runner prompt、普通环境文件或日志。
- [ ] **生产禁止启用** `DISCOVERY_AUTHORITY_DEV_SIGN=1`；dev/test 临时 signer 不能作为正式毕业方案。
- [ ] 配置 durable public key / key id、轮换、吊销、审计与旧签名验证策略；上线前做一次轮换演练。
- [ ] signer 必须签发完整 canonical Authority inventory，且覆盖 `authorized_package_digest`；Registry 必须重新计算完整 package material 并比对。
- [ ] run/audit/manifest/EvidenceStore/builder/membership/observation 任一引用不匹配时必须 blocked；不得只验证字符串非空。
- [ ] completion 必须绑定当前 RepairAuthority、attempt id、私有 nonce，并一次性消费；旧 run、新 Authority、篡改、复制 public id 或二次 replay 均不得成功。
- [ ] metric observation 必须由 Authority metric reader 从真实 typed store capture；pre/post token 必须绑定同 scope/schema 并在结算后消费。

### 2.3 Peer-audit 与回归 MUST

- [ ] 以下核心命令必须无 xfail/skip 全绿：

  ```powershell
  & 'E:\anaconda\python.exe' -m pytest -q `
    tests/test_discovery_authority_peer_audit.py `
    tests/test_discovery_publication_contracts.py `
    tests/test_discovery_repair_controller.py `
    tests/test_discovery_evidence_store.py
  ```

  当前已知基线：`57 passed`。

- [ ] 扩展 Authority/constraint/audit/mixed/SDRF 集合全绿；当前已知基线：`196 passed`。
- [ ] Wiring 回归必须覆盖 publication→record、repair authority、dev signer default-off、legacy event、32/0、package substitution、replay 与 no-progress。
- [ ] 前端相关 suite、TypeScript 检查与 production build 全绿；必须验证 server/legacy `completed` 在 Authority 失败时不会显示成功。
- [ ] 真实修复或 wiring 变更后重新跑 peer-audit，而不是只跑新增 happy-path 测试。
- [ ] 测试不得访问 live PRIDE/生产模型或真实私钥；外部系统试跑与离线回归必须分开记录。

### 2.4 Capability / dispatcher MUST

- [ ] 唯一 dispatcher 只能执行 registry-approved adapter；执行前原子调用 `mark_execution_started(...)` 并持久化 idempotency reservation。
- [ ] `materialize_evidence`、`refresh_auth_context` 若仍未接安全 adapter，必须在产品能力说明中标为 unavailable，并保持 `registered_adapter_not_wired`；不得伪装已修复。
- [ ] 若产品范围宣称支持上述能力，则上线前必须实现 adapter、权限/预算边界、metric reader、失败回滚和集成测试。
- [ ] 禁止任意 shell、任意代码、任意 URL、副作用未注册的 tool dispatch。
- [ ] 同一 no-progress signature 连续 2 次无 delta 必须停止；不得换 intent 文案或 metric 文案绕过。

### 2.5 上线禁止事项 MUST

- [ ] 不得用候选数、审查数、judgment 数、正 delta、HTTP 200、Runner 返回或 event 名称代替 build-ready 成功。
- [ ] 不得恢复第二 Runner“返回即修复完成”的双轨。
- [ ] 不得为兼容旧测试把 hard unknown 当 pass、把 soft 升/降 hard、忽略 duplicate/overflow constraint。
- [ ] 不得在 `app.py`、主循环、Authority 或 UI 中加入免疫、DDA、RT/PSM 等案例字符串特判。
- [ ] 不得删除、xfail、放宽 peer-audit sacred negatives 来换取绿色结果。

## 3. 已知 fail-closed 行为（可能被误认为 bug）

| 现象 | 预期 Authority 行为 | 为什么不是 bug |
| --- | --- | --- |
| 找到很多候选、完成很多审查，但状态仍未完成 | 显示 progress / blocked_with_progress，build-ready=0 | 候选与审查只是中间进展 |
| 材料看起来齐全，但没有生产 signer / signed inventory | 不签发成功，保持 blocked | 未签发材料不能证明来自 Authority |
| dev signer 默认关闭 | 即使 package 完整也不毕业 | 防止开发开关成为生产后门 |
| 临时 dev signer 后重启，旧签名失效 | 旧 completion/package fail-closed | 临时 key 不具备 durable production 语义 |
| audit 不是精确 `ready`、缺 audit ref 或 closing audit | 不成功并公开 limitation | “attempt 完成”不等于 audit ready |
| package digest、manifest、membership、evidence 或 builder ref 不一致 | package 被拒绝 | 防止 signed inventory 被用于替换材料 |
| hard constraint unknown/conflict、重复 ID 含 hard、或超过支持上限 | hard_unknown/conflict blocker | hard 必须 fail-closed，不能静默丢失/降级 |
| proposal 缺 issue context、未知 capability/metric、参数 schema/risk/budget 不合规 | proposal reject / stop_with_limitations | 薄 Authority 只授权可计算且安全的动作 |
| `materialize_evidence` / `refresh_auth_context` 返回 not wired | repair blocked/incomplete | 当前缺安全 adapter；诚实阻塞优于假执行 |
| 同一 idempotent 动作再次请求 | 拒绝 duplicate execution | 防止重复副作用和换措辞重跑 |
| 相同 signature 两次无进展 | `repair_no_progress` + incomplete/blocked | 这是锁定的停止条件，不是循环失灵 |
| metric pair、completion token 或旧 run decision 被重放 | 第二次拒绝 / incomplete | issuance 是一次性且绑定当前 attempt |
| legacy `discovery_quality_repair_completed` 出现但 UI 不显示成功 | 只表示 attempt finished | legacy event 无业务成功授权力 |
| 缺 `openai-agents` / `typer` / `fastapi` 时测试 collection 失败 | 环境门禁失败，不应 skip | 这是部署环境未准备好，不是业务逻辑回归 |

## 4. 建议验收标准：什么叫“试跑通过”

试跑建议由用户**自愿选择**，不强迫用户必须执行。若执行，应区分“系统机制试跑通过”和“业务任务毕业”。

### 4.1 系统机制试跑通过

满足以下全部条件，才可称为一次系统试跑通过：

1. 使用完整依赖、隔离环境、受控凭据与明确预算，run 从对话/议程进入 discovery，只有 Manager 写 assistant reply。
2. 真实 candidate、inspection、judgment、audit、issue、repair、publication 数据写入 typed run record；可凭 run id 回放。
3. capability dispatch 只走 registry；idempotency reservation 在执行前持久化；metric pre/post 来自 Authority reader。
4. 遇到缺 signer、缺证据、unwired adapter、hard unknown 或 no-progress 时，系统诚实 blocked/incomplete，保留中间 artifact 并给出机器可读 limitation。
5. Runner 返回、HTTP 成功、repair attempt finished、候选/审查非零均未触发成功绿勾。
6. run save/load、服务重启或历史回放后，issuance 与一次性 replay 规则仍成立。
7. 日志、record、API 和 UI 对 searched/reviewed/judgment/build-ready/blockers 的显示一致，无 secrets 泄漏。

**重要：** 若真实任务最后诚实停在 `blocked_with_progress`，但以上机制均正确，这可以叫“系统机制试跑通过”；它仍然不是“该业务任务已完成”。

### 4.2 业务任务试跑毕业

只有同时满足以下条件，才可称为该试跑的业务任务通过：

1. 最新 Authority audit 为 ready，且无 hard conflict / hard unknown / unresolved blocker。
2. 生产级 signer 签发的 inventory 验证通过；canonical package digest、run/audit/manifest/EvidenceStore/builder/membership/observations 全部一致。
3. build-ready project/file 均非零，文件角色、可访问性、size、membership 和 evidence 满足 builder 入口。
4. 使用与生产相同版本的 dataset builder 做 preflight 或 dry-run，确认 package 能被接受进入构建；仅“schema 能解析”不够。
5. `BusinessCompletionDecision` 为 issued `succeeded=true / status=build_ready_succeeded / package_kind=build_ready / success_ui_allowed=true`。
6. API/run record/UI 三处一致显示 build-ready；成功 event 只能来自当前 run/attempt 的一次性 issued completion。
7. 保存验收证据：run id、audit ref、package digest、builder preflight 输出、测试版本、依赖版本与签名 key id（不保存私钥）。

### 4.3 建议的首轮手动试跑矩阵（可选）

- **负向 32/0 类场景：** 有候选/审查、0 build-ready；期望诚实 blocked，无绿勾。
- **缺 signer 场景：** package material 完整但 signer 不可用；期望 fail-closed。
- **unwired capability 场景：** 触发 `materialize_evidence` 或 `refresh_auth_context`；期望明确 not wired，不静默成功。
- **no-progress 场景：** 同 signature 两次零 delta；期望停止并给 limitation。
- **正向 build-ready 场景：** 生产等价 signer + 完整 evidence/membership + builder preflight；期望唯一成功路径。
- **replay 场景：** 重放旧 package、metric pair 或 completion；期望全部拒绝。

## 5. 红线

### 5.1 假成功红线

- 任何 `build_ready=0`、audit 非 ready、package 未签发或 builder 入口未验证的状态显示 completed/成功绿勾。
- 把 Runner return、HTTP 200、工具返回、candidate/review/judgment 非零、repair positive delta 或 legacy event 当成业务成功。
- 以“系统机制试跑通过”替代“业务任务 build-ready 毕业”。

### 5.2 Issuance 削弱红线

- 在生产启用 dev signer，或把私钥、固定签名秘密、真实凭据写入代码、仓库、fixture、日志或 prompt。
- 接受 unsigned inventory、只验证非空字段、跳过 canonical package digest、允许 package substitution。
- 接受公开 model/Literal 作为 provenance，或允许 metric/completion token 跨 run、跨 attempt、跨 Authority 或重复消费。
- 为“恢复成功率”而关闭 hard fail-closed、package validation、idempotency、no-progress 或 replay 防护。

### 5.3 案例特判红线

- 在 Authority、dispatcher、`app.py` 或 UI 中按 immuno、DDA/DIA、RT、PSM、PXD、特定候选数量或真实事故 ID 分支。
- 为单一 benchmark/fixture 写特殊成功条件、特殊证据豁免或特殊 repair 路径。
- 正确做法只能是：新增通用 capability primitive、TaskProfile 数据、可验证 observation、明确 schema/policy 和相应回归测试。

## 6. Go / No-Go 规则

- **当前：** 测试门禁 GO；生产正式上线 / 对外宣称正式可用 NO-GO。
- **受控开发试跑：** 在 dev signer 显式开启、隔离环境和诚实 fail-closed 前提下可选 GO。
- **生产真跑：** 只有第 2 节全部勾选、完整依赖回归全绿、生产 signer 与 adapter 运维就绪、且第 4 节至少完成一轮负向矩阵和一轮正向 builder preflight 后才可 GO。
- 任何红线触发：立即 NO-GO，回滚成功宣称；不得通过删除测试或降级 Authority 解决。

STATUS: READY
