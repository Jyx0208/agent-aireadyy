# AI-ready Data Agent 项目交接文档

更新时间：2026-06-26

## 1. 项目当前定位

本项目当前是一个 **task-aware proteomics AI-ready data agent v1 smoke release**。它已经可以从自然语言或结构化表单出发，完成小中规模真实数据上的闭环验证：

```text
数据需求 / 建模目标
-> 数据发现 Discovery
-> 候选 project / file 筛选与评分
-> Batch parameters / prepare / full 或 partial-output recovery
-> AI-ready Build
-> dataset recipe / split / leakage / hard benchmark / curation queue
-> model-loop smoke / gap plan
-> Web / CLI / Docker 复现
```

当前版本适合用于项目交接、后续开发、小中规模验收和内部复查；不应描述为“任意用户一键大规模生成训练集”或“已经完成真实模型训练闭环”的生产系统。

## 2. 代码与部署路径

本文档中的代码路径均以仓库根目录为起点，不记录本机绝对路径。

### 2.1 仓库根目录

```text
.
```

交接时以 AI station 路径为主。AI station 完整副本保留运行证据；轻量代码副本仅用于源码查阅，通常只包含源码、测试、轻量配置和文档，不包含 `.env`、`runs/`、RAW/mzML/parquet/xlsx 等大文件或真实运行产物。

### 2.2 AI station 路径

AI station 上的交接副本路径：

```text
/mnt/inaisfs/home/wdbl/ajun/JieWu/agent-aireadyy
```

本中文交接文件在 AI station 上的路径：

```text
/mnt/inaisfs/home/wdbl/ajun/JieWu/agent-aireadyy/docs/PROJECT_HANDOFF_CN.md
```

登录 AI station 后可进入该目录：

```bash
cd /mnt/inaisfs/home/wdbl/ajun/JieWu/agent-aireadyy
```

如果 AI station 上需要继续运行 Web 或测试，优先在该目录下执行 Docker / CLI 命令。

### 2.3 容器内路径

Docker Web 服务的容器工作目录：

```text
/app
```

容器内运行结果默认写入：

```text
/app/runs
```

宿主机对应为仓库下的：

```text
runs/
```

### 2.4 受保护 benchmark 证据路径

本地完整交接证据保存在：

```text
runs/_protected_benchmark_20260624_4_5_sample_pool
```

该目录包含 `.agent_keep`，用于避免 Web 的自动清理机制删除它。当前受保护证据池约 1.5 GB，属于 AI station / 本地交接证据；如移动项目，需要单独确认该目录是否一并迁移。

## 3. 目录结构说明

核心目录如下：

```text
README.md
docker-compose.yml
Dockerfile
.env.example
src/agent/
tests/
docs/
scripts/
profiles/
runs/
```

主要代码路径：

```text
src/agent/cli.py
```

命令行入口，包含 Discovery、Batch handoff、AI-ready Build、recipe、model-loop、iProX index refresh 等命令。

```text
src/agent/web/app.py
src/agent/web/templates/index.html
```

Web 服务与前端模板，提供 Discovery、Batch、AI-ready Build、Recipe/Split、Repository smoke 等页面功能。

```text
src/agent/discovery/
```

通用数据发现模块，负责 query normalizer、repository discovery、候选 manifest、validity、task readiness、value scoring、diversity、Batch handoff 等。

```text
src/agent/ai_ready/
```

AI-ready 训练表导出与数据科学 agent 模块，包括 RT、fragment intensity、PSM scoring、de novo、PTM de novo、chimeric exporters，以及 recipe、leakage、curation、model-loop、agentic recovery。

```text
src/agent/repositories/
```

PRIDE / MassIVE / iProX repository adapter 和 smoke 支持。

```text
src/agent/decision/dda.py
```

DDA/DIA 相关判定逻辑。当前策略是：纯 DIA/SWATH/PRM/SRM/MRM 与 DDA 目标冲突时排除；同一 project 同时有 DDA 与 DIA 证据时进入 file-level review，明确 DDA 文件保留，明确 DIA 文件排除。

