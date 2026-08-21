---
document: WAVE1_REPORT
plan: docs/plans/LOCKED_PLAN.md
wave: 1
status: READY_FOR_GROK
scope: 离线红灯夹具与未来公共合约测试
product_code_changed: false
network_used: false
---

# Wave 1 实施报告

## 1. 本波结论

Wave 1 已按锁定计划完成：新增两个脱敏、无网络 discovery scenario bundle、一个 scripted repair bundle、Python publication/repair 红灯测试，以及一条前端 32/0 诚实状态红灯测试。

本波没有实现 `PublicationContractRegistry`、`BusinessCompletionDecision`、`CapabilityRegistry`、`RepairAuthority` 或 repair 状态机；没有修改 `src/`。新增行为测试故意因这些 Wave 2/3 公共 API 尚不存在而失败。

业务语义已固化为：候选/审查是中间进展；只有 build-ready 才能使业务成功。32 个候选、约 20 个 judgment、0 build-ready 必须是 `blocked_with_progress`，不得显示交付成功或 repair 成功绿勾。

## 2. 变更文件

### 新增离线夹具

- `tests/fixtures/discovery/real_derived_progress_without_build_ready.json`
  - 脱敏 real-derived 32/0/2408 摘要；
  - 32 candidates、约 20 judgments、`build_ready_count=0`；
  - acquisition DDA 为 soft，label-free 为 assay-scope hard；
  - 不含真实 accession、密钥或 immunopeptide 分支词。

- `tests/fixtures/discovery/synthetic_rt_psm_build_ready_transition.json`
  - 非免疫 synthetic RT/PSM 场景；
  - 同一科学任务包含 progress-only 与 build-ready control 两个状态；
  - progress-only 缺 file/assay membership、URL、size、role 和 label source；
  - control 状态补齐材料后才允许业务毕业。

- `tests/fixtures/discovery/scripted_repair_proposals.json`
  - 开放 intent 映射到已注册 capability primitives；
  - unknown capability reject；
  - uncomputable metric reject；
  - stale search/grant context 请求一次刷新；
  - 相同 no-progress signature 连续两次零 delta；
  - attempt finished + audit not ready + 0 build-ready 禁止 success。

### 新增/修改测试

- `tests/test_discovery_publication_contracts.py`
  - 3 个绿色 fixture contract 测试；
  - 3 个 Wave 2 预期红测试，目标 seam：
    `agent.discovery.publication.PublicationContractRegistry.evaluate(...)`。

- `tests/test_discovery_repair_controller.py`
  - 3 个绿色 repair fixture contract 测试；
  - 6 个 Wave 3 预期红测试，目标 seam：
    `CapabilityRegistry`、`RepairAuthority.review_proposal(...)`、
    `record_attempt(...)`、`events_for_finished_attempt(...)`。

- `frontend/benchmark-review/src/DiscoveryProgressMessage.test.tsx`
  - 新增最小 UI 红灯：服务端旧状态为 `completed`，但 Authority business completion 为 false、build-ready 为 0 时，UI 不得显示“已完成”。

- `docs/plans/WAVE1_REPORT.md`
  - 本报告。

## 3. 测试命令与结果

本机默认 `python` 为 2.7；Python 3.13 安装未包含 pytest。因此本次使用已有 Anaconda Python 3.12.7 + pytest 7.4.4 执行测试。项目声明 Python >=3.13，此差异记录为环境风险，不在 Wave 1 安装或修改依赖。

### 3.1 新增 Python 测试：预期红/绿混合

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_repair_controller.py
```

结果：

```text
9 failed, 6 passed in 1.11s
```

预期红分类：

- 3 个 Wave 2 红：缺少 `agent.discovery.publication`，失败信息明确指向 `PublicationContractRegistry` 与 `BusinessCompletionDecision`；
- 6 个 Wave 3 红：缺少 `agent.control_plane.capabilities` / `agent.control_plane.repair`，失败信息明确指向开放 `RepairProposal` Authority Plane。

夹具本身的绿色验证：

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_repair_controller.py -k fixture
```

