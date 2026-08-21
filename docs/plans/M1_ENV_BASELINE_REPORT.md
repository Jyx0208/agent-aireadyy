# M1 完整环境与回归基线报告

日期：2026-07-22  
角色：`@lead`  
权威：`docs/plans/MEETING_CONSENSUS_PLAN.md` M1

## 1. 结论

已在当前工作树建立 Python 3.13 `.venv`，按项目声明的完整 extras 安装，并首次补跑此前因依赖缺失未收集的 Agents SDK、Typer、FastAPI、control-plane 和 web 测试。

核心 Authority、agent-turn、task-build-plan、control-plane、可收集 web、frontend tests 与 production build 均已通过。全仓 collection 仍有一个明确缺口：`tests/test_web_discovery.py` 引用仓库中不存在的 `agent.projects`，因此 M1 不宣称全部出口完成，状态为 `PARTIAL`。

本轮没有为消红削弱 Authority。四个旧 `completed` 断言被改为 `blocked`，原因是对应夹具只有候选/manifest/Runner 结束，没有 issued build-ready package。

## 2. 依赖与可复现安装

### 2.1 仓库声明核对

- `pyproject.toml`
  - `requires-python = ">=3.13"`
  - `agents-sdk = ["openai-agents==0.18.1"]`
  - `dev = ["pytest>=8.2,<9"]`
  - `web` 包含 FastAPI、OpenAI Agents SDK、OpenAI、Uvicorn、python-dotenv 等。
- `Dockerfile`
  - 基础镜像 `python:3.13-slim`
  - 安装 `-e ".[agents-sdk,dev,web]"`。
- `docker-compose.yml`
  - `web` 服务由当前工作树构建，端口 `8000`。
- Python lock
  - 当前仓库没有 Python lock 文件；只有 frontend `package-lock.json`。

### 2.2 本轮采用的 Windows 开发环境安装

从工作树根目录运行：

```powershell
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[agents-sdk,dev,web]"
```

`.venv/` 已由 `.gitignore` 排除，没有进入版本控制。

### 2.3 容器安装入口

```powershell
docker compose build --no-cache web
docker compose up -d web
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/health
```

本轮使用本机 Python 3.13 跑完整回归，没有实际重建 Docker image；Dockerfile/compose 仅做静态核对。

### 2.4 实际解析版本快照

| 组件 | 版本 |
|---|---:|
| Python | 3.13.14 |
| pip | 26.1.2 |
| openai-agents | 0.18.1 |
| typer | 0.27.0 |
| fastapi | 0.139.2 |
| uvicorn | 0.51.0 |
| pytest | 8.4.2 |
| pydantic | 2.13.4 |
| httpx | 0.28.1 |
| pandas | 2.2.3 |
| pyarrow | 18.1.0 |
| openai | 2.46.0 |
| anthropic | 0.117.1 |

当前安装方式由 `pyproject.toml` 的范围和 SDK 精确版本约束，可重复建立兼容环境，但尚不是跨平台、字节级锁定：`python:3.13-slim` 未固定 image digest，Python 传递依赖也无 lock。后续应生成跨 Windows/Linux 的正式 lock；不得把本机 `pip freeze`（含 `pywin32`）直接冒充 Linux/Docker lock。

## 3. 完整环境暴露并修复的问题

### 3.1 Control-plane 旧成功断言

完整 Agents SDK 环境首次跑出 4 个失败：

- `test_openai_agents_runner_executes_real_function_tool_loop`
- `test_openai_agents_runner_executes_multi_agent_budget_loop`
- `test_openai_agents_runner_uses_quality_first_search_environment`
- 原 `test_quality_first_runner_autonomously_repairs_a_premature_final_answer`

修复方式：

- 无 signed build-ready completion 时期望 `blocked`；
- 新增 `business_completion.succeeded is False`、`success_ui_allowed is False`；
- 明确 `manifest_selected` 不产生 `build_ready_succeeded` / `repair_succeeded`；
- 过早 final 不再恢复第二 Runner 成功双轨，测试改为验证 Authority 阻断。

### 3.2 Web API 测试漂移

