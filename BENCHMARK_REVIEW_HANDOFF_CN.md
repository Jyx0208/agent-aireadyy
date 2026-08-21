# Benchmark Review 评分系统实施交接

> 更新时间：2026-07-16  
> 状态：实施中，存在未提交修改；不要归档任务，也不要从头重写。

## 1. 接手位置

请在以下现有隔离工作树中继续：

```text
E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning
```

当前分支：

```text
worktree-benchmark-review-planning
```

不要切换到主项目目录，也不要新建平行实现。此工作树已有大量尚未提交的有效成果。

## 2. 用户最终目标

实现一套一键式、多模型专家 Agent 评审系统：

```text
自然语言 Prompt
→ 解析任务及硬约束
→ 自动发现候选项目
→ 收集项目材料和原始页面证据
→ 去重、hard gate、Evidence Package
→ 注册评审池
→ 自动选择异构专家并开始评审
→ 两票共识或触发第三票
```

产品约束：

- 用户不应手写 scenario JSON。
- 用户不应手动运行 CLI。
- 用户不应上传 pool JSON。
- 用户不应在建池后再次手动启动 Judge Job。
- 当前没有真实人类专家，首期使用不同底层模型构成专家 Agent。
- 不允许同一底层模型既产生候选又给自己的结果评分。
- 模型专家结果不能写入 `human_grades`，也不能标为 `human_verified`。
- hard-gate `fail` 不能被软分或专家平均分抵消。
- 无法验证底层模型独立性时，最多形成 `model_expert_provisional`，不能宣称正式共识。

完整需求和设计见：

```text
.trellis/tasks/07-16-benchmark-review-scoring/prd.md
.trellis/tasks/07-16-benchmark-review-scoring/design.md
.trellis/tasks/07-16-benchmark-review-scoring/implement.md
.trellis/tasks/07-16-benchmark-review-scoring/task.json
```

## 3. 当前任务状态

任务列表：

- #5 `验证现有评审实现基线`：已完成。
- #6 `实现一键评审池构建`：进行中。
- #7 `实现异构专家评审共识`：尚未开始。

当前优先级必须是：先完成 #6，再做 #7。

不要运行 `/trellis:finish-work`：当前存在属于本任务的未提交代码，且完整任务尚未完成。

## 4. 已完成的主要工作

### 4.1 规划和语义

已完成多模型专家评审与一键建池的 PRD、设计和实施计划，明确了：

- 五类信号分离：hard gate、heuristic、model expert、human verification、未来校准概率。
- 来源等级：
  - `model_expert_provisional`
  - `model_expert_consensus`
  - `needs_adjudication`
  - `human_verified`
  - `legacy_unverified`
- 两位异构专家独立首评，必要时第三位专家。
- 专家 Evidence Package、调查状态、证据引用和网络安全要求。
- Prompt-only 的 `build_only` / `build_and_review` 工作流。
- `pool_ready` 是持久化边界；评审启动失败不得重新 discovery。

### 4.2 现有专家评审基础设施

当前工作树已有或增强了：

- 文件系统评审池 registry。
- 专家评分写入与导出。
- impact 重算。
- 多 LLM profile 配置。
- Judge Job 的启动、进度、取消、恢复和错误处理。
- 开发者模式评审 UI。
- expert/developer/test 模式和服务端字段盲化。

相关文件：

```text
src/agent/web/expert_review/grading.py
src/agent/web/expert_review/impact.py
src/agent/web/expert_review/jobs.py
src/agent/web/expert_review/openai_judge.py
src/agent/web/expert_review/pool_registry.py
src/agent/web/llm_config_store.py
src/agent/web/app.py
src/agent/web/templates/benchmark_review.html
```

### 4.3 一键建池初版

新增：

```text
src/agent/web/expert_review/pool_builder.py
src/agent/web/expert_review/pool_builds.py
tests/test_expert_pool_builder.py
tests/test_expert_pool_builds.py
```

`ExpertPoolBuildManager` 当前已具备：

- Prompt discovery request。
- `build_only` 和 `build_and_review`。
- `default/v1` preset 校验。
- idempotency key。
- request credential 仅内存传递，不落盘。
- 递归 secret stripping。
- 原子 JSON checkpoint。
- queued/discovering 状态恢复。
- 进程内重复 worker 防护。
- discovery cancel。
- `pool_ready` 持久化边界。
- review handoff 失败后 `reconcile_review()`，且不重新 discovery。
- duplicate candidate ID 校验。