```text
6 passed, 9 deselected in 0.10s
```

### 3.2 Sacred green：审计、约束与 evidence scope

```powershell
& 'E:\anaconda\python.exe' -m pytest -q `
  tests/test_discovery_quality_audit.py `
  tests/test_discovery_scientific_constraint_validity.py `
  tests/test_discovery_mixed_acquisition_policy.py `
  tests/test_discovery_sdrf_assay_evidence.py
```

```text
137 passed in 17.60s
```

覆盖了 hard fail-closed、soft-only ranking、constraint evidence grounding、file/portfolio scope、mixed acquisition 与 SDRF assay evidence；未因 Wave 1 退化。

### 3.3 前端最小红灯

```powershell
npm.cmd run test -- src/DiscoveryProgressMessage.test.tsx
```

```text
1 failed, 4 passed
```

预期红：当前 `buildDiscoveryRunView` 仍把传入的旧 `status=completed` 直接显示为 completed，没有以 `business_completion.succeeded=false` 和 `build_ready_projects=0` 覆盖为进展/阻塞状态。原有 4 个同文件测试保持绿色。

### 3.4 未能执行的 SDK sacred 抽查

尝试运行 `tests/test_discovery_agent_turn.py` 中 one-writer、session、confirmation 与 fingerprint 六个定向用例，但当前 Anaconda 环境缺少 `openai-agents`：

```text
ModuleNotFoundError: No module named 'agents'
```

本波没有安装依赖，也没有修改该测试或 `src/agent/web/app.py`。这属于测试环境缺依赖，不计作产品失败；Grok/后续具备项目完整依赖的环境应补跑这组六项 sacred tests。

## 4. H 类覆盖

| H 类 | Wave 1 红灯覆盖 |
| --- | --- |
| H1 | 32/0 夹具明确区分 progress 与 build-ready graduation |
| H2 | DDA 明确为 soft；label-free 明确为 hard，禁止 concrete value 自动变 hard |
| H3 | synthetic 场景分别列出 project、assay、file evidence 与 membership 缺口 |
| H4 | judgment-qualified 非零但 build-ready 为零仍不得完成；control 状态补齐材料后才毕业 |
| H5 | 开放 proposal、metric 可计算性、两次 no-progress、stale context 和 finished-not-success fixtures |
| H6 | Python event contract + 前端 32/0 红灯均禁止假成功绿勾 |
| H7（附加） | stale search/grant context 只能经 `refresh_auth_context` 有界刷新一次 |

没有处理 H8；动态科学议程属于 Wave 5。

## 5. 风险与未做项

- 预期红测试定义的是锁定计划中的公共 seam，Wave 2/3 实现需让这些行为转绿，而不是 xfail/skip 或削弱断言。
- Python 测试运行环境为 3.12.7，不是项目声明的 >=3.13；完整依赖环境需复跑。
- `test_discovery_agent_turn.py` 因缺 `openai-agents` 未完成本轮抽查。
- 本波没有实现 publication、build-ready package、capability registry、Authority Plane 或 UI 修复。
- 本波没有修改 `tests/test_discovery_quality_audit.py`，避免把未来语义混入现有 sacred 行为。
- 没有网络调用、live PRIDE、真实密钥、真实 accession 或 run bundle 依赖。
- 没有 reset/clean；没有覆盖脏 worktree 中既有 `src/agent/web/app.py` 和 `tests/test_discovery_agent_turn.py` 改动。

## 6. 验收状态

Wave 1 所需夹具、预期红测试、绿色数据校验和 sacred 回归证据已就绪，等待 Grok 审查。本报告不宣称 merge-ready，也不授权开始 Wave 2。

WAVE1_STATUS: READY_FOR_GROK
