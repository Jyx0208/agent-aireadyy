---
document: WAVE3_REPORT
authority: docs/plans/LOCKED_PLAN.md + docs/plans/WAVE2_ARTIFACTS.md
wave: 3
scope: H5、H7（开放 RepairProposal + 薄 Authority Plane）
business_completion: build-ready only
status: FIXED_AWAITING_PEER_REVIEW
---

# Wave 3 实施报告

> **状态更正（2026-07-22）：** 本报告提交后，peer audit 判定 Wave 2/3 为 FAIL。7 个 fail-closed 负例现已修复，权威修复记录与最新测试结果见 `docs/plans/WAVE2_3_FIX_REPORT.md`。本旧报告不能作为 PASS 证据。

## 1. 结论

Wave 3 的开放 repair proposal 与薄 Authority Plane seam 已实现。Wave 1 留下的 6 个 `WAVE 3 RED` 已转绿；本波没有进入 Wave 4，没有修改 `src/agent/web/app.py` 或 frontend。

唯一业务毕业标准仍是 build-ready。候选、审查、judgment 或单次 repair attempt 完成都只能表示中间进展；`repair_succeeded` / `build_ready_succeeded` 必须同时满足最新 audit ready 和完整的 `BusinessCompletionDecision` build-ready 合约，Runner 返回文案或其自报 delta 均不能触发成功。

## 2. 变更文件

### 产品代码

- `src/agent/control_plane/capabilities.py`
  - 新增可加法扩展的 `CapabilityRegistry` 与 `CapabilityPrimitive`；
  - 注册 8 个通用 capability primitives；
  - 镜像 Wave 2 LP2 metric 白名单，包括权威 source、aggregation 与比较方向；
  - 镜像 Wave 2 LP6 issue code → capability/metric/risk 指导表；
  - 每个 primitive 声明 risk、预算单位、幂等策略、参数 schema 标识、adapter、权威事件及上下文约束；
  - 不包含科学主题、特定 accession 或 32/0 案例分支。

- `src/agent/control_plane/repair.py`
  - 新增开放 `RepairProposal` v2、`SuccessMetricSpec`、审批结果与 attempt 结果模型；
  - 新增 `RepairAuthority.review_proposal(...)`，对 capability、metric、方向、参数、risk、预算、刷新上限和 build-ready selection 门进行 fail-closed 校验；
  - 未知 capability 与不可计算/模型代码 metric 明确 `reject`，带机器可读 `reason_code`；
  - `refresh_auth_context` 默认最多批准 1 次；
  - 新增 `record_attempt(...)`，由 Authority 根据 pre/post 自行计算 delta，不信任 Runner 提交的 `delta`；
  - no-progress signature 包含 `approved_capability_set + parameter_hash + issue_code_set + metric_id`，默认连续 2 次同签名无进步后停止；
  - 新增 `events_for_finished_attempt(...)`，严格分离 `repair_attempt_finished` 与业务成功；
  - 新增显式 v1 action → v2 proposal upgrader，旧 action 可回放但不会自动获得成功语义。

### 测试

- `tests/test_discovery_repair_controller.py`
  - 原 6 个 Wave 3 红灯转绿；
  - 补充 LP2/LP6 registry 镜像、Authority 自算 delta、完整 build-ready 成功门、缺字段 fail-closed 与 v1 upgrader 回归测试；
  - 全部离线，不访问网络、真实密钥或 live repository。

### 文档

- `docs/plans/WAVE3_REPORT.md`
  - 本报告。

## 3. H5 / H7 对照

### H5：repair 完成被误报为成功

- attempt 完成固定先产生 `repair_attempt_finished`，不等于 `repair_succeeded`；
- Authority 从可信 pre/post 计算 delta，忽略 Runner 自报 delta；
- 正 delta 只能产生 `repair_progressed`；
- 零或反向 delta 产生 `repair_no_progress`，相同 signature 连续 2 次后产生 `repair_incomplete` 并停止；
- 只有 audit=`ready`，且 completion 同时具备 `succeeded=true`、`status=build_ready_succeeded`、`package_kind=build_ready`、`success_ui_allowed=true`、build-ready project/file 均非零，才允许成功事件。

### H7：stale context、越权能力与无界重试

- capability 必须存在于可扩展 registry，未知 capability 明确拒绝；
- success metric 必须命中 LP2 白名单，aggregation/source/方向若显式提供则必须一致；模型代码、自评文字或表达式不能作为 metric；
- capability composition 必须通过 risk 与剩余预算校验；
- proposal 不得要求放宽 hard constraint 或把 hard unknown 当 pass；
- `select_manifest` 在 publication/build-ready 合约未通过时拒绝；
- stale search/grant repair 可组合 `refresh_auth_context + inspect`，但刷新上限为 1；
- intent 文本保持开放，Authority 不用固定业务 kind 或科学主题字符串决定路线。

## 4. 测试命令与结果

### 4.1 指定 Wave 3 + publication/Wave 2/sacred 收尾回归

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_constraint_bindings.py `
  tests/test_discovery_evidence_store.py `
  tests/test_discovery_quality_audit.py
```

结果：

```text
133 passed in 17.57s
```

其中 `test_discovery_repair_controller.py` 单独结果为：

```text
15 passed in 1.11s
```

原 6 个 `WAVE 3 RED` 已全部转绿；repair controller + publication contracts 合计 25 passed。

### 4.2 扩展 sacred 回归

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_constraint_bindings.py `
  tests/test_discovery_evidence_store.py `
  tests/test_discovery_quality_audit.py `
  tests/test_discovery_scientific_constraint_validity.py `
  tests/test_discovery_mixed_acquisition_policy.py `
  tests/test_discovery_sdrf_assay_evidence.py
```

结果：

```text
167 passed in 16.72s
```

### 4.3 静态检查

```powershell
& 'E:\anaconda\python.exe' -m compileall -q `
  src/agent/control_plane/capabilities.py `
  src/agent/control_plane/repair.py
```

结果：通过；新增产品模块未发现 trailing whitespace，也未包含 `immunopeptidomics`、`immunopeptide` 或特定 accession 分支。

## 5. 边界与未做项

- 本波实现并验证 Authority seam，没有把它接入 `openai_agents.py` 的完整主循环；旧事件 replay 与 UI 诚实展示属于锁定计划 Wave 4，不能把 legacy `discovery_quality_repair_completed` 解读成成功。
- no-progress 状态目前由一个 `RepairAuthority` 实例维护；持久化到 run store 与完整事件接线尚未在本波展开。
- capability adapter 本波只声明注册边界，不在 registry 内执行任意代码、shell、URL 或未注册副作用。
- 未修改 `src/agent/web/app.py`、frontend 或用户已有脏 worktree 内容，也未 reset/clean。
- 当前 Anaconda 测试环境缺少既有 `typer`，因此额外尝试 `tests/test_control_plane.py` 时在收集阶段因 `ModuleNotFoundError: typer` 停止；这不是成功测试。完整 Python 3.13 + `openai-agents` 环境仍需补跑此前 W1-N1/W2-N1 的 agent-turn sacred。
- 本报告不宣称 merge-ready；等待 Grok 验收，不授权开始 Wave 4。

## 6. 验收状态

Wave 3 的目标 seam、LP2/LP6 registry、delta/no-progress、bounded refresh、build-ready 成功门及离线回归均已就绪，提交 Grok 审查。

WAVE3_STATUS: FIXED_AWAITING_PEER_REVIEW
