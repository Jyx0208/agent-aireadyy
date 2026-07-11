# Discovery Agent v2：质量优先的替代路线

## 当前结论

Discovery Agent v2 已完成核心实现，但尚未完成真实质量验收。当前状态是：

- Agent 可自主决定查询、单次查询深度、候选检查对象和是否继续扩展预算。
- 模型回合、工具调用、查询单位、仓库请求和总时长仍由服务器硬上限保护。
- Agent 不再把“搜到新 accession”直接当作进展，而是观察高相关候选增量、语义覆盖、硬约束证据缺口、重复率和检查产出。
- Workflow 暂时只保留为替代基准的对照组。只有 Agent 通过真实成对质量门槛后，产品入口才应移除 Workflow。

这意味着“自主搜索架构”已经具备，但“全面超过 Workflow”仍是待验证的实验结论。

## Agent 循环

```text
自然语言需求
  -> 生成多维查询计划，并为每个查询选择深度
  -> 搜索 PRIDE，持续维护去重候选池
  -> 观察候选预览、查询产出、重复率和语义覆盖
  -> 选择 accession 做元数据与文件级检查
  -> 观察可用文件、证据缺口和任务就绪度
  -> 满足质量目标则生成 manifest
  -> 否则针对未覆盖意图重新搜索或申请扩展预算
```

搜索和检查被拆成两个工具动作。这样模型可以先低成本浏览候选，再把较贵的项目详情和文件检查预算投向最有希望的 accession，而不是让固定管线替它决定检查顺序。

## 动态预算

默认分为三个阶段：

| 阶段 | 查询单位 | PRIDE 请求 | 进入条件 |
|---|---:|---:|---|
| initial | 12 | 80 | 每次运行自动拥有 |
| expanded | 30 | 160 | 存在可量化的语义或证据缺口，且策略有预期增益 |
| max quality | 60 | 300 | 缺口仍明显，并提出与此前不同的搜索或检查策略 |

阶段值不是要求 Agent 用满的配额，而是允许它在有证据时继续。模型回合 50、工具调用 100、总时长 1800 秒是安全上限，不是固定搜索轮数。连续两次动作既没有提高语义覆盖，也没有增加高相关候选时，控制平面要求 Agent 说明并改用实质不同的策略；只有 Budget Agent 明确停止或触发硬上限时才锁定搜索。已有候选始终可以被检查和选择。

Budget Agent 负责评估边际价值，确定 `grant`、`shrink`、`replan` 或 `stop`；确定性 governor 负责验证查询绑定、一次性 grant 和所有硬上限。两者职责不同，模型不能绕过 governor。

## 需求准确性

原始自然语言和结构化需求分开评测。原始描述未明确 DDA、DIA、label-free、物种等字段时，这些字段保持 `unknown`，不会被本地默认值升级成硬约束。只有用户明确输入或 LLM 解析得到并标注来源的字段才参与硬冲突排除。

这项设计专门防止模糊问题被错误收窄，也是 Agent 相对固定 Workflow 最可能产生优势的场景。

## 可观测性

Web 日志公开以下内容：

- 实际查询计划和候选搜索次数；
- 原始、新增、重复和高相关候选数；
- 语义覆盖、硬约束证据缺口、检查产出与停止原因；
- Budget Agent 决策、grant、仓库请求、模型请求和 token 用量；
- 最终选择、警告、阻塞项和可下载审计文件。

日志展示的是模型主动提交的理由摘要、工具输入和真实观察结果，不展示供应商保留的隐藏 chain-of-thought。

## Workflow 替代基准

场景文件为 `benchmarks/discovery_replacement_scenarios.v2.json`，包含三个潜在任务，每个任务有 structured、clear、vague、ambiguous 四种表达。Workflow 使用原生路径作为基线；Agent 分别运行 1x、2x、max-quality 三个预算层级。

评分包含 graded relevance、nDCG@5、高相关召回、任务就绪度、证据完整性、硬约束违规、错误提前停止、失败率与资源使用。资源成本是硬上限和次级比较项，不会为了“省请求”牺牲答案质量。

```powershell
.venv\Scripts\python.exe scripts\run_discovery_replacement_benchmark.py `
  --output-root runs\benchmarks\discovery-agent-v2
```

可先做单场景冒烟：

```powershell
.venv\Scripts\python.exe scripts\run_discovery_replacement_benchmark.py `
  --scenario ipsc_neuroprotection_dda `
  --variant clear `
  --tier 2x `
  --output-root runs\benchmarks\discovery-agent-v2-smoke
```

单场景运行用于检查工具调用和外部服务，不足以通过替代门槛。正式结论必须来自完整成对基准及人工复核后的相关性 judgments。

## 发行门槛

在下列条件全部成立前，不移除 Workflow：

1. Agent 在各表达清晰度层级上的质量提升达到替代基准门槛。
2. Agent 胜场多于负场，且模糊需求上有可重复的净提升。
3. 没有新增硬约束违规、错误提前停止或明显失败率回归。
4. max-quality 的增量质量足以解释其额外成本。
5. 真实 PRIDE 与配置的 LLM 均完成冒烟和重复运行。

## 真实冒烟记录

2026-07-11 对 `ipsc_neuroprotection_dda / clear / 2x` 做了两轮真实 DeepSeek Pro + PRIDE 冒烟：

1. 初始 v2 直接把长语义短语提交给 PRIDE，只得到无关的 `PXD007751`，相对 Workflow 质量差为 `-0.847`。该失败暴露了仓库查询适配器缺失，结果被保留为反例。
2. 接入 PRIDE 原子 seed 编译并记录 seed 深度后，Agent 找到目标 `PXD074954`；质量差变为 `+0.816`，仓库请求为 14 对 59，耗时为 147.7 秒对 181.3 秒。
3. 随后的 Agent-only 冒烟检查了 4 个候选，保留 `PXD074954` 与同模型的 CIPN 机制项目 `PXD070094`，排除了 off-topic 的 Dravet iPSC 项目。最终选择工具已能按 accession 过滤检查后的 manifest。
4. 按每个 prompt 实际公开的约束重新评分后，Workflow 有 19 个物种硬约束违规，Agent 为 0。未在 raw prompt 中出现的 label-free 偏好不再被用来处罚 Agent。

这些结果证明查询恢复与项目级选择链路在一个真实场景中有效，但样本量不足以支持替代声明。完整成对基准、重复运行和人工 pooled relevance judgment 仍待完成。
