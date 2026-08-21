# Internal Beta：前端看哪里

日期：2026-07-23  
范围：前端内测观察说明；不构成 production GO。

## 1. 先核对构建身份

打开 `/benchmark-review/` 后，看工作台标题右侧的 `Build v… · <revision> · <UTC time>`。鼠标悬停可看到完整版本、修订和构建时间。

- 若页面没有 `Build` 标签，当前页面是旧 bundle，不能用于本轮 UI 验收。
- 若标签内容与部署记录不一致，先清浏览器缓存并确认实际服务的静态产物；不要用业务状态判断 bundle 身份。

## 2. 看运行状态与进展

在“数据发现”的运行消息中同时看：

- 状态标签；
- searched/candidate、inspected/reviewed、judgment-qualified、build-ready 指标；
- blocker 列表和技术轨迹；
- 需要跨层核对时，在浏览器 Network 的 discovery job 响应中查看 `record.business_completion`。

文件数量只用于展开后的明细，不能单独证明业务完成。

## 3. 诚实状态对照

| 看到的情况 | 前端应显示 | 含义 |
| --- | --- | --- |
| `blocked_with_progress`，已有候选/审查但 `build_ready=0` | 紫红色“质量未通过”，保留进展和 blockers；不显示绿色“已完成” | 运行没有崩溃；已有进展但材料尚未达到 build-ready |
| server 为 `completed`，但无完整 issued `business_completion` | blocked/进展态，无成功绿勾 | 服务端阶段结束不等于业务毕业 |
| legacy repair completed / attempt finished | “修复尝试结束，结果待审计”一类中性轨迹 | repair attempt 不是成功签发 |
| 完整 Authority v2、registry authority、issued token/package、非零 build-ready projects/files | 绿色“已完成” | 唯一允许的业务成功状态 |
| 红色“失败” | 展开失败原因/技术轨迹 | 运行错误；与 `blocked_with_progress` 的质量阻塞不同 |

若出现 `build_ready=0` 却有绿色“已完成”，或只因候选数/文件数/repair event 画绿，应立即记为内测阻断问题。

## 4. 前端门禁

在 `frontend/benchmark-review` 执行：

```powershell
npm test
```

本轮结果：

```text
Test Files  10 passed (10)
Tests       192 passed (192)
```

覆盖包括 build stamp 存在且非空、无 issued build-ready 不成功、`blocked_with_progress` 保留进展、legacy repair 不冒充成功。此绿色只证明前端自动化门禁通过，不代表完整内测环境或产品正式可用。

INTERNAL_BETA_UI_STATUS: READY_FOR_GROK