```text
src/agent/execution/
src/agent/msdt_converter/
```

原 agent workflow 执行、输出识别、MSDT Docker converter 调用和 partial-output 复用相关逻辑。

```text
tests/
```

单元测试和 targeted regression。当前测试覆盖 Web、Discovery、DDA 判定、repository adapter、AI-ready exporters、recipe、model-loop、recovery、harness 等。

```text
docs/
```

交接与复现文档。英文交接文档仍以 `README.md`、`docs/HANDOFF.md`、`docs/README_reproduction.md`、`docs/final_agent_capability_report.md` 为主；本文件是中文总交接说明。

## 4. 主要功能说明

### 4.1 General Discovery

默认入口是：

```text
Discovery target: General data search
```

它支持任意自然语言蛋白质组数据检索需求，例如：

```text
human HLA immunopeptidomics DDA data
drug treatment kinase inhibitor DDA proteomics
disease cohort proteomics
cell line proteomics
PTM-enriched phospho/acetyl/glyco data
```

Discovery 输出 candidate manifest，并按以下状态分层：

```text
valid
weak_keep
needs_review
exclude
```

当前筛选逻辑不是简单关键词匹配，而是：

```text
自然语言 / 表单目标
-> query terms / ontology normalizer
-> project-level metadata scoring
-> file listing / SDRF / file-level feature extraction
-> validity / task readiness / data value
-> diversity tie-break
-> manifest / Batch handoff
```

### 4.2 Task readiness / Data value

系统会根据下游任务判断数据是否适合构建训练表。当前支持的 task readiness / AI-ready Build 任务为：

```text
rt_prediction
fragment_intensity_prediction
psm_scoring
denovo
ptm_denovo
chimeric_interpretation
```

关键口径：

- 如果用户明确要求 human，则 species 作为偏好或约束，不再因为“非 human 物种多样性”额外加分。
- Diversity 不只包括物种，也包括 project、repository、instrument family、fragmentation method、LC gradient、PTM / labeling strategy 等。
- TMT / iTRAQ 是 `weak-but-allowed`，不是默认排除。
- `PTM-enriched data` 是 discovery target；`ptm_denovo` 是下游 AI-ready task，二者不混用。
- 当 `Discovery target = PTM-enriched data` 时，Web 的 `PTM type` 支持多选。

### 4.3 Repository 支持

当前 repository 成熟度：

```text
PRIDE: online-first 主路径，当前最成熟
MassIVE: adapter / discovery v1 / smoke path 可用，但仍需更多真实项目验证
iProX: index-first，需要先 refresh public JSONL index，再基于本地 index 检索
```

iProX 当前不是实时在线全文检索。如果没有 index，预期 blocker 是：

```text
iprox_index_missing
```

### 4.4 Batch / full / partial-output recovery

Discovery 候选可以 handoff 到 Batch，并进入：

```text
parameters
prepare
full
```

Web full workflow 可以区分：

```text
clean full completed
failed with usable partial outputs
blocked / review case
failed without usable outputs
```

对于 full 失败但有可用中间产物的情况，AI-ready Build 可以复用 partial outputs，避免整体失败时浪费已生成的搜索结果。

### 4.5 AI-ready Build

AI-ready Build 可以从以下来源构建训练表：

```text
已有 agent run 目录
已有 batch run / item 目录
本地搜索结果文件
已有 AI-ready batch output
usable partial outputs
```

如果缺少必要输入，例如 search result、peaklist、MGF、MSDT 或 raw spectrum parquet，系统会输出 blocker，不会伪造训练标签。

### 4.6 Recipe / Split / Leakage / Hard benchmark / Curation

`make-dataset-recipe` 会基于 AI-ready Build 输出生成：

```text
dataset recipe
split plan
leakage risk report
hard benchmark manifest
counterfactual benchmark manifest
evidence graph
curation queue
```

当前 v3 smoke 使用 `file_disjoint` split，并完成 leakage check。

### 4.7 Model-loop smoke

