# 委员会分册：UI 体验与可选试跑清单

## 1. 目的与边界

本清单从当前真实状态出发：Wave 1–4/6、Authority peer audit、Wiring A/Continue 与 Wave B 议程接线已有离线验证；尚未完成的主要是生产 signer 运维、部分 capability adapter、完整依赖环境测试和真实端到端试跑。

本清单是**用户可选的手工验收脚本**，不是要求用户替项目补测试，也不是 PASS 的前置条件。本文没有声称已执行 live PRIDE/provider 真跑。业务成功定义始终只有一个：存在 Authority 签发并验证的 build-ready package，材料能进入数据集构建。候选、审查、判断合格、repair delta 和 Runner 返回都只是进展。

## 2. 前置与启动方式

### 2.1 工作树

```text
E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning
```

以下命令均从该目录开始。不要在别的 clone 上试，以免看到旧 UI 或旧 API schema。

### 2.2 方案一：Docker + 已构建前端（仓库文档已有，推荐先试）

```powershell
Set-Location 'E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning'
docker compose up -d web
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/health
```

浏览器打开：

```text
http://127.0.0.1:8000/benchmark-review
```

需确认：

- Docker Desktop/daemon 已启动，镜像依赖能正常安装；
- 容器使用的静态 bundle 与当前工作树前端源码一致；若页面明显不是 Carbon 对话页，应改用下面的 Vite 开发方式；
- live provider/PRIDE 所需配置已按本机约定注入，且不会把 key 写入日志或截图；
- 当前默认无生产 signer。未显式配置 signer 时，即使有很多候选，也应 fail-closed 为 progress/blocked。

停止：

```powershell
docker compose stop web
```

### 2.3 方案二：当前前端源码 + 本地后端（按真实脚本推导，需确认）

仓库 `vite.config.ts` 固定前端端口 `5174`，并把 `/api` 代理到 `127.0.0.1:8001`，因此双终端启动应为：

终端 A：

```powershell
Set-Location 'E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning'
.\.venv\Scripts\python.exe -m uvicorn agent.web.app:app --host 127.0.0.1 --port 8001
```

终端 B：

```powershell
Set-Location 'E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning\frontend\benchmark-review'
npm ci
npm run dev
```

浏览器打开：

```text
http://127.0.0.1:5174/benchmark-review/
```

需确认：

- `.venv` 已包含 `fastapi`、`uvicorn`、`typer`、`openai-agents` 等完整依赖；既有报告记录过部分本地解释器缺依赖；
- Node/npm 版本与 `package-lock.json` 兼容；若依赖已安装且不希望重装，可先尝试 `npm run dev`；
- provider 配置、PRIDE 网络和代理在本机可用；场景 C 的对话议程也可能需要模型 provider；
- 不要为了手工负例打开 `DISCOVERY_AUTHORITY_DEV_SIGN=1`。该开关仅供显式开发验证，不是生产 signer。

### 2.4 通用观察位置

每个场景都同时看四处：

1. **中间对话区**：Agent 当前问题、进度消息、折叠的“技术轨迹”。
2. **右侧“策略预览”**：阶段标签（待开始/对话澄清中/待确认策略/搜索中/已完成/失败）和“确认，开始搜”。窄屏先点“查看策略与运行状态”。
3. **右侧“当前数据发现”**：运行 status、searched/inspected/judgment-qualified/build-ready、阻塞原因，以及“查看审计/查看结果”。文件数只应在折叠的文件详情里承担 drill-down 作用。
4. **浏览器开发者工具 → Network**：找到 `POST /api/discovery/jobs` 和后续 `GET /api/discovery/jobs/{job_id}`；必要时另开：

   ```text
   http://127.0.0.1:<后端端口>/api/discovery/jobs/<job_id>?detail=1
   ```

   重点检查 `record.business_completion`：

   - `schema_version`（当前应为 `business-completion/v2`）；
   - `authority_source`；
   - `succeeded`、`status`、`package_kind`、`success_ui_allowed`；
   - `progress.candidate_projects/reviewed_projects/judgment_qualified_projects/build_ready_projects/build_ready_files/blocker_counts`；
   - `build_ready_package`、`limitations`；
   - 真成功还应由后端验证 issuance/package/Authority provenance，不能只看计数。

## 3. 完整试跑清单

### 场景 A：只浏览/找候选

目标：证明“找到数据”可作为有价值进展展示，但不冒充业务完成。

- [ ] 打开 Carbon 数据发现页，在中间聊天输入：

  ```text
  我现在只想浏览 PRIDE，找一批人源免疫肽候选项目；先找到候选就停，不要宣称已经可用于数据集构建。
  ```

