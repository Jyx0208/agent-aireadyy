# 内测可用（Internal Beta）定义与交付清单

用户目标：**至少做成内测可用**（不是正式无脑用 / 不是 production GO）。

Date: 2026-07-22  
Worktree: benchmark-review-planning  
成功业务语义不变：仅 issued build-ready 算业务完成。

## 1. 内测可用 = 什么

内测者（懂一点产品的人）可以：

1. **按文档启动** 后端 + 前端（或 Docker，若可用）
2. **对话澄清** 后确认策略再开搜（确认前不瞎跑）
3. 看到 **诚实状态**：有候选/审查进展时可以显示；**无权威签发不得显示业务成功绿**
4. 材料不齐或无生产签名时：`blocked_with_progress` / blocked，**有 blocker 可读**
5. 一条 **固定门禁命令** 全绿（discovery 主体，不含 future_project）
6. 有 **内测须知**：会 blocked、dev sign 不是生产、预算与密钥自理

**明确不包含：**

- 生产 KMS 签名与正式上线 SLA
- 完整 Project 工单子系统
- 「随便点一定出可构建数据集」的承诺

## 2. 验收门禁（Audit/Grok）

### BETA-A 工程门禁（必须）

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_web_discovery.py
# 默认配置应 exclude future_project；全绿
.\scripts\run_m1_gate.ps1   # 若存在；否则见 M1_GATE_COMMANDS.md
```

- Authority/wiring/agenda/materialize 核心组合全绿
- `tests/test_project_orchestration_future.py` 不计入主体绿

### BETA-B 启动与文档（必须）

- `docs/plans/INTERNAL_BETA_RUNBOOK.md`：Windows 启动步骤、URL、观察 `business_completion`、常见 blocked 含义
- 前端 build stamp 可见（已有则复核）

### BETA-C 行为合同（必须，自动化优先）

- 无 issued completion → API/UI 不得 completed 成功
- browse_only 不被训练议程阻塞
- repair attempt ≠ success

### BETA-D 可选加分（有则更好）

- 显式 `DISCOVERY_AUTHORITY_DEV_SIGN=1` + 完整 synthetic 材料的 **演示路径**（仅内测，文档大字警告）
- 一个 M3 安全 adapter 或 v2 proposal intake 有测试

## 3. 状态位

完成后报告写：

```text
INTERNAL_BETA_STATUS: READY_FOR_GROK | PARTIAL
```

Grok 裁决：

```text
INTERNAL_BETA_GROK: PASS | PARTIAL | FAIL
```

**PASS 只表示内测可用，仍 NO-GO 正式产品。**

## 4. 执行优先级（自动，不问用户）

1. 确认 P0 迁出落地 + web_discovery 主体全绿  
2. RUNBOOK + gate 脚本  
3. 修任何阻断「能启动/能诚实显示」的 bug  
4. M3 最小切片（时间允许）  
5. Audit 独立复跑 → Grok 终裁  

禁止：假绿、实现完整 Project、宣称 product GO。