3 个 LLM config 测试仍按旧单 profile、无 `Request` 参数接口调用。修复为：

- direct function test 显式传 request stub；
- 显式指定默认 profile id；
- 测试隔离宿主机 `AGENT_LLM_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY`，不读取、不打印真实值；
- public response 按当前 multi-profile schema 做字段断言，不要求旧的 exact dict。

### 3.3 M1 审计新增 fail-open

`tests/test_discovery_m1_audit_extra.py` 证明：`_public_discovery_record` 在 `business_completion=None` 时仍可返回 `status=completed`；frontend `honestDiscoveryStatus` 也依赖 repair-finished 日志才降级。

修复后：

- Python API 对 `completed/completed_with_review` 重新验证 issued `BusinessCompletionDecision`；缺失/无效时降为 `blocked`，仅 `running_progress` 映射为 `running`；
- frontend 对任何缺 Authority decision 的 server `completed` 直接 fail-closed 为 `blocked`；
- 不依赖 legacy repair event 才保持诚实状态。

## 4. 测试命令与结果

### 4.1 Authority / peer / wiring / agenda

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_discovery_authority_peer_audit.py `
  tests/test_discovery_authority_properties.py `
  tests/test_discovery_publication_contracts.py `
  tests/test_discovery_repair_controller.py `
  tests/test_discovery_evidence_store.py `
  tests/test_discovery_constraint_bindings.py `
  tests/test_discovery_quality_audit.py `
  tests/test_discovery_scientific_constraint_validity.py `
  tests/test_discovery_mixed_acquisition_policy.py `
  tests/test_discovery_sdrf_assay_evidence.py `
  tests/test_discovery_wiring_dev_publication.py `
  tests/test_discovery_wiring_publication_to_record.py `
  tests/test_discovery_wiring_repair_authority.py `
  tests/test_discovery_agenda.py
```

结果：`240 passed`。加入本轮 M1/M2 测试后的扩展组合为 `246 passed`。

### 4.2 Agent-turn / task-build-plan / control-plane

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_discovery_agent_turn.py `
  tests/test_discovery_task_build_plan.py `
  tests/test_control_plane.py
```

首次：`199 passed, 4 failed`；修正 stale success contract 后：`205 passed`。

### 4.3 可收集 web

```powershell
& .\.venv\Scripts\python.exe -m pytest -q `
  tests/test_web.py `
  tests/test_web_ai_ready.py
```

结果：`103 passed`。

`tests/test_web_discovery.py` 单独 collection 失败：

```text
ModuleNotFoundError: No module named 'agent.projects'
```

仓库当前不存在 `src/agent/projects.py`，且该测试需要 `ProjectBuildExecutionResult`、`ProjectManagerRunResult`、`SpecialistExecutionResult`。本轮未伪造测试 stub，也未擅自恢复未知历史模块。

### 4.4 M1 审计与 frontend

```powershell
& .\.venv\Scripts\python.exe -m pytest -q tests/test_discovery_m1_audit_extra.py
```

结果：`1 passed`。

```powershell
Set-Location frontend/benchmark-review
npm test
npm run build
```

结果：`9 test files / 191 passed`；TypeScript + Vite production build 通过。构建生成的 hashed static bundle 已清理，未把构建噪声混入本轮变更。

### 4.5 全仓 collection 快照

```powershell
& .\.venv\Scripts\python.exe -m pytest --collect-only -q
```

结果：`1428 tests collected, 1 error`；唯一 error 为上述缺失 `agent.projects`。

## 5. 剩余风险与退出判断

- **阻断 M1 完整出口：** `test_web_discovery.py` 无法 collection。
- **可复现性缺口：** Python 依赖没有跨平台 lock，Docker base 未固定 digest。
- FastAPI 输出 `StarletteDeprecationWarning`：当前 `httpx` TestClient 路径未来需迁移 `httpx2`，本轮不影响测试结果。
- 本轮没有网络/live PRIDE 测试，没有使用真实密钥，没有把 dev signer 当 production。
- 当前可以说“Python 3.13 完整环境主体门禁可运行”；不能说“全仓全部测试可收集”或“产品正式可用”。

M1_STATUS: PARTIAL