`run-dataset-model-loop` 当前是 dry-run / smoke 级别，不是真实模型训练。它会读取 recipe 和指标 schema，生成：

```text
model-loop smoke report
failure mode summary
gap plan
agent expansion suggestions
```

## 5. 运行环境

### 5.1 推荐环境

推荐使用 Docker：

```text
Docker image: pride-agent-all-in-one-local:latest
Container working directory: /app
Web port: 8000
```

本地 Python 调用时，需保证依赖安装完整，并让 Python 能找到 `src/`。当前 `pyproject.toml` 声明包名为：

```text
pride-ai-ready-agent
```

开发测试配置中使用：

```text
pythonpath = ["src"]
```

### 5.2 环境变量

复制 `.env.example` 为 `.env` 后按需填写。`.env` 用于当前运行环境配置，接手方可根据 AI station、Docker 或本地环境重新配置。

常用环境变量：

```text
AGENT_LLM_API_KEY
DEEPSEEK_API_KEY
AGENT_LLM_BASE_URL
AGENT_LLM_MODEL
AGENT_LLM_TIMEOUT
AGENT_MAX_CONCURRENT_TASKS
AGENT_SEARCH_THREADS
AGENT_RESULT_RETENTION_SECONDS
AGENT_WEB_FULL_WORKFLOW_ENABLED
AGENT_PRIDE_CACHE_DIR
AGENT_IPROX_INDEX_XLSX
AGENT_CONTAINER_APP_DIR
AGENT_CONTAINER_RUNS_DIR
AGENT_HOST_APP_DIR
AGENT_HOST_RUNS_DIR
AGENT_MSDT_DOCKER_TIMEOUT_SECONDS
AGENT_MSDT_DOCKER_IDLE_TIMEOUT_SECONDS
AGENT_MSDT_ABORT_ON_LOW_PSM
```

关键说明：

- `AGENT_WEB_FULL_WORKFLOW_ENABLED=1` 才开放 Web full workflow。
- `AGENT_RESULT_RETENTION_SECONDS` 默认 1800 秒，普通 run 可能被自动清理。
- 需要长期保留的目录应放到 protected 目录，或放置 `.agent_keep`。
- `AGENT_HOST_RUNS_DIR` 与 `AGENT_CONTAINER_RUNS_DIR` 用于 nested Docker 路径映射，跑 MSDT converter 时很重要。

## 6. 输入文件与输出文件

### 6.1 Discovery 输入

可以来自 Web 表单或 CLI 参数：

```text
natural-language prompt
discovery target
task readiness
species / species policy
repository: pride / massive / iprox / auto / local
query terms
PTM types
local data directory
use LLM query planning
max candidates
```

### 6.2 Discovery 输出

典型输出位于：

```text
runs/discovery/
runs/discovery_memory/
runs/discovery_handoff/
```

常见文件类型：

```text
dataset_manifest.csv / json
quality_report.json
repository_audit.json
discovery_pipeline_handoff.json
batch_payload.json
discovery_task_build_plan.json
```

### 6.3 Batch / workflow 输入

Batch 输入来自 Discovery handoff 或手动文件列表：

```text
project accession
repository
file names / file URLs
run mode: parameters / prepare / full
API key for LLM-assisted parameter inference
```

### 6.4 Batch / workflow 输出

典型目录：

```text
runs/_batches/<batch_id>/
runs/_batches/<batch_id>/items/<item_id>/
```

常见文件：

```text
benchmark_results.xlsx
metadata.json
decision_trace.json
task_state.json
review_queue.json
workflow outputs
partial outputs
```

### 6.5 AI-ready Build 输入

可接受输入包括：

```text
agent run directory
batch item directory
psm.tsv / peptide.tsv
pin
MGF / peaklist
MSDT parquet
rawspectrum parquet
discovery task build plan
```

### 6.6 AI-ready Build 输出

典型目录：

```text
runs/ai_ready_builds/<build_id>/
```

常见文件：

```text
ai_ready_summary.json
task_table.parquet / csv
blockers.json
input_profile.json
validation_report.json
```

### 6.7 Recipe / model-loop 输出

