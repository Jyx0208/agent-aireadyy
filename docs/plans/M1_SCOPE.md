# M1 主体门禁范围

日期：2026-07-23  
权威：`OVERNIGHT_AUTONOMY.md`、`_GROK_M1_CLOSE_QUALITY.md`

## 1. 范围决策

M1 主体门禁验证当前 Git tree 已实现的 Discovery Agent：

- Authority Plane：publication、repair、evidence、issuance/replay 与 no-progress；
- discovery 对话、agenda、task-build plan 与 Agents SDK 主循环；
- build-ready materialization 与 wiring；
- 当前 `/api/discovery` Web/API、诚实状态投影和 frontend；
- 运行环境、依赖收集和离线回归。

唯一业务毕业仍是 Authority issued build-ready。候选、审查、Runner/transport 完成、manifest selection 或 repair attempt finished 都只是进度或受阻状态。

## 2. Future Project 波次

以下能力不属于 M1 当前产品范围，而且在当前 Git tree 中没有实现：

- Project core/domain models；
- `_project_store` 与 durable project jobs；
- `/api/projects` CRUD、approval、artifact、release API；
- manager replan、specialist review、build execution 和 startup recovery workers；
- Project execution coordinator 与 discovery durable handoff。

对应的 14 个合同已从 `tests/test_web_discovery.py` 物理迁到：

```text
tests/test_project_orchestration_future.py
```

该文件统一标记 `future_project`。默认 pytest 配置运行 `-m "not future_project"`，因此 M1 主体门禁不会长期依赖手写 `-k not (...)` 名单。这里没有 skip、xfail 或兼容 stub；future tests 仍会被收集，并可显式运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -m future_project `
  tests/test_project_orchestration_future.py
```

在 Project 产品波次实现前，该命令应诚实报告 14 个失败，原因是 `_project_store`、`create_project_record`、`_project_execution_coordinator` 等真实产品入口不存在。不能通过复制另一个脏工作树草稿或创建空 stub 消红。

## 3. 主体门禁命令

当前 discovery Web 主路径：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_web_discovery.py
```

全仓 M1 默认门禁：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

`pyproject.toml` 已注册 marker 并固定默认过滤；全仓 collection 仍应包含 future contracts，执行摘要应明确报告 deselected 数，禁止将其描述为已实现或已通过。

## 4. 升级规则

只有当 Project core、API、workers、durability 与 security 作为一个受审产品波次进入当前 tree 后，才可移除 `future_project` marker。届时必须：

1. 显式 future 命令从 14 failed 变为 14 passed；
2. 默认门禁去除 marker exclusion 后保持全绿；
3. 不削弱 build-ready、approval、artifact checksum 或 durable recovery 合同；
4. 由 Audit/Grok 独立复跑，不能由实现者自封 product GO。

M1_SCOPE_STATUS: LOCKED_FOR_CURRENT_TREE
