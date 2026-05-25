# 公共原始质谱数据标准化 Agent MVP

## 目标

这个 MVP 的定位不是“把一个固定 workflow 跑起来”，而是让系统像一个受控专家助理一样完成公共原始质谱数据标准化：

- 看懂输入属于哪个仓库、项目、文件和实验上下文。
- 自动选择最高置信度的 repository、database、workflow 和搜索参数。
- 在证据不足或生物学风险较高时停止并留下可审计理由。
- 在低风险工程错误上给出恢复策略，并写入 `recovery_audit.json`。
- 为 parameters、prepare、full、batch 四类路径生成一致的审计产物。

## 自治等级

| 等级 | 含义 | 当前行为 |
| --- | --- | --- |
| L0 | 只记录 | 记录输入、仓库解析、属性推断、计划和运行状态。 |
| L1 | 证据门控规划 | 自动选择项目、文件、FASTA、workflow 和参数；低置信度或冲突进入 review。 |
| L2 | 受控工程恢复 | 对内存、下载、转换、缺失输出等工程失败生成恢复建议和审计记录。 |
| L3 | 生物学自主改写 | 高风险生物学事实不自动改写，必须人工确认。 |

当前 MVP 目标是稳定达到 L1 + 受限 L2。L3 不开放，因为错误物种、错误数据库、错误采集模式或错误酶切策略会直接破坏结果可信度。

## 决策链

核心审计文件：

- `project_resolution.json`：仓库与项目候选、匹配分数、是否需要人工复核。
- `asset_resolution.json`：匹配到的仓库文件、文件类型、下载方式、转换需求和资产置信度。
- `attributes.json`：采集模式、物种、仪器、酶、修饰、搜索参数及其证据来源。
- `decision_trace.json`：DDA 执行计划和阻断原因。
- `agent_observation.json`：Agent 观察到的项目/文件/元数据。
- `agent_plan.json`：database、workflow、search parameters、resource policy 的计划摘要。
- `agent_decision_trace.json`：可审计决策记录，包括 `project_selection`、`file_matching`、`database_selection`、`workflow_selection`、`resource_policy_selection` 和属性推断。

任何 `review_required` 决策都会使 `agent_plan.execution_gate` 变成 `review_required`，并把原因写入 `blocking_issues`。

## 证据门控

系统允许自动执行的前提：

- 项目解析没有跨仓库同分歧义，且 `resolution_confidence >= 0.85`。
- 文件资产不是 `unknown`，`asset_confidence >= 0.75`，并且存在仓库匹配文件、逻辑路径或本地路径。
- DDA 采集模式可确认；DIA、Top-down、metabolomics/small-molecule 数据会阻断。
- 物种、仪器家族、酶切酶是可解释的非空值；冲突值必须来自可信来源并达到高置信度。
- FASTA 必须是真实可下载或用户确认的数据库；占位 FASTA 不允许进入真实搜库。
- workflow 不能只来自弱规则的名称猜测；必须有 LLM、SDRF、人工确认，或至少有数据库/容差等支持性参数证据。

这些规则偏保守：宁可进入人工复核，也不自动生成生物学上站不住的结果。

## 恢复策略

失败时系统会把结构化错误写入 `recovery_audit.json`，包含：

- `task`：任务、输入、仓库、项目、输出目录和运行模式。
- `failure`：阶段、错误分类、证据、公开信息和操作提示。
- `recovery`：是否允许自动恢复、允许动作、参数、安全检查和下一步人工动作。
- `artifacts`：相关的 `task_state.json`、`review_queue.json`、`run_manifest.json`、`error.json`、运行日志等。
- `integrity`：幂等键和脱敏状态。

允许自动或半自动处理的低风险类别包括：

- `insufficient_memory` / `fragpipe_oom`：可建议降低线程数后重试。
- `download_failure` / `network` / `timeout`：可建议在原下载边界内重试。
- `conversion_failure` / `docker_unavailable`：可建议切换已知转换器或检查本地工具链。

必须人工复核的类别包括：

- 缺失 `PIN`、缺失 `MSDT parquet`、空或损坏 mzML。
- 需要更改物种、数据库、采集模式、标记策略、酶切策略或 PTM 实验解释。
- 任何不在 allowlist 内的自由命令或任意 shell 操作。

## 运行模式产物

| 模式 | 产物原则 |
| --- | --- |
| parameters | 只生成参数、workflow 预览和审计；不下载 RAW/mzML/FASTA 大文件。 |
| prepare | 下载/转换数据并生成 MSDT-Converter 输入包；不运行 full workflow。 |
| full | 运行完整 Docker 流程；失败时写 `recovery_audit.json`，成功时打包结果。 |
| batch | 每个 item 独立输出审计、状态和错误恢复记录；批次 manifest 不保存 API Key。 |

`msdt_input_manifest.json` 的 `audit_files` 只列出实际存在的文件，避免前端或 ZIP 下载引用不存在产物。

## 工程边界

- 不自动安装 Java、Git、msconvert 或 Docker。
- 不执行 LLM 生成的任意命令。
- 不无限重试；恢复策略必须通过 allowlist。
- 不把弱关键词规则当作最终生物学事实。
- 不在 full workflow 禁用的展示服务器上强行执行 full。
- 不把 MassIVE、iProX、PRIDE 的仓库细节泄漏到下游计划层；下游只使用规范化模型。

## 可验证性

推荐回归套件：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_core.py tests\test_agent_recovery.py tests\test_execution_outputs.py tests\test_docker_pipeline.py tests\test_assets_integration.py tests\test_decision.py tests\test_repositories.py tests\test_web.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

只有这些测试通过，并且没有已知阻断问题时，才能把当前 Agent 闭环称为工程级 MVP。