典型目录：

```text
runs/ai_ready_builds/dataset_recipe_<id>/
runs/model_loop/model_loop_<id>/
```

常见文件：

```text
dataset_recipe.json
split_plan.json
leakage_risk_report.json
hard_benchmark_manifest.json
counterfactual_benchmark_manifest.json
evidence_graph.json
curation_queue.json
model_loop_report.json
gap_plan.json
```

## 7. 关键参数说明

### 7.1 Discovery target

推荐默认使用：

```text
General data search
```

仅当目标本身是寻找 PTM 富集数据时使用：

```text
PTM-enriched data
```

### 7.2 Task readiness

表示下游希望构建或评估的任务类型，例如：

```text
rt_prediction
fragment_intensity_prediction
psm_scoring
denovo
ptm_denovo
chimeric_interpretation
```

### 7.3 Species policy

默认开放。如果用户写了 human / mouse 等物种，则该物种进入约束或偏好，不应再用其他物种多样性额外加分。

### 7.4 Acquisition / DDA

如果目标要求 DDA：

- 明确 DDA 文件可以保留。
- 明确 DIA/SWATH/PRM/SRM/MRM 文件应排除。
- 同一个 project 同时有 DDA 和 DIA 证据时，project 进入 mixed acquisition review；需要 file-level 判断后再 handoff。

### 7.5 PTM types

当 Discovery target 是 `PTM-enriched data` 时，PTM type 可多选，例如：

```text
phospho
acetyl
ubiquitin / GlyGly
glyco
methyl
```

### 7.6 Batch run mode

```text
parameters: 只做参数推断和记录，不下载大文件，不产出训练表
prepare: 做准备阶段
full: 尝试完整 workflow，可能下载和转换文件，成本较高
```

### 7.7 Protected cleanup

Web cleanup 默认会清理普通过期 run。需要保留的目录应满足至少一项：

```text
位于配置的 protected result dirs 中
目录下含有 .agent_keep
是 _batches 等特殊保留目录
```

## 8. Web 使用步骤

启动 Web：

```powershell
docker compose up -d web
```

打开：

```text
http://127.0.0.1:8000
```

健康检查：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/health
```

### 8.1 API Configuration

如果要使用 LLM query planning 或 Batch parameters，需要先在页面 API Configuration 区域填写 API key。API key 仅用于当前运行会话或环境变量配置。

如果只做 deterministic discovery，可关闭 LLM query planning。

### 8.2 Discovery

推荐流程：

1. 选择 `Discovery target = General data search`。
2. 选择下游 `Task readiness`。
3. 如果有明确物种需求，再填写 species；否则保持开放。
4. 如果是 PTM discovery，选择 `PTM-enriched data` 并多选 PTM types。
5. 选择 repository 或保持 auto。
6. 点击开始检索。
7. 查看 Discovery events 和候选表。

候选表默认按：

```text
valid
weak_keep
needs_review
exclude
```

分组显示。通常默认选择 `valid + weak_keep`，再人工取消不合适候选。

### 8.3 Send selected to Batch

Discovery 完成后：

1. 勾选候选。
2. 点击发送到 Batch。
3. Web 会生成 Batch payload，并预填 Batch 区域。
4. 如果没有 API key，Batch 会提示需要先填写 key，而不是假装卡住。

### 8.4 Batch parameters / full

建议常规验收优先使用：

```text
parameters
```

这可以验证 handoff 和参数推断，不会下载大 RAW。

只有在文件足够小、Docker 和磁盘空间确认可用时，才开启：

```text
full
```

### 8.5 AI-ready Build

当前 Web AI-ready Build UI 是三段式：

```text
Input source
Task and build
Results and next step
```

常用路线：

1. 在 Input source 选择 `From Batch run` 或已有 agent run。
2. 填入 batch/item/run 目录。
3. 在 Task and build 选择任务，例如 `rt_prediction` 或 `denovo`。
4. 点击 Build / validate。
5. 如果输出 completed，可继续生成 recipe/split。
6. 如果 blocked，查看 blockers；parameters-only 候选仍应按 review / blocked 处理，不能作为训练表。

低频调试工具放在 Advanced 中，例如 locator、repository smoke、manual TSV/MGF、external metrics adapter 等。

### 8.6 Recipe / Split / Model-loop

AI-ready Build 完成后：

1. 使用生成的 output dir 作为 recipe 输入。
2. 生成 recipe/split/leakage/hard benchmark/curation queue。
3. 使用 recipe dir 运行 model-loop smoke。
4. 查看或下载报告，用于交接复查。

## 9. CLI 常用步骤

推荐在 Docker 容器内运行：

```powershell
docker compose exec web python -m agent.cli check-runtime
```

### 9.1 iProX index

```powershell
docker compose exec web python -m agent.cli refresh-iprox-index --help
```

### 9.2 Discovery

示例：

```powershell
docker compose exec web python -m agent.cli discover-dataset `
  --goal general `
  --repository pride `
  --species human `
  --query-term HLA `
  --query-term immunopeptidomics `
  --output-dir runs/discovery/<run_id>
