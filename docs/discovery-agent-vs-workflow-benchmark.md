# Discovery Agent 与 Workflow 对比基准

> 本文记录 v1 固定两轮基准，结论为质量持平、未证明替代价值。新的质量优先架构与替代门槛见 `docs/discovery-agent-v2-quality-first.md`；v1 结果保留作为设计变更前基线。

## 目的

本基准用于回答一个可证伪的问题：在相同需求、数据源、候选上限、两轮发现上限且关闭历史记忆时，OpenAI Agents SDK 运行时是否比 LLM Workflow 产生更高质量的 Discovery 结果。

结果数量本身不计为质量提升。质量分由以下指标组成：

- 已知目标项目召回率：35%
- 硬约束遵守率：25%
- Valid 文件精度：15%
- Valid 或 Weak Keep 文件精度：15%
- 下游任务 Ready 文件精度：10%

通过条件要求至少三个有效成对场景、平均质量提升不低于 0.03、Agent 胜场多于负场、无新增硬约束违规、无错误提前停止，并且仓库请求不超过 Workflow 的两倍。

## 2026-07-11 结果

在修复浅层搜索和已知标签冲突选择后，三个真实 PRIDE 场景结果如下：

| 场景 | Workflow 分数 | Agent 分数 | Workflow/Agent 召回 | Workflow/Agent 请求 | Workflow/Agent 秒数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| HeLa multi-protease DDA | 0.4525 | 0.4525 | 0 / 0 | 71 / 56 | 166.4 / 139.1 |
| iPSC neuroprotection DDA | 0.8250 | 0.8250 | 1 / 1 | 70 / 49 | 152.4 / 112.9 |
| Human cell-line immunopeptidomics | 0.4750 | 0.4750 | 0 / 0 | 78 / 71 | 221.6 / 182.5 |

汇总：Agent 0 胜、3 平、0 负，平均质量差 0.000；仓库请求比 0.804；总耗时比 0.804；新增硬约束违规 0；错误提前停止 0。

**结论：当前 Agent 更省，但没有证明质量比 Workflow 更高，因此不通过“真实提升”门槛。**

## 架构判断

两种运行时最终共用同一套确定性候选打分、项目检查早停和多样性选择。Agent 当前主要影响查询词，而真正决定哪些项目被深入检查、何时停止检查、哪些候选进入最终集合的逻辑仍由固定管线控制。这解释了为什么 Agent 能减少请求和耗时，却很难改变最终质量。

下一阶段应把以下决策作为受硬上限保护的 Agent 工具能力，而不是继续增加提示词：

1. 根据每个查询的命中分布决定单查询搜索深度。
2. 在轻量元数据上提出候选重排，并由确定性约束验证器复核。
3. 根据边际有效候选增益决定继续检查项目或停止。
4. 在已知目标未命中或证据单一时发起针对性恢复搜索。

## 复现

```powershell
.venv\Scripts\python.exe scripts\run_discovery_runtime_benchmark.py `
  --output-root runs\benchmarks\agent-vs-workflow-<timestamp>
```

场景定义位于 `benchmarks/discovery_runtime_scenarios.v1.json`，机器可读报告和每次公开运行记录写入指定输出目录。脚本退出码为 0 表示通过真实提升门槛，1 表示完成但未通过，2 表示基准执行无效。
