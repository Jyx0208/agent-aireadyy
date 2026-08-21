---
document: WAVE6_REPORT
authority: docs/plans/LOCKED_PLAN.md
wave: 6
scope: replay、property invariants、hardening sampling
network: offline
business_completion: build-ready only
status: READY_FOR_GROK
---

# Wave 6 实施报告

## 1. 结论

Wave 6 已完成离线 replay、属性/不变式测试、Authority hardening 抽检与 frontend production build 验证。本波没有修改产品运行逻辑；新增测试通过公开 seam 验证 Wave 2–3 peer PASS 后的 Authority contract，并继续保留全部 peer-audit 负例。

当前只声明 `READY_FOR_GROK`，不声明 merge-ready。Wave 5 仍按编排保持 `PARTIAL`；本波未接 `app.py` 议程树。

## 2. 变更文件

- `tests/test_discovery_authority_properties.py`
  - 新增 24 个离线属性/replay 用例；
  - 无网络、无 live PRIDE、无 secrets。
- `docs/plans/WIRING_CHECKLIST.md`
  - 记录 publication→run record 与 repair→OpenAI Agents 的最小接线顺序和 fail-closed 前置；
  - 状态仅为 documented，未进行主循环接线。
- `docs/plans/WAVE6_REPORT.md`
  - 本报告。
- `docs/plans/TEAM_BOARD.md`
  - Wave 6 里程碑与待 Grok 状态。

## 3. 属性与不变式覆盖

### 3.1 Soft 永不 hard-exclude

对 project/assay/file/sample/spectrum/portfolio 六种 scope 和多种 operator 添加缺证据或不匹配 soft preference；已经 issued build-ready 的状态仍可毕业，且不产生 hard blocker。

### 3.2 Hard unknown 永不 pass

对六种 scope 分别添加无 Authority observation 的 hard constraint；所有决策均 `succeeded=false`、`success_ui_allowed=false`，并明确报告对应 `hard_unknown:<dimension>`。

### 3.3 Project evidence 不下沉 file

即使调用者提交 project→file 字符串并把同一 membership ref 放进 available set，`EvidenceStore` 仍拒绝不在允许 edge family 中的 scope promotion。

### 3.4 No-progress 有界停止

使用 Authority metric reader capture 两轮同 scope、同 signature、零 delta observation pair：首轮记录 no-progress，第二轮默认上限 2 强制停止并发 `repair_no_progress + repair_incomplete`，不发成功。

### 3.5 Success event 只对应 issued build-ready completion

- 正向路径必须经过 signed inventory、canonical package digest、当前 RepairAuthority 私有 attempt nonce 和 Registry-issued completion；
- completion 首次消费可发 `repair_succeeded/build_ready_succeeded`；第二次 replay 不可再次成功；
- 32 candidates / 20 judgment-qualified / 0 build-ready 只保留进度，不能产生成功事件。

## 4. v1 replay 兼容

- 参数化回放 5 个 v1 repair action：`search_more`、`inspect_candidates`、`rescore_projects`、`select_manifest`、`stop_with_limitations`；均显式升级为 `discovery-repair-proposal/v2` capability/metric envelope。
- `discovery-quality-audit/v1` 可由当前 `DiscoveryQualityAudit` 解析并保留 issue/action；其中 v1 action 再经 upgrader。
- legacy `discovery_quality_repair_completed` 即使伴随自报 success mapping，也只得到 `repair_attempt_finished + repair_incomplete`。

## 5. 测试命令与结果

### 5.1 Wave 6 新增 suite

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_authority_properties.py
```

```text
24 passed in 1.12s
```

### 5.2 Authority/constraint/quality 抽检组合

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_authority_properties.py `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_evidence_store.py `
  tests/test_discovery_constraint_bindings.py `
  tests/test_discovery_scientific_constraint_validity.py `
  tests/test_discovery_quality_audit.py
```

```text
202 passed in 17.53s
```

全部 peer-audit 负例保持启用，无 xfail/skip。

### 5.3 Frontend production build

```powershell
npm run build
```

在 `frontend/benchmark-review` 执行：TypeScript build 与 Vite production build 通过，12539 modules transformed。Vite 报告既有大 chunk 警告，不影响退出码。构建生成的 hashed static bundle 已恢复到构建前无 diff 状态，未占用 frontend/UI 文件所有权。

## 6. 风险与未做项

- 未进行 publication/repair 主循环接线；具体顺序见 `WIRING_CHECKLIST.md`。
- Authority signer 私钥与 durable ledger 必须由生产确定性平面提供；仓库只保留验证公钥与离线测试 signature。
- `openai_agents.py` 仍有 legacy repair event 路径；接线时必须按 checklist 使用 typed proposal、metric reader、idempotency reserve、re-audit 和 issued completion，不能把 Runner 返回当成功。
- 当前环境仍缺完整 Python 3.13 + `openai-agents`/`typer` 依赖，未补跑既有 W1-N1/W2-N1 agent-turn sacred。
- 未调用 live PRIDE、Grok、DeepSeek 或其它外部模型；Grok 最终验收由编排执行。
- 未修改 frontend、`app.py`、Wave 5 agenda 或用户既有脏 worktree。

WAVE6_STATUS: READY_FOR_GROK
