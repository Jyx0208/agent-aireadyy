---
document: WAVE2_REPORT
plan: docs/plans/LOCKED_PLAN.md
wave: 2
status: READY_FOR_GROK
scope: Contract spine（H1–H4）
architecture: 方案 2（灵活智能层 + 薄 Authority Plane）
business_completion: build-ready only
network_used: false
---

# Wave 2 实施报告

## 1. 结论

Wave 2 合约 spine 已实现。Wave 1 的 3 个 publication 红灯已转绿；新增 hard unknown、hard conflict、soft preference、显式 build-ready=0 权威性、constraint binding 与 EvidenceStore scope 测试也已转绿。

本波没有实现 Wave 3 的 `CapabilityRegistry` / `RepairAuthority` 状态机，没有修改 `src/agent/web/app.py`，没有把候选/审查冒充业务完成。

唯一业务毕业仍是 build-ready：`BusinessCompletionDecision.succeeded=true` 需要 build-ready project/file 非零、无缺失材料、无 hard conflict/unknown、最新 audit ready；`success_ui_allowed` 只能与该结果同时为 true。

## 2. 变更文件

### 产品代码

- `src/agent/discovery/publication.py`（新增）
  - `PublicationContractRegistry.evaluate(snapshot)`；
  - `BusinessCompletionDecision`；
  - `PublicationProgress`；
  - progress/build-ready package kind；
  - hard conflict/unknown fail-closed；
  - soft constraint 不阻塞；
  - 显式 build-ready count 优先于从 file rows 推导，显式 0 不可被覆盖；
  - 缺显式 count 时，可按 identifier、URL、size、role、validity、review flag、evidence scope 推导候选 build-ready files。

- `src/agent/discovery/evidence_store.py`（新增）
  - `EvidenceObservation` / `EvidenceStoreArtifact` v1；
  - materialize 时校验 source refs；
  - observation id 幂等且冲突失败；
  - project evidence 不自动下沉到 file；
  - assay→file、file→spectrum、sample→file 仅凭显式 membership ref 解析。

- `src/agent/discovery/constraints.py`（修改）
  - `ConstraintStrength` 增加 `open`；
  - scope 增加 `assay`、`spectrum`，保留旧 `sample` 等值；
  - `normalize_constraint_bindings` 将紧凑 binding 规范化为现有 `ScientificConstraint`；
  - `accepted_preference` 映射为已有 `accepted_recommendation` provenance；
  - 未创建第二套约束模型。

### 测试

- `tests/test_discovery_publication_contracts.py`（扩展）
  - Wave 1 的 3 个目标红灯转绿；
  - 增加 hard unknown、hard conflict、soft-only、显式 build-ready=0 测试。

- `tests/test_discovery_constraint_bindings.py`（新增）
  - binding 复用 `ScientificConstraint`；
  - `open` / `spectrum` 一等支持。

- `tests/test_discovery_evidence_store.py`（新增）
  - unknown refs 拒绝；
  - project 不隐式下沉；
  - assay 只向显式成员 file 解析。

### 文档

- `docs/plans/WAVE2_ARTIFACTS.md`
  - LP2 metric 白名单；
  - LP6 issue→capability/metric/risk 表；
  - Evidence scope/materialization 规则；
  - v1 `hard_constraint_fields` 兼容说明。

- `docs/plans/WAVE2_REPORT.md`
  - 本报告。

## 3. 接口行为

### 3.1 Publication seam

调用：

```python
decision = PublicationContractRegistry().evaluate(
    {"request": request_snapshot, "state": discovery_state}
)
```

返回至少包含：

- `succeeded`
- `status`
- `package_kind`
- `progress`
- `limitations`
- `success_ui_allowed`

规则：

- build-ready project/file 任一为 0：不毕业；
- judgment-qualified 非零：保留为 progress；
- hard conflict/unknown：fail-closed；
- soft missing/mismatch：不单独阻塞；
- missing build-ready fields 或 audit 未 ready：不毕业；
- 只有 build-ready 成功允许 success UI。

### 3.2 Evidence seam

调用：

```python
store = EvidenceStore(available_refs={...})
store.materialize(observation)
store.resolve(subject_kind=..., subject_id=..., dimension=...)
```

