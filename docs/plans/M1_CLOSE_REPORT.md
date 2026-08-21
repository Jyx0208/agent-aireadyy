# M1 环境与回归收尾报告

日期：2026-07-22  
角色：`@lead`  
权威：`MEETING_CONSENSUS_PLAN.md`、`M1_AUDIT_GATE_REPORT.md`、`M1_ENV_BASELINE_REPORT.md`

## 1. 结论

M1 的当前产品范围已达到一条稳定、可复跑的 Python 3.13 + Node 离线基线：全仓可收集 `1482` 个 Python tests、零 collection error；排除仓库从未提交的 Project orchestration 子系统所对应的 14 个前置测试后，当前范围全仓执行为 `1468 passed`；Authority、Wiring、agenda、agent-turn、task-build、control-plane、Web 与 frontend 均为绿。

本轮没有把候选、审查、Runner 结束或 manifest selection 恢复成业务成功。没有 issued `BusinessCompletionDecision` 时，API/job/review artifact 继续停在 `blocked`，唯一毕业仍是 Authority 签发的 build-ready。

但整体 M1 仍诚实标记为 **PARTIAL**，原因有三项：

1. 14 个 Project orchestration tests 对应的 core/API/workers 不存在于当前 Git 历史，尚未作为独立产品波次实现；
2. Docker Desktop Linux daemon 本机未启动，因此 `docker compose build web` 与容器 health 未完成；
3. Python 依赖只有版本范围和 `openai-agents==0.18.1` 精确约束，没有跨平台 lock，`python:3.13-slim` 也没有固定 digest。

这表示 M1 主体已经可以交给 Grok/Audit 复核，但尚不能宣称完整 L2 闭环，更不能宣称 product GO。

## 2. 本轮修改

### 2.1 `agent.projects` 收集缺口

- 删除 `tests/test_web_discovery.py` 顶部仅用于 Fake 返回对象的 `agent.projects` import；这些 Fake 改用标准库 `SimpleNamespace`，没有新增产品 stub。
- 当前工作树以及可见 Git refs/object history 中均无 `src/agent/projects.py` 或受版本控制的 `src/agent/projects/`。
- 主仓另一个脏工作树存在未跟踪的 `src/agent/projects/` 草稿，但其 `app.py` 没有 `/api/projects`、`_project_store`、`create_project_record` 等接线；`docs/project-wiring-design-cn.md` 也明确记载 Project core/Web wiring 未提交。因此本轮没有复制该未审草稿。
- 结果：`pytest --collect-only -q` 从原先 `ModuleNotFoundError: agent.projects` 恢复为零错误。

### 2.2 Web 与 legacy 成功语义

- `tests/test_web_discovery.py` 中执行 repository discovery 的 fixture 现在显式带 `grill_confirmed=True`，不再绕过确认边界。
- candidate/manifest-only fixture 的预期从 `completed` 改为 `blocked`；仍验证候选、文件、日志、downloads 与审查进度可读。
- `_wait_discovery_job` 把 `blocked` 视为诚实终态，避免测试等待超时。
- `tests/test_discovery_review_artifacts.py` 明确：selected candidate 与 review artifact 存在，但 `business_completion=None` 时状态仍为 `blocked`。
- `src/agent/web/app.py::_agent_discovery_configuration` 恢复通用 `project_plan.hard_ceilings` 向下封顶：project plan 只能收紧 model turns、tool calls、rounds、runtime，不能扩张 server ceiling。该逻辑没有科学主题或 accession 特判。
- legacy 静态页剩余两个 `alert(...)` 改为现有 `showFormAlert(...)`，保持 inline error 契约。

### 2.3 离线与契约漂移修正

- `tests/conftest.py` 的 autouse fixture 除隔离 saved config 外，还清除宿主机 `AGENT_LLM_API_KEY`、`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`；需要环境变量的测试自行显式设置，离线测试不会意外调用真实模型。
- Dynamic budget tests 对默认大预算断言与当前 typed model 对齐；需要测试 12/30 分层行为的 helper 显式构造小 limits，继续验证 measured-gap admission，而不是依赖全局默认。
- expert pool tests 对齐现有 broad human fallback、扩展 scale presets、calibration private metadata 与 confirmation gate；仍验证调用方伪造 credentials/model identity 被剥离。
- PRIDE request-meter fixture 对齐当前 list response contract；meter 断言未削弱。

## 3. Project orchestration 明确排除范围

以下 14 个测试属于未提交、未接线的 Project core/API/worker 波次，本轮保留在测试文件中作为未来合同，没有添加 skip/xfail，也没有伪造空产品模块让其变绿：

1. `test_web_worker_notifies_project_execution_coordinator`
2. `test_project_api_persists_goal_jobs_and_events`
3. `test_project_plan_api_uses_saved_llm_config_and_starts_queued_discovery`
4. `test_manager_replan_worker_creates_revision_and_starts_new_discovery`
5. `test_startup_restarts_all_queued_supported_project_jobs`
6. `test_project_build_execution_worker_runs_batch_and_data_scientist_loop`
7. `test_project_build_execution_preserves_partial_outputs_before_recovery`
8. `test_project_specialist_worker_completes_candidate_review_job`
9. `test_project_approval_can_be_decided_through_api`
10. `test_project_approval_rejection_is_kept_as_human_decision`
11. `test_project_artifact_download_requires_matching_checksum`
12. `test_project_release_verify_api_returns_replay_result`
13. `test_discovery_job_is_durable_idempotent_and_does_not_persist_api_key`
14. `test_interrupted_durable_discovery_job_can_be_resumed`