- [ ] 点发送。看中间对话是否围绕目标、范围或数量澄清；看右侧“策略预览”的“下游任务”是否接近“先只找数据、任务未定”，“本次终点”是否接近“找到候选数据就停”。
- [ ] 如果还缺数量/范围，可在对话里回答“精选约 20 个，物种优先人源”；也可点右侧“补齐稳妥默认”。
- [ ] 只有阶段变成“待确认策略”后，点右侧“确认，开始搜”。确认前 Network 不应出现 `POST /api/discovery/jobs`。
- [ ] 运行时看中间进度与右侧“当前数据发现”：searched 可增长；inspected、judgment-qualified 可为 0 或后续增长；build-ready 应保持 0，除非真实 Authority package 已生成。
- [ ] 展开“技术轨迹”，确认 search/inspect 是运行事实；即使出现 attempt finished，也不能出现未经 Authority 判定的成功宣称。
- [ ] 在 Network 打开最终 job 响应，检查 `record.business_completion`。

预期：

- `progress.candidate_projects > 0` 可以显示“有进展”；
- 只找到候选时，`succeeded=false`、`package_kind=progress`、`build_ready_projects/files=0`；终态可以是 `blocked_with_progress` 或其它诚实 progress/blocked 状态；
- 右侧不得出现绿色“已完成”或“发现结果已就绪”；应继续显示进展、限制或质量未通过；
- candidate file count 只能辅助 drill-down，不能把状态推成成功。

异常判据：候选数一大于 0 就出现绿色“已完成”；server `completed` 被 UI 原样画绿；或 selected/reviewed/file count 被当作 build-ready。

### 场景 B：材料不齐时跑发现

目标：验证真实 32/20/0 类语义——searched 和 judgment 有进展，但缺证据/文件时必须 blocked_with_progress。

- [ ] 保持生产 signer 未配置或 dev signer 关闭；新开/重置一次数据发现对话。
- [ ] 输入：

  ```text
  请找适合训练的人源免疫肽数据，要求样本级标签、可追溯文件证据以及可进入构建的文件清单。先找到并审查候选；材料不齐时不要猜，也不要说完成，请保留进展并列出阻塞原因。
  ```

- [ ] 点发送。完成必要澄清后，在“策略预览”核对硬条件没有被软偏好替代；点“确认，开始搜”。
- [ ] 搜索中观察 searched、inspected、judgment-qualified 分别增长。不要用候选文件数判断成功。
- [ ] 终态看右侧运行卡：应出现“质量未通过”、进展/审计入口或阻塞原因；策略阶段可能以红色“失败”表达 blocked，但绝不能绿色“已完成”。
- [ ] 点“查看审计”，核对 limitations/blockers 与实际缺口一致，例如 missing file evidence、missing labeling evidence、project-only evidence、hard unknown/conflict。
- [ ] 在 Network 检查：

  ```text
  record.business_completion.status = blocked_with_progress（有进展时）
  record.business_completion.succeeded = false
  record.business_completion.success_ui_allowed = false
  record.business_completion.package_kind = progress
  progress.build_ready_projects = 0
  progress.build_ready_files = 0
  ```

- [ ] 若日志含 `discovery_quality_repair_completed`，UI 只能显示“修复尝试结束，结果待审计”；若含 `repair_succeeded`/`build_ready_succeeded`，UI 也只能提示最终仍以 Authority decision 为准，不能由事件名画绿。

预期：有候选/审查/判断的数字和 blockers；无成功绿勾；可追溯到 `business_completion.limitations`。

异常判据：HTTP 200、Runner 返回、repair event、`job.status=completed`、文件数或 judgment-qualified 非零中的任一项单独触发绿色成功。

### 场景 C：对话议程（chimeric/训练类）

目标：验证接线后的 TaskProfile agenda 在搜索前先处理影响训练可行性的关键决策，并保持 Manager 单写者/动态单问题。

- [ ] 新开/重置对话，输入：

  ```text
  我要为 chimeric spectrum interpretation 准备训练数据。标签来源还没定，也没决定是否允许 relabel；先帮我把关键决策理清，不要开始搜索。
  ```

- [ ] 点发送。看中间对话和右侧“策略预览”：阶段应为“对话澄清中”，而不是搜索中；“确认，开始搜”应保持禁用；Network 不应出现 `POST /api/discovery/jobs`。
- [ ] 第一优先问题应围绕 `label_provenance` / `relabel_tolerance`（标签来源是否可追溯、是否允许重新标注），而不是先问 optional `labeling_strategy`，也不应恢复固定 Q1–Q10 问卷。
- [ ] 回答：

  ```text
  只接受公开可追溯的原始标签，不允许重新标注。
  ```

- [ ] 看策略卡是否只更新用户明确回答的字段，并转向下一个最高价值决策；每轮只应有一个 `next_decision`。
- [ ] 可选再试 open：回答“标签策略保持开放”。预期 open 被视为已解决，不重复追问同一决策。
- [ ] 可选对照：另开一次只输入“我只是浏览候选，不做训练”。预期 browse-only 不被训练类 acquisition/species/labeling 议程阻塞。

预期：关键标签可行性先于 optional labeling；确认前不访问 PRIDE；咨询性表达不应偷偷改策略；agenda ready 不等于 build-ready，更不能显示业务成功。

异常判据：直接开始搜索、同时抛出多个固定问题、先问次要 labeling、把 open 当 missing 反复追问、或把“议程已解决”画成“业务已完成”。