Web API 已增加两组别名：

```text
GET  /api/benchmark-review/builds
POST /api/benchmark-review/builds
GET  /api/benchmark-review/builds/{build_id}
POST /api/benchmark-review/builds/{build_id}/cancel
POST /api/benchmark-review/builds/{build_id}/reconcile

GET  /api/expert-review/pool-builds
POST /api/expert-review/pool-builds
GET  /api/expert-review/pool-builds/{build_id}
POST /api/expert-review/pool-builds/{build_id}/cancel
POST /api/expert-review/pool-builds/{build_id}/reconcile
```

UI 已增加：

- Prompt 输入框。
- “构建并评审”与“仅构建评审池”。
- 构建状态列表和轮询。
- pool 创建后打开评审池。
- developer-only 显示控制。

## 5. 当前最重要的未完成问题

### 5.1 `pool_builds.py` 绕过了现有安全 builder/private key 路径

这是接手后的第一优先级。

当前 `src/agent/web/expert_review/pool_builds.py` 在 `_register_pool()` 中使用：

```python
pool = self._pool_from_discovery(discovery)
pool_record = self.registry.import_pool(pool, label=label)
```

这会绕过：

```python
build_blinded_pool_from_discovery(...)
registry.import_generated_pool(..., private_key=...)
```

因此可能造成：

- 原始用户 Prompt 未进入 pool task；
- 固定字符串 `Discovery candidate review` 代替真实 Prompt；
- private candidate identity/provenance map 丢失；
- 没有写入 `private/judgment.key.json`；
- 与已有 `pool_builder.py` 的稳定 candidate ID 和 accession 去重逻辑分叉。

必须保留 `pool_builds.py` 现有的凭据脱敏、原子 checkpoint、恢复、幂等和 duplicate validation，同时恢复调用现有 builder：

```python
pool, private_key = build_blinded_pool_from_discovery(
    discovery_record,
    prompt=original_prompt,
    build_id=build_id,
    visible_constraints=visible_constraints,
)

pool = self._validated_pool(pool)
pool_record = self.registry.import_generated_pool(
    pool,
    private_key=private_key,
    label=label,
)
```

原始 Prompt 可从持久化后的安全 `record["discovery_request"]["prompt"]` 读取。不要从 transient credential request 取 Prompt 作为唯一来源。

`visible_constraints` 应从安全 discovery request 或版本化 preset 中提取，只允许 builder 已定义的公开字段。

### 5.2 现有 builder 的盲化规则需要后续升级

`src/agent/web/expert_review/pool_builder.py` 当前说明：

```text
Judge visible repository metadata only. Candidate origin is intentionally hidden.
```

未来专家应能看到 canonical project URL 和 Evidence Package 引用，并可受控调查原项目页面。但在完成 #6 的最小闭环时，应先恢复 builder/private key 路径；不要同时大规模重写 Evidence Package，避免扩大变更面。

### 5.3 API 测试 mock 仍匹配旧接口

`tests/test_expert_review_api.py` 中 `test_pool_build_api_requires_only_prompt_and_defaults_to_review` 仍期望旧调用：

```python
return {"build_id": "build-1", "status": "discovering"}, False
```

以及旧参数：

```text
prompt
discovery_body
client_request_id
```

当前实际 API 使用：

```python
start_build(
    discovery_request=...,
    action=...,
    label=...,
    preset_id=...,
    review=...,
    idempotency_key=...,
)
```

测试 mock 应返回单个 build dict，并断言新参数。

### 5.4 FastAPI 测试环境问题

基础 Python 有 pytest，但缺 `fastapi`。Conda `unagi` 有 fastapi，但没有 pytest。混合两个环境的 site-packages 会导致：

```text
ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
```

不要继续混合环境。应查找项目正式环境或安装一致的开发依赖后运行 API tests。若暂时无法解决，必须明确记录“未运行”，不能声称通过。

## 6. 建议的下一步，按顺序执行

### Step 1：修复建池注册路径

修改：

```text
src/agent/web/expert_review/pool_builds.py
```

目标：

1. import `build_blinded_pool_from_discovery`。
2. 从 discovery response 中提取 record。
3. 从安全 build record 读取原始 Prompt。
4. 调用 builder 生成 `pool, private_key`。
5. 保留 `_validated_pool()` 校验。
6. 使用 `import_generated_pool()`。
7. 确保 review handoff 失败后仍复用同一 pool。

