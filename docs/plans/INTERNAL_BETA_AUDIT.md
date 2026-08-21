# Internal Beta 独立终审（@audit）

日期：2026-07-23  
权威：`docs/plans/INTERNAL_BETA.md`  
裁决：**PASS — 内测可用；正式产品仍 NO-GO。**

## 1. 结论边界

当前 Discovery 主体满足“懂基本产品风险的内测者可以按文档启动、确认策略、观察诚实进展/blocked，并用一条固定命令验证门禁”的 Internal Beta 定义。

这个 PASS 不表示随便输入都能得到可构建数据集，也不表示 production signer、durable ledger、完整 Project 工单子系统、正式 SLA 或 production GO 已完成。普通内测在没有生产签名或材料不齐时，预期结果通常就是诚实的 `blocked_with_progress` / blocked；只有 Authority issued build-ready 才是业务完成。

INTERNAL_BETA_USABLE: YES  
PRODUCT_STATUS: NO-GO

## 2. 独立测试证据

统一环境：根目录 `.venv`，Python 3.13.14；`pip check` 返回 `No broken requirements found`。

| 门禁 | 独立结果 | 判断 |
|---|---:|---|
| `tests/test_web_discovery.py` 主体 | `38 passed in 34.44s` | PASS |
| Authority/properties/publication/repair/evidence + wiring + agenda + M1 sacred + materialize | `108 passed in 5.24s` | PASS |
| 更新后的固定门禁 `scripts/run_m1_gate.ps1` | `354 passed in 38.41s` | PASS |
| Frontend | `10 files / 192 passed` | PASS |
| Future Project 显式合同 | `14 failed` | 预期红灯，不计主体绿 |

固定门禁已纳入 `tests/test_discovery_m1_audit_extra.py`，持续验证缺 issued `business_completion` 时，public API 不得把 transport/Runner 的 `completed` 投影为业务成功。

## 3. Future Project 范围审查

14 个未实现的 Project core/API/worker 合同已从 `test_web_discovery.py` 物理迁入 `tests/test_project_orchestration_future.py`，统一标记：

```python
pytestmark = pytest.mark.future_project
```

`pyproject.toml` 注册 marker，并默认执行 `-m "not future_project"`。审计确认：

- 主体 `test_web_discovery.py` 保留 38 个真实当前合同并全绿；
- future 文件保留 14 个测试，不是删除或伪造 stub；
- 相关测试无 skip/xfail；
- 显式 `pytest -m future_project tests/test_project_orchestration_future.py` 仍诚实报告 14 failed，原因是 `_project_store`、`create_project_record`、`_project_execution_coordinator` 等产品入口确实未实现。

这符合 `INTERNAL_BETA.md` 明确“不包含完整 Project 工单子系统”的范围。它们不得被描述为已实现；未来只有真实 Project wave 进入当前 tree 后才可转绿。

## 4. 行为合同

自动化门禁继续证明：

- 无 issued completion 时 API/UI 不得 completed 成功；
- `build_ready=0`、候选/审查非零只能显示进展或 blocker；
- browse-only 不加载训练议程；显式 open 不重问；chimeric feasibility 优先于 optional labeling；
- repair attempt finished、Runner 返回、HTTP 200、manifest selection 和 positive delta 都不是业务成功；
- hard unknown/conflict、membership/evidence 缺失、replay、no-progress=2 与 signer unavailable 保持 fail-closed；
- dev signer 仍只用于显式 synthetic/test 机制演示，不是 production 信任根。

未发现 sacred 测试削弱、案例特判或通过事件/候选数制造假成功。

## 5. RUNBOOK 可用性审查

`docs/plans/INTERNAL_BETA_RUNBOOK.md` 已覆盖全部必选信息：

- Windows Python 3.13 `.venv`、extras 安装、`pip check` 与固定 gate；
- 后端静态前端 8000、Vite 5174 + API 8001、可选 Docker 三条路径；
- `/api/health`、`/benchmark-review` 与停止方式；
- Build stamp 核对和旧 bundle 警告；
- 确认前不启动、单一 `next_decision`、browse-only 与进展观察；
- DevTools/PowerShell 查看 `record.business_completion`；
- 常见 blocked、no-progress、evidence、signer/capability 含义；
- 密钥、预算、DEV SIGN 与正式产品边界。

审计对同一 FastAPI 应用执行无外部服务的启动面检查：

```text
GET /api/health       -> 200, status=ok
GET /benchmark-review -> 200, text/html
```

`start-web.ps1` 到 `scripts/run_web.ps1` 的 `-Port`/host/uvicorn 传递与 RUNBOOK 一致；前端 Vite proxy 指向 8001。RUNBOOK 中正向 completion 状态已纠正为真实合同值 `build_ready_succeeded`。

审计环境策略阻止了后台拉起临时 Uvicorn 进程，未绕过该限制，也未留下进程；FastAPI TestClient、应用导入、页面/health 响应、脚本静态检查和测试门禁已覆盖本轮启动审查。Docker daemon 不是 Internal Beta 必选路径，RUNBOOK 也明确不得把 compose config 冒充 image/health。

## 6. 前端与内测观察

前端独立 `192 passed`。可见 Build stamp、完整 v2 Authority success gate、`blocked_with_progress` 进展与 blocker 展示、legacy repair 中性语义均有测试覆盖。

内测者若看到以下任一情况，应直接判为内测阻断：

- `build_ready=0` 却出现绿色完成；
- 缺 `business_completion` 仍保留 server completed；
- repair/legacy event、候选或文件数直接授予成功；
- 页面无 Build stamp 或与部署记录不一致。

## 7. 正式产品 NO-GO

Internal Beta PASS 后仍缺少正式产品硬条件：

- 进程外 production signer/KMS、key rotation/revocation 与 durable issuance ledger；
- 真实 repository → canonical package → production-equivalent builder preflight 的受控闭环；
- 完整 Project 子系统；
- 生产级依赖锁、Docker/部署验证、SLA、权限/预算/回滚运维；
- L3 负向矩阵与正向 builder 证据。

禁止将本报告用于宣称产品正式可用、merge-ready 或 production GO。

INTERNAL_BETA_AUDIT: PASS