## 4. 正常与异常对照表

| 观察点 | 正常 | 异常/必须记录 |
| --- | --- | --- |
| 候选已找到，build-ready=0 | 显示 searched 进展；progress/blocked | 绿色“已完成”或“发现结果已就绪” |
| server status=`completed`，decision 失败 | UI 规范化为 blocked/progress；无绿勾 | 顶层策略卡仍显示绿色“已完成” |
| `blocked_with_progress` | 保留候选/审查/判断数字，并显示 blockers | 只显示笼统失败，进展消失；或反过来画成功 |
| legacy repair completed | “修复尝试结束，结果待审计” | “自主修复完成/通过交付” |
| 新 repair/build-ready success 事件 | 中性提示，最终以 Authority decision 为准 | 仅凭事件名显示成功 |
| 未知 repair 事件 | fail-soft、中性提示、无 `ok/完成` badge | 崩溃、静默成功或绿色 badge |
| 指标主次 | searched → inspected → judgment-qualified → build-ready → blockers | file count/selected/reviewed 直接替代 build-ready |
| 真业务成功 | v2 decision、Authority source、validated package/issuance、BR projects/files>0，全门成立 | 裸计数、伪造 decision、缺 package/signer 也成功 |
| chimeric 议程 | label provenance/relabel tolerance 优先，动态单问题 | optional labeling 优先或固定问卷 |
| browse-only 议程 | 不受训练议程阻塞 | 被训练 labeling/acquisition 问题强制阻塞 |
| open choice | 视为已解决 | 当作 missing 重复追问 |
| 确认边界 | 确认前不发起 discovery job | 咨询/输入目标后自动访问 PRIDE |

建议记录异常时保存：时间、页面 URL、输入原文、job_id、屏幕截图、`GET ...?detail=1` 的脱敏响应、前端 console 错误。不要保存 API key、provider secret、dialogue DB 或完整私有 run bundle。

## 5. 试跑后的后续计划

### P0：形成可重复的 E2E 验收

1. 在完整依赖环境复跑 web/agent-turn/task-build-plan suites，消除“缺 fastapi/typer/openai-agents”的环境空档。
2. 把 A/B/C 固化为浏览器自动化：至少覆盖顶层 phase、运行卡、Network decision 和 legacy/unknown event 文案，避免只测组件不测跨层状态。
3. 为 32/20/0 准备脱敏离线 replay fixture；默认测试不得访问 live PRIDE/provider。
4. 确认 Docker 静态 bundle 与当前 frontend 源码版本一致，页面显示 build/version，避免试错工作树。

### P1：补齐生产能力

1. 将 signer 私钥迁到进程外服务，落实 durable public-key/ledger、密钥轮换与审计；dev signer 不能进入生产毕业路径。
2. 为 `materialize_evidence`、`refresh_auth_context` 等当前未接能力实现注册、参数 schema、风险、预算和测试齐全的安全 adapter；未知能力继续 fail-closed。
3. 做一次受控 live 真跑，验证 PRIDE 网络、provider、完整 run record、publication 与 UI 投影端到端一致；限制预算并脱敏留档。

### P1：UI 体验收敛

1. 将 `blocked_with_progress` 在策略阶段的“失败”文案细化为“有进展但受阻”，避免用户把可审计进展误解为系统崩溃；颜色仍不得是成功绿。
2. blockers 增加稳定中文标签和可展开原始 code；保留 unknown code，不做案例特判。
3. 在页面提供脱敏的“复制诊断摘要”，包含 job_id、decision status、五类进度指标和 limitations，不含 secrets。
4. 真成功页明确展示 build-ready package 的项目/文件数量、Authority provenance 摘要和进入数据集构建的下一步；不以普通“发现结果”模糊毕业含义。

### P2：可用性与可访问性

1. 桌面 `1440x900` 与移动 `390x844` 检查指标换行、折叠轨迹、审计弹窗、焦点顺序与屏幕阅读器标签。
2. 长 blockers、长 PXD 列表和 200 条事件下验证滚动、轮询不抢焦点、用户手动展开状态不被重置。
3. 对 slow provider、断网、取消、刷新/replay 做恢复测试；恢复后仍以同一 Authority decision 为准。

## 6. 用户选择“不必试”时，本清单仍有何价值

用户完全可以选择不做手工试跑，理由包括没有 live credentials、不希望产生网络/模型费用、当前只做文档评审或自动测试已足够。此时本清单仍然：

- 把“候选进展”和“build-ready 毕业”的可观察差异写成验收合同；
- 给开发、测试、Grok/审计和未来维护者提供同一套场景与异常判据；
- 暴露当前环境、signer、adapter、静态 bundle 与真实 E2E 的剩余风险；
- 可直接转写为 Playwright/集成测试，不依赖用户替项目点页面；
- 为出现假绿、议程错序或 blocker 丢失时提供最小复现和取证格式。

因此，“不必试”只表示不要求用户现在手动执行，不表示这些行为没有验收标准，也不改变 build-ready 唯一毕业规则。

STATUS: READY