```

### 9.3 Batch AI-ready validation

示例：

```powershell
docker compose exec web python -m agent.cli validate-agent-runs-ai-ready-batch `
  --agent-run-dir runs/_protected_benchmark_20260624_4_5_sample_pool/agent_runs/PXD079076_20190404_TMT10_rebuild_20260624 `
  --agent-run-dir runs/_protected_benchmark_20260624_4_5_sample_pool/agent_runs/sk_BNWTTS2_C6_160307__20260611-174303__9395af9f `
  --task-type rt_prediction `
  --task-type denovo `
  --output-dir runs/ai_ready_builds/mini_e2e_batch_<new_id>
```

### 9.4 Recipe

```powershell
docker compose exec web python -m agent.cli make-dataset-recipe `
  --batch-dir runs/ai_ready_builds/mini_e2e_batch_<new_id> `
  --output-dir runs/ai_ready_builds/dataset_recipe_<new_id> `
  --split-strategy auto
```

### 9.5 Model-loop smoke

```powershell
docker compose exec web python -m agent.cli run-dataset-model-loop `
  --recipe-dir runs/ai_ready_builds/dataset_recipe_<new_id> `
  --task-type rt_prediction `
  --mode smoke `
  --output-dir runs/model_loop/model_loop_<new_id>
```

## 10. 当前受保护 v3 benchmark

受保护证据池：

```text
runs/_protected_benchmark_20260624_4_5_sample_pool
```

当前覆盖 5 个真实候选：

```text
PXD079076: clean completed / TMT10 / MSDT + AI-ready parquet
PXD027067: usable partial output / partial-output recovery
PXD079072: blocked / spectrum or export mismatch
PXD074954: drug treatment / phospho discovery parameters evidence
PXD077080: HLA / immunopeptidomics discovery parameters evidence
```

当前 v3 结果：

```text
completed candidates: 2
blocked/review candidates: 3
selected task outputs: 4
excluded task outputs: 6
recipe status: ready
split strategy: file_disjoint
split counts: train 2 / val 2
leakage status: passed
hard benchmark rows: 10
curation queue rows: 10
model-loop status: completed
RT rows scanned: 636
smoke score: 0.7165
```

详细报告：

```text
runs/_protected_benchmark_20260624_4_5_sample_pool/reports/benchmark_sample_pool_4_5.md
runs/_protected_benchmark_20260624_4_5_sample_pool/reports/final_delivery_checklist.md
runs/_protected_benchmark_20260624_4_5_sample_pool/reports/web_smoke/web_ui_smoke_report.md
```

## 11. 验证命令

快速前端模板测试：

```powershell
docker compose exec web python -m pytest tests/test_frontend_template.py
```

Web / recipe / model-loop targeted regression：

```powershell
docker compose exec web python -m pytest `
  tests/test_dataset_recipe.py `
  tests/test_model_loop.py `
  tests/test_web_ai_ready.py `
  tests/test_frontend_template.py