如果 discovery 显式返回现成 pool，需要谨慎处理：只有能同时取得或构建 private key 时才能走 generated-pool 路径；不要为了兼容现成 pool 而静默丢弃 identity map。

### Step 2：补测试

修改 `tests/test_expert_pool_builds.py`，至少覆盖：

- 原始 Prompt 确实进入 `tasks[*].visible_prompt`。
- pool candidate ID 使用稳定 builder 生成规则。
- `private/judgment.key.json` 确实存在。
- private key 包含 candidate 到 accession 的映射。
- expert-safe blinded pool 不包含 `project_accession`、generator、runtime 或 secret。
- idempotent replay 不重复 discovery。
- review handoff 失败后 reconcile 不重建 pool。
- duplicate candidate ID/invalid pool 仍在注册前失败。

注意：当前 `_completed_discovery()` fixture 只有 `projects`，没有 `files`；builder 可以接受这种输入，但建议增加最小文件 fixture 验证 bundle summary。

### Step 3：修 API 测试

修改：

```text
tests/test_expert_review_api.py
```

使其匹配当前 manager 接口和返回值。

### Step 4：验证任务 #6

已确认最近一次运行：

```text
python -m py_compile src/agent/web/app.py src/agent/web/expert_review/pool_builds.py
python -m pytest tests/test_expert_pool_builds.py tests/test_benchmark_review_template.py -q
```

结果：

```text
7 passed in 0.16s
```

但这是修复 builder/private key 路径之前的结果。完成修改后必须重新运行：

```powershell
python -m py_compile src/agent/web/app.py src/agent/web/expert_review/pool_builder.py src/agent/web/expert_review/pool_builds.py
python -m pytest tests/test_expert_pool_builder.py tests/test_expert_pool_builds.py tests/test_benchmark_review_template.py -q
```

然后在依赖完整环境运行：

```powershell
python -m pytest tests/test_expert_review_api.py -q
```

再运行 expert review 回归集：

```powershell
python -m pytest tests/test_expert_grading.py tests/test_expert_impact.py tests/test_expert_jobs.py tests/test_expert_pool_registry.py tests/test_expert_pool_builder.py tests/test_expert_pool_builds.py tests/test_benchmark_review_template.py -q
```

只有测试通过且 private key/原始 Prompt 闭环完成后，才可将任务 #6 标记完成。

### Step 5：进入任务 #7

任务 #7 需要实现：

- `ExpertModelProfile` registry。
- provider/requested model/resolved model/family/endpoint identity。
- verified/unverified independence policy。
- 与候选生成模型 family 冲突检查。
- 两个跨 family 首评专家。
- hard 分歧、grade 差异、证据冲突、低置信度触发第三专家。
- deterministic consensus。
- `model_expert_provisional`、`model_expert_consensus`、`needs_adjudication`。
- 模型票不得写入 `human_grades`。
- Claude provider 必须使用官方 `anthropic` Python SDK，不使用 OpenAI-compatible shim。
- Claude 默认模型：`claude-opus-4-8`；复杂判断使用 adaptive thinking，长调用使用 streaming，结构化输出使用官方能力。

不要把当前 `independent_model: bool` 当作真实独立性证明；它最终必须由模型身份策略替代。

## 7. 当前 Git 状态

最近提交：

```text
3b0f72f feat: expert scoring UI, test-mode impact, and agent judge jobs
d4c4f0a feat: expert review pool registry and multi-profile LLM store
37f17d1 docs: select expert review implementation baseline
4c6f3d4 docs: add one-prompt review pool builder
96a8d47 docs: plan multi-model expert review loop
```

当前存在属于本任务的未提交修改：

```text
M  src/agent/discovery/blind_judging.py
M  src/agent/web/app.py
M  src/agent/web/expert_review/__init__.py
M  src/agent/web/expert_review/grading.py
M  src/agent/web/expert_review/impact.py
M  src/agent/web/expert_review/jobs.py
M  src/agent/web/expert_review/openai_judge.py
M  src/agent/web/expert_review/pool_registry.py
M  src/agent/web/llm_config_store.py
M  src/agent/web/templates/benchmark_review.html
M  tests/test_benchmark_review_template.py
M  tests/test_expert_grading.py
M  tests/test_expert_impact.py
M  tests/test_expert_jobs.py
M  tests/test_expert_pool_registry.py
M  tests/test_expert_review_api.py
M  tests/test_llm_config_store.py
?? src/agent/web/expert_review/pool_builder.py
?? src/agent/web/expert_review/pool_builds.py
?? tests/test_expert_pool_builder.py
?? tests/test_expert_pool_builds.py
?? .trellis/tasks/07-16-benchmark-review-scoring/check.jsonl
?? .trellis/tasks/07-16-benchmark-review-scoring/implement.jsonl
?? .trellis/tasks/07-16-benchmark-review-scoring/task.json
```

