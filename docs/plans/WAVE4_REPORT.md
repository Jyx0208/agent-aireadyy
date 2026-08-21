# Wave 4 实施报告：诚实事件与 UI（H6）

## 结论

Wave 4 前端已完成。UI 继续以 **build-ready 作为唯一业务毕业标准**：候选检索、项目审查、judgment-qualified、repair Runner 返回或 HTTP/server `completed` 都只代表运行或修复尝试结束，不能单独触发“已完成”绿态。

## 修改文件

- `frontend/benchmark-review/src/DiscoveryProgressMessage.tsx`
  - 增加权威业务完成投影与诚实状态映射。
  - 优先展示 searched、inspected、judgment-qualified、build-ready 与 blocker counts。
  - 候选文件、build-ready 文件及兼容指标下沉到折叠 drill-down。
- `frontend/benchmark-review/src/DiscoveryProgressMessage.test.tsx`
  - 覆盖真实 32/20/0、权威成功及伪造成功缺 build-ready files 三类场景。
- `frontend/benchmark-review/src/grill-tree.ts`
  - 增加与后端 Authority Plane 一致的成功门。
  - 修正 legacy repair 文案，并显式映射 Wave 4 repair/result 事件。
  - completed 终态摘要使用诚实状态；聊天收尾在无权威 build-ready 成功时不宣称毕业。
- `frontend/benchmark-review/src/grill-tree.test.ts`
  - 覆盖 legacy 待审计、typed repair 进展、未知未来事件 fail-soft。
- `frontend/benchmark-review/src/workflow-api.ts`
  - 增加 `BusinessCompletionDecision` 与 publication progress 前端类型。
  - 在 discovery job API 映射边界统一规范化 `completed`，避免顶层 phase / 策略卡绕过进度视图再次显示假绿。
- `docs/plans/TEAM_BOARD.md`
  - 按协作协议仅追加 UI 里程碑与完成状态。

`CodexTimeline.tsx` / `CodexTimeline.test.tsx` 无需修改：现有 action/tool 边界可以安全承载纠偏后的事件投影。

## 防止假绿勾

当存在 `record.business_completion` 时，只有下列条件全部成立才允许 `completed` 成功绿态：

1. `succeeded === true`
2. `status === "build_ready_succeeded"`
3. `package_kind === "build_ready"`
4. `success_ui_allowed === true`
5. `build_ready_projects > 0`
6. `build_ready_files > 0`

因此，server `status=completed` 但 `business_completion.succeeded=false`、build-ready=0 时会投影为 `blocked`/进展态，不显示“已完成”绿勾；成功事件文本本身也无权改变业务状态。对没有 Authority 字段的普通旧记录保留 replay 兼容，但已知 attempt-only repair 事件会 fail-closed，不能冒充成功。

## legacy 与新事件映射

- `discovery_quality_repair_completed` 与 `repair_attempt_finished`：**“修复尝试结束，结果待审计”**。
- `repair_progressed`：有可验证进展，但尚待 build-ready 审计。
- `repair_no_progress` / `repair_incomplete` / `repair_blocked` / `blocked_with_progress`：显示无进展、未完成或阻塞，不显示成功。
- `repair_succeeded` / `build_ready_succeeded`：只显示“收到成功事件，最终仍以权威判定为准”，不回显未经审计的成功原文；最终绿态必须通过完整 `BusinessCompletionDecision` 门。
- 未知未来 repair/build-ready 事件 fail-soft：转为中性提示，不抛错、不显示 `ok/完成` badge，也不据事件名提升为成功。

## 指标展示

权威读取优先级为 `business_completion.progress` → `record.summary` → legacy record：

- searched：`candidate_projects`
- inspected：`reviewed_projects`，兼容 `inspected_projects` / `assessable_inspections`
- judgment-qualified：`judgment_qualified_projects`
- build-ready：projects 与 files 分离
- blockers：`blocker_counts`

候选 file count 与兼容的待复核文件数只在 drill-down 中展示，不再作为主进度或毕业依据。

## 验证

```text
npm test -- --run src/DiscoveryProgressMessage.test.tsx
Test Files  1 passed (1)
Tests       7 passed (7)
```

补充回归：

```text
npm test
Test Files  9 passed (9)
Tests       188 passed (188)

npm run build
tsc -b && vite build
build passed
```

生产构建生成的 `src/agent/web/static/benchmark-review-next` 临时产物已恢复到构建前内容；Wave 4 未修改 `src/agent/**` 业务源码。当前 `src/agent/discovery/constraints.py` 与 `src/agent/web/app.py` 的工作树改动在 Wave 4 开始前已存在，归属其他角色。

WAVE4_STATUS: READY_FOR_GROK