接口隐藏 refs 校验、幂等与 scope membership 逻辑；调用方不能通过复制 project 字段伪造 file evidence。

## 4. 测试命令与结果

当前完整依赖 Python 3.13 环境仍不可用；沿用 Wave 1 的 Anaconda Python 3.12.7 + pytest 7.4.4。全部测试无网络、无 live PRIDE。

### 4.1 原 Wave 2 红灯

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_publication_contracts.py
```

```text
10 passed in 1.05s
```

其中 Wave 1 原有 3 个 `WAVE 2 RED` 已转绿，且没有 xfail/skip。

### 4.2 Wave 2 + sacred 回归

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_constraint_bindings.py `
  tests/test_discovery_evidence_store.py `
  tests/test_discovery_quality_audit.py `
  tests/test_discovery_scientific_constraint_validity.py `
  tests/test_discovery_mixed_acquisition_policy.py `
  tests/test_discovery_sdrf_assay_evidence.py
```

```text
152 passed in 14.49s
```

### 4.3 Wave 3 红灯仍保留

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_repair_controller.py
```

```text
6 failed, 3 passed in 1.04s
```

6 个失败均因 `agent.control_plane.capabilities` / `agent.control_plane.repair` 尚不存在，符合 Wave 3 边界。本波未用 stub、xfail 或假实现消除这些红灯。

### 4.4 语法与 diff 检查

新增 Python 模块和测试通过 `compileall`；`git diff --check` 无内容错误，仅有现有 Windows LF/CRLF 提示。

### 4.5 环境限制

- `tests/test_discovery_pipeline_handoff.py` 尝试收集时因当前环境缺少 `typer` 失败；未安装依赖。
- Wave 1 W1-N1 仍存在：当前环境缺少 `openai-agents`，`test_discovery_agent_turn.py` sacred 抽查需在完整依赖环境补跑。

## 5. H1–H4 对照

| H 类 | 本波实现 |
| --- | --- |
| H1 Horizon/毕业尺子错 | publication seam 将候选/审查固定为 progress，只有 build-ready package 毕业 |
| H2 Soft→Hard | binding 复用 `ScientificConstraint`；soft 缺失测试证明不阻塞，hard 仍 fail-closed |
| H3 Evidence scope | EvidenceStore 禁止 project→file 隐式传播，仅允许显式 membership edge |
| H4 双质量定义/未物化 | judgment-qualified 独立于 build-ready；EvidenceObservation 以验证 refs 物化；唯一 BusinessCompletionDecision 签发成功 |

真实 derived 32/0 fixture 结果为 `blocked_with_progress`、`package_kind=progress`、`success_ui_allowed=false`；synthetic control 补齐材料后为 `build_ready_succeeded`。

## 6. 风险与未做项

- `PublicationContractRegistry` 当前通过独立 snapshot seam 工作，尚未接入 `discovery.py` 或 web UI；锁定计划允许先完成 seam，本波未冒险触碰脏 `app.py`。
- `EvidenceStore` 当前为最小内存实现，可序列化 `EvidenceStoreArtifact`，尚未接 run artifact persistence。
- 文件级 build-ready 推导是保守入口检查；若 authority state 已显式提供 build-ready count，显式值优先，避免本地重复推导覆盖权威 0。
- v1 `hard_constraint_fields` 仍由现有 `DatasetRequest` 与 audit 路径接受；尚未迁移前端 payload。
- 未实现 Wave 3 capability registry、开放 RepairProposal 审批、delta/no-progress 状态机。
- 未实现 Wave 4 UI 诚实事件；Wave 1 前端红灯继续保留。
- 未运行网络或 live repository 测试；未修改/打印 secrets。
- 未 reset/clean，未覆盖用户已有 `src/agent/web/app.py` 和 `tests/test_discovery_agent_turn.py` 改动。

## 7. 验收状态

Wave 2 目标 seam、EvidenceStore、LP2/LP6 产物和 H1–H4 测试已就绪，等待 Grok 审查。本报告不宣称 merge-ready，也不授权开始 Wave 3。

WAVE2_STATUS: READY_FOR_GROK