这些修改不能丢弃、不能整文件覆盖。尤其注意：

- `src/agent/web/app.py` 变更较大。
- `src/agent/web/templates/benchmark_review.html` 变更非常大。
- 修改前先读目标段落并做增量编辑。
- 不要使用 `git reset --hard`、`git checkout -- <file>` 或覆盖式复制。

当前 diff 大约为：

```text
17 tracked files changed, 2822 insertions(+), 793 deletions(-)
```

未计入新增文件。

## 8. 重要实现约束

### 凭据安全

- API key、Authorization、token、password、secret 不得落盘到 build JSON、pool、job、Prompt 或导出。
- `pool_builds.py` 的 transient request + `_safe_value()` 机制应保留。
- 外部异常文本必须经过 `_safe_error()`。

### 幂等和恢复

- 同一 idempotency key 返回同一 build。
- discovery 已完成并注册 pool 后，review 失败只重试 handoff。
- 不得因 review 服务失败重新产生 discovery 成本。
- 原子 checkpoint 使用临时文件 + `os.replace()`，应保留。

### 评审来源语义

- 模型专家输出绝不能进入 `human_grades`。
- `human_verified` 仅允许未来真实人类流程写入。
- 旧 reviewed JSON 无明确来源时应标为 `legacy_unverified`。
- 当前旧代码仍有相关语义债务，计划在后续 Phase 6 修复。

### Hard gate

- `fail` 不可被 grade 或 soft score 覆盖。
- `unknown/review` 必须保留不确定性。
- 不能把启发式 readiness/value score 宣称为概率。

## 9. 已知环境和流程问题

- 当前工作树缺少 `.trellis/scripts/get_context.py`，因此 `/trellis:finish-work` 的 survey 命令无法执行。
- 不要因为脚本缺失而手工归档任务；任务仍未完成。
- 之前有子 Agent 错误地修改过主项目目录而非 active worktree。接手 Agent 必须确认每次读写路径都在上述工作树中。
- 当前工作树是会话开始前已存在的工作树，不是本会话自动创建。提交、切分支或 push 前应确认用户授权和仓库状态。

## 10. Matt Pocock skills 插件状态（与业务实现独立）

用户还要求安装：

```text
https://github.com/mattpocock/skills
```

已经通过 Claude Code plugin 安装并启用：

```text
mattpocock-skills@mattpocock
Version: 1.2.0
Scope: user
Status: enabled
```

本地插件缓存存在于：

```text
C:\Users\28425\.claude\plugins\cache\mattpocock\mattpocock-skills\1.2.0
```

但用户在当前会话里“没看到” slash command。原因很可能是当前会话在插件安装前已经启动，技能列表不会热更新。应让用户完全退出并重新进入 Claude Code，再尝试：

```text
/setup-matt-pocock-skills
```

不要重复安装。若重启后仍不可见，再检查 `claude plugin details mattpocock-skills@mattpocock` 和当前客户端版本/插件加载日志。

## 11. 接手 Agent 的首条建议指令

可以直接给接手 Agent 以下指令：

```text
请读取 BENCHMARK_REVIEW_HANDOFF_CN.md，以及
.trellis/tasks/07-16-benchmark-review-scoring/{prd,design,implement}.md。
在当前 worktree-benchmark-review-planning 工作树继续，不要切换目录、不要丢弃未提交修改、不要从头重写。
优先完成任务 #6：修复 pool_builds.py，使其恢复调用 build_blinded_pool_from_discovery() 和 import_generated_pool(private_key=...)，确保原始 Prompt 进入 pool task、private/judgment.key.json 被保存，同时保留凭据脱敏、原子 checkpoint、幂等、恢复及 review reconcile。随后修正测试并运行交接文件列出的验证命令。任务 #6 验证完成后再进入异构专家共识任务 #7。
```