单独执行该集合的结果为 `14 failed, 38 deselected`；失败均指向当前产品确实不存在的 `_project_store`、`create_project_record`、`_project_execution_coordinator` 等入口。这是明确的 future-scope 红灯，不是 M1 当前 discovery Web 合同红灯。后续应把 core、storage、API、workers、security 与这些 tests 作为一个独立 Project wave 一起迁入，不能只造兼容 stub。

## 4. 可复现环境

### 4.1 Windows 开发环境

从干净 checkout 建议执行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[agents-sdk,dev,web]"
.\.venv\Scripts\python.exe -m pip check
```

本轮实测：

```text
Python 3.13.14
openai-agents 0.18.1
typer 0.27.0
fastapi 0.139.2
uvicorn 0.51.0
pytest 8.4.2
No broken requirements found.
```

`pyproject.toml` 是安装入口；不要用本机 Windows `pip freeze` 冒充 Linux/Docker lock。

### 4.2 Docker 推荐路径

仓库已有 `Dockerfile` 与 `docker-compose.yml`，推荐命令：

```powershell
docker compose build web
docker compose up -d web
Invoke-WebRequest http://127.0.0.1:8000/api/health
```

本机 `docker compose config --services` 正确返回 `web`，Docker CLI 为 `27.3.1`、context 为 `desktop-linux`。实际 build 失败于 Docker Desktop Linux engine 未运行：

```text
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified
```

因此本报告不声称 image build 或 container health 通过。另一个可复现风险是 `FROM python:3.13-slim` 未固定 digest，Python transitive dependencies 也无 lock；建议在 CI/Linux 生成正式 lock，并在验证更新流程后固定 base image digest。

## 5. 测试命令与结果

### 5.1 Authority / publication / repair / evidence / wiring / agenda / M2

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
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
  tests/test_discovery_agenda.py `
  tests/test_discovery_m1_audit_extra.py `
  tests/test_discovery_build_ready_materialization.py
```

结果：`247 passed`。

### 5.2 Agent turn / task build / control plane

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_discovery_agent_turn.py `
  tests/test_discovery_task_build_plan.py `
  tests/test_control_plane.py
```

结果：`205 passed`。

### 5.3 Web

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_web.py tests/test_web_ai_ready.py
```

结果：`103 passed`。

`test_web_discovery.py` 去除上述 14 个 future Project tests 后：`38 passed, 14 deselected`。这里的 deselection 来自报告中写明的命令行范围，不是仓库内 skip/xfail 标记。

### 5.4 全仓收集与当前范围全执行

```powershell
.\.venv\Scripts\python.exe -m pytest --collect-only -q
```

结果：`1482 tests collected in 3.31s`，零 collection error。

使用第 3 节的 14 个明确名称做 `-k not (...)` 后执行全仓：

```text
1468 passed, 14 deselected, 1 warning in 131.99s
```

唯一 warning 是 FastAPI TestClient 报告 Starlette 将从 `httpx` 迁向 `httpx2`；不影响本轮退出码，但应进入依赖升级清单。

### 5.5 Frontend 与 build identity

```powershell
cd frontend/benchmark-review
npm test -- --run
npm run build
```

结果：`10 test files / 192 passed`；TypeScript + Vite production build 成功，当前主 bundle 为 `index-B-97VgkP.js`。`M1_UI_CLOSE_REPORT.md` 已验证 package version、revision 与 UTC build time 的可见 build stamp；本轮只验证构建产物，没有完成真实浏览器/API A–C 场景。

## 6. 安全与非目标

- 未访问 live PRIDE，未使用真实 LLM key，未输出或提交 `.env`、dialogue DB、私钥。
- 未启用 dev signer 冒充 production signer。
- 未删除、xfail 或放宽 peer/property/replay/no-progress/build-ready tests。
- 未实现 Project orchestration 子系统；未从另一个脏工作树复制未提交代码。
- 未执行 production signer、durable ledger、builder preflight 或 L3 E2E；产品仍是 NO-GO。

## 7. 剩余出口条件

M1 从 PARTIAL 升为完整 L2 前仍需：

1. 在有 Docker daemon 的 Linux/CI 环境完成 `docker compose build web`、启动与 `/api/health`；
2. 生成并审计跨平台 Python lock，固定 Docker base digest；
3. 决定 14 个 Project tests 的独立产品 wave，连同真实 core/API/workers 一起实现或从当前 discovery 测试边界正式迁出；
4. 用当前 build stamp 对照部署静态 bundle，完成真实浏览器/API A–C 场景；
5. 由 `@audit`/Grok 独立复跑并裁决，`@lead` 不自评 merge-ready。

M1_CLOSE_STATUS: PARTIAL
