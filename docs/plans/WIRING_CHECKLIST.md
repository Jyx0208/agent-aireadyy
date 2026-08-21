# Discovery Authority 接线清单

本清单只描述 Wave 6 后的最小接线顺序，不授权修改 `app.py`、Wave 5 议程树或 UI。接线必须保持 build-ready 为唯一业务毕业，并继续通过 peer-audit/property suites。

## 1. Publication → run record

目标触点：

- `src/agent/control_plane/models.py::AgentRunRecord`
- `src/agent/control_plane/store.py::AgentRunStore`
- `src/agent/control_plane/openai_agents.py::_audit_and_persist`
- `src/agent/discovery/publication.py::PublicationContractRegistry`

顺序：

1. 确定性平面从 run store 读取当前 run、latest audit、manifest、EvidenceStore、membership 与 builder-entry 状态；不得读取 Runner 文案作为事实。
2. Authority signer 在产品进程外持有私钥，签发 `PublicationAuthorityState`；产品只接收 opaque inventory signature 并用内置公钥验证。
3. canonical `BuildReadyPackage` 必须与 signed `authorized_package_digest` 一致；package、audit、run、manifest、EvidenceStore、builder、membership、observations 任一不一致即返回 progress/blocked。
4. 调用 `PublicationContractRegistry.evaluate(snapshot)`；将完整 `BusinessCompletionDecision` 持久化为 run record 的 typed 字段，而不是塞入自由文本 summary。
5. `AgentRunRecord.status` 不得仅因 Runner 返回变成成功。只有 issued completion 的 `succeeded=true`、`package_kind=build_ready`、`success_ui_allowed=true` 可驱动业务完成。
6. API adapter 把 decision 放到前端已约定的 `record.business_completion`（或先与 @ui 协调唯一实际路径）；不允许前端从 candidate/review counts 推导成功。

最小接线测试：

- run save/load 后 decision schema、package digest 与 issuance token round-trip；
- 缺 signer/inventory、audit ref mismatch、package substitution 均保持 blocked；
- 32 candidates / 0 build-ready 的 run record 不产生 completed/success event。

## 2. Repair → OpenAI Agents 主循环

目标触点：

- `src/agent/control_plane/openai_agents.py` quality-audit repair 段
- `src/agent/control_plane/repair.py::RepairAuthority`
- capability adapters（独立注册，不在主循环写科学主题 if/else）

顺序：

1. audit 产生 Authority-owned issue set 与 evidence scopes；缺 issue context 时 proposal admission 必须拒绝。
2. Agent/Runner 只提出开放 `RepairProposal`；调用 `review_proposal(...)` 校验 LP6 policy、metric、参数 schema、risk 和预算。
3. dispatch 前原子调用 `mark_execution_started(decision)`，并把 idempotency key 持久化到 run store；不能先执行 adapter 再补 ledger。
4. Authority 注入的 `AuthorityMetricReader` 在相同 scope/schema 下 capture pre observation；Runner 不得提交 pre 数字。
5. 执行 registry-approved capability adapters；未知 capability、shell、任意 URL/代码或未注册副作用拒绝。
6. Authority capture post observation，调用 `record_attempt(...)` 计算 delta；observation pair 一次性消费。
7. 同 signature 连续 2 次无进步后停止；正 delta 仅发 `repair_progressed`，不能发成功。
8. re-audit 后，由当前 `RepairAuthority.completion_context(attempt_id)` 生成私有 nonce binding，再调用 Publication Registry。
9. `events_for_finished_attempt(...)` 消费当前实例/attempt 的 issued completion；旧 run、新 Authority、复制 public authority id 或第二次 replay 均只能得到 `repair_incomplete`。

legacy `discovery_quality_repair_completed` 只回放为 attempt finished/incomplete。旧 v1 `repair_actions` 必须先经 `upgrade_v1_repair_action(...)`，不能直接获得 v2 成功语义。

## 3. 禁止项与接线前置

- signer 私钥、live credentials、dialogue DB 不得进入仓库或测试 fixture；fixture 只含公钥可验证的离线 signature。
- 不在 `openai_agents.py` 或 `app.py` 根据科学主题字符串分支。
- 不把 soft preference 升为 hard，不把 hard unknown 当 pass。
- 不把 project evidence 无 membership 下沉到 file。
- 不改变 one-writer dialogue 或 Wave 5 agenda 所有权。
- 接线修改 `app.py` 前必须先在 `TEAM_BOARD.md` 协调脏 worktree 与文件所有权。

WIRING_CHECKLIST_STATUS: DOCUMENTED_NOT_WIRED
