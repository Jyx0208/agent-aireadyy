# Discovery Agent Internal Beta 运行手册

日期：2026-07-23  
目标：供了解基本风险的内测者启动和验证；**不是 production GO**。

## 1. 内测边界

Internal Beta 支持：对话澄清、策略确认、当前 Discovery 搜索路径、候选/审查进度、Authority 审计与诚实 blocked 状态。

Internal Beta 不承诺：每次搜索都能生成可构建数据集、生产 KMS/SLA、完整 Project 工单系统、无人监管的 live provider 成本或数据下载。

业务成功只有一个定义：

```text
hard constraints fail-closed 通过
AND 关键 claim 有证据
AND Authority 签发并验证 build-ready package
AND package 可进入 dataset build
```

Runner 返回、HTTP 200、候选数、审查数、manifest selection、repair attempt finished 或 positive delta 都不等于业务成功。

## 2. 首次准备

从唯一工作树执行：

```powershell
Set-Location 'E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning'
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[agents-sdk,dev,web]"
.\.venv\Scripts\python.exe -m pip check
.\scripts\run_m1_gate.ps1
```

如果 `.venv` 已存在，可跳过创建和安装。固定门禁不运行 live PRIDE，也不需要真实模型密钥。

密钥由内测者自行提供并承担预算；不要把 key 写进仓库、截图、聊天、测试 fixture 或普通 `.env` 提交。启动前确认：

```powershell
git status --short -- .env .agent_secrets
```

不得 stage 这些路径。

## 3. 启动方式 A：后端自带静态前端（端口 8000）

最简单的 Windows 路径：

```powershell
Set-Location 'E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning'
.\start-web.ps1 -Port 8000
```

脚本会检查 Web 依赖、启动 Uvicorn，并打开浏览器。健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health | ConvertTo-Json -Depth 8
```

页面：

```text
http://127.0.0.1:8000/benchmark-review
```

标题区应显示 frontend build stamp（version、revision、builtAt）。如果页面没有 build stamp，可能正在看旧静态 bundle，不应用它做内测验收。

不用启动脚本时，可直接运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn agent.web.app:app `
  --host 127.0.0.1 --port 8000
```

按 `Ctrl+C` 停止。

## 4. 启动方式 B：当前前端源码 + 后端（5174 / 8001）

Vite 配置把 `/api` 代理到 `127.0.0.1:8001`，因此需要两个终端。

终端 A：

```powershell
Set-Location 'E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning'
.\.venv\Scripts\python.exe -m uvicorn agent.web.app:app `
  --host 127.0.0.1 --port 8001
```

终端 B：

```powershell
Set-Location 'E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning\frontend\benchmark-review'
npm ci
npm run dev
```

打开：

```text
http://127.0.0.1:5174/benchmark-review/
```

后端健康检查使用 `http://127.0.0.1:8001/api/health`。两个终端分别按 `Ctrl+C` 停止。

## 5. 启动方式 C：Docker（daemon 可用时）

```powershell
Set-Location 'E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning'
docker compose build web
docker compose up -d web
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

停止：

```powershell
docker compose stop web
```

若 Docker Desktop Linux engine 未运行，应如实记录环境 blocker；不要把 `docker compose config` 成功冒充 image build/health 成功。

## 6. 内测操作路径

1. 在对话区描述目标；先让 Manager 动态澄清。
2. 每轮只回答一个 `next_decision`。browse-only 不应被训练议程问题阻塞。
3. 只有策略进入“待确认”后，点击“确认，开始搜”；确认前不应创建 discovery job。
4. 搜索时观察 searched、inspected、judgment-qualified、build-ready 与 blockers。
5. 候选或审查增加可以显示为进展，但 build-ready 为 0 时不得出现交付成功绿态。
6. repair attempt finished 只代表尝试结束；必须由 Authority 量 pre/post/delta 并重新审计。

## 7. 查看 `business_completion`

浏览器 DevTools → Network 中找到：

```text
GET /api/discovery/jobs/<job_id>?detail=1
```

也可在 PowerShell 查询：

```powershell
$job = Invoke-RestMethod `
  'http://127.0.0.1:8000/api/discovery/jobs/<job_id>?detail=1'
$job.record.business_completion | ConvertTo-Json -Depth 20
```

重点字段：

- `schema_version`：当前 Authority 合同版本；
- `succeeded`：唯一业务成功布尔值；
- `status`：例如 `blocked_with_progress`、`blocked`、`build_ready_succeeded`；
- `package_kind`：进度包或 `build_ready`；
- `success_ui_allowed`：只有 issued build-ready 才可为 true；
- `progress`：candidate/review/judgment/build-ready 中间指标；
- `blockers` / `limitations`：继续修复或诚实停止的原因；
- `build_ready_package` 与 issuance/Authority provenance。

若 `business_completion` 缺失，API/UI 必须 fail-closed，不得保留 server `completed`。32 candidates / 20 judgments / 0 build-ready 的含义是“有进展但未毕业”。

## 8. blocked 是正常且有价值的终态

常见含义：

- `blocked_with_progress`：已找到或审查候选，但材料/证据/签发不齐；
- `hard_constraint_unknown` / conflict：硬约束未证明或冲突，必须 fail-closed；
- missing membership/file/assay evidence：project 证据不能无 membership 下沉为 file 齐套；
- no progress：同 signature 连续两次零 delta，Authority 有界停止；
- signer unavailable：没有生产 signer，不允许模型或 Runner 自封成功；
- capability unavailable/rejected：未注册、越权或 metric 不可计算的 repair 不执行。

这些状态不是程序“假失败”；它们保护内测者不把候选清单当成可构建数据集。应读取 blocker，再调整 query、补材料、刷新授权或停止并保留 limitations。

## 9. DEV SIGN 高危提醒

`DISCOVERY_AUTHORITY_DEV_SIGN=1` 只允许显式 synthetic/test 演示。它使用开发签发路径，不代表生产信任根，不能用于真实交付或对外宣称成功。

普通内测保持关闭：

```powershell
Remove-Item Env:DISCOVERY_AUTHORITY_DEV_SIGN -ErrorAction SilentlyContinue
Remove-Item Env:DISCOVERY_AUTHORITY_SIGNING_KEY -ErrorAction SilentlyContinue
```

禁止将任何 PEM/private key 写入 Git、`.env`、fixture、prompt、日志或报告。生产 signer 与 durable ledger 属于后续 L3，不在 Internal Beta 内。

## 10. 快速故障排查

- `.venv` 缺依赖：重新执行 `pip install -e ".[agents-sdk,dev,web]"` 与 `pip check`。
- 端口占用：使用 `Get-NetTCPConnection -LocalPort 8000`，或改用 8001/Vite 方案。
- 前端旧版：核对可见 build stamp；当前源码试跑优先 Vite 5174。
- API 返回 blocked：先看 `record.business_completion.blockers/limitations`，不要改 UI 强制绿。
- 模型不可用：检查本机 provider 配置；不要在 issue/chat 中粘贴 key。
- Project API 不存在：这是已声明 future wave，不属于 Internal Beta Discovery 主体。

INTERNAL_BETA_RUNBOOK_STATUS: READY