```

Recovery / harness / data scientist loop：

```powershell
docker compose exec web python -m pytest `
  tests/test_agentic_recovery.py `
  tests/test_mini_e2e.py `
  tests/test_mini_e2e_batch.py `
  tests/test_agent_recovery.py `
  tests/test_agent_harness.py `
  tests/test_data_scientist_loop.py `
  tests/test_guidance_alignment.py
```

Protected cleanup 测试：

```powershell
docker compose exec web python -m pytest `
  tests/test_web.py::test_cleanup_results_preserves_protected_validation_directories `
  tests/test_web.py::test_cleanup_results_removes_expired_process_directories `
  tests/test_web.py::test_cleanup_results_keeps_only_four_latest_downloadable_runs
```

## 12. 运行数据与清理规则

当前项目包含两类内容：

```text
源码、测试、配置模板、文档
运行证据、benchmark、Batch 输出、AI-ready build、recipe/model-loop 输出
```

源码和文档用于继续开发；运行证据主要保留在：

```text
runs/_protected_benchmark_20260624_4_5_sample_pool
```

Web cleanup 默认会清理普通 run 目录。长期保留的交接证据需要满足以下条件之一：

```text
目录含有 .agent_keep
位于 AGENT_PROTECTED_RESULT_DIRS 配置的保护目录中
属于 _batches 等特殊运行目录
```

接手后如需新增 benchmark 或复现证据，建议沿用 protected 目录或新增 `.agent_keep`，避免 1800 秒默认清理机制影响复查。

## 13. 当前无法完成或需要后续跟进

### 13.1 大规模一键复现

当前只是小中规模 v1 smoke release。大规模一键复现还需要固定小样本 cache、下载策略、更多 repository 兼容性和更完整的错误恢复。

### 13.2 真实模型训练闭环

model-loop 当前是 dry-run / metric-schema smoke。后续需要接入真实 XuanjiNovo / MassNet command adapter，并在用户明确提供配置时启动训练。

### 13.3 MassIVE / iProX 成熟度

PRIDE 是当前主路径。MassIVE / iProX 已有 adapter 和 smoke，但 metadata richness、file listing、download URL、handoff、parameters/prepare/full 仍需更多真实项目验证。

### 13.4 RAW / WIFF-like 兼容

RAW 转换、Windows Docker nested path、WIFF-like mzML nativeID / scan mismatch 仍是独立后续工作，不作为当前交付阻断项。

### 13.5 Readiness / value 校准

当前已有 readiness/value/scoring，但仍需要更多真实 parquet 和人工 review 校准，尤其是：

```text
证据强但 AI-ready rows 暂时为 0
project-level evidence 强但 file-level evidence 缺失
parameters-only 候选是否进入 review
mixed DDA/DIA project 的 file-level 过滤
```

### 13.6 Web 状态验收

接手后如需确认 Web 当前状态，建议手动走一遍最小验收路径：

```text
Discovery
-> Send selected to Batch
-> Batch parameters
-> AI-ready Build
-> Recipe / split
-> Model-loop smoke
```

如需留存交接证据，可截取最新页面截图，并记录当时的 Docker 状态、浏览器地址和输出目录。

## 14. 推荐交接阅读顺序

```text
README.md
docs/PROJECT_HANDOFF_CN.md
docs/final_agent_capability_report.md
docs/README_reproduction.md
docs/known_limitations_and_next_stage.md
docs/HANDOFF.md
```

如果是接手开发，先读：

```text
docs/module-architecture.md
docs/model-adapter-metrics.md
tests/
src/agent/cli.py
src/agent/web/app.py
src/agent/discovery/
src/agent/ai_ready/
```

## 15. 一句话总结

AI-ready Data Agent 当前已经完成“通用数据发现 -> 任务适配与数据价值判断 -> Batch/full/partial evidence -> AI-ready 训练表 -> recipe/split/leakage/hard benchmark/curation -> model-loop smoke”的小中规模真实数据闭环；下一阶段应重点推进真实模型训练 adapter、多库真实项目验证、RAW/WIFF 兼容和更稳定的复现包。
