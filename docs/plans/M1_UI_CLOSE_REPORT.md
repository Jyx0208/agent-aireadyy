# M1 前端构建身份收尾报告（@ui）

元信息：

- 权威：`docs/plans/MEETING_CONSENSUS_PLAN.md` §4 L2、§6 试跑观察点
- 范围：`frontend/benchmark-review/**` 与本报告
- 日期：2026-07-22
- 结论边界：本报告只关闭 M1 UI 的构建身份与前端测试/build 门禁，不代表整体 M1、L2、L3 或 product GO。

## 1. 可核对构建身份

新增 `frontend/benchmark-review/src/build-info.ts`，并由 `vite.config.ts` 在编译期注入以下非敏感字段：

- `version`：来自前端 `package.json`；
- `revision`：优先 `VITE_BUILD_REVISION`，其次 GitHub Actions 的 `GITHUB_SHA` 前 7 位，本地未指定时为 `local`；
- `builtAt`：优先 `VITE_BUILD_TIME`，未指定时为当前 UTC ISO 时间。

工作台标题区现在显示紧凑标签：

```text
Build v<package-version> · <revision> · <UTC-build-time>
```

鼠标悬停可看到未压缩的版本、修订和构建时间，辅助用户把浏览器中的页面与 CI/部署记录核对，避免旧静态 bundle 造成假结果。CI 或人工构建可显式固定身份：

```powershell
$env:VITE_BUILD_REVISION = "<git-short-sha>"
$env:VITE_BUILD_TIME = "<UTC-ISO-8601>"
npm run build
```

本轮涉及文件：

- `frontend/benchmark-review/vite.config.ts`
- `frontend/benchmark-review/tsconfig.node.json`
- `frontend/benchmark-review/src/build-info.ts`
- `frontend/benchmark-review/src/build-info.test.ts`
- `frontend/benchmark-review/src/App.tsx`
- `frontend/benchmark-review/src/App.test.tsx`
- `frontend/benchmark-review/src/styles.css`

## 2. 测试覆盖与诚实 UI

新增测试验证：

- 注入的 version/revision 均存在且非空；
- build time 可解析；
- 页面存在可见、可访问的“构建身份”标签，并提供完整 title。

既有诚实 UI 门禁未放宽：缺少完整 v2 Authority issued build-ready decision 时，server `completed` 仍归一化为 blocked/progress；`blocked_with_progress`、legacy repair attempt、未知 repair event、候选或审查数量均不能触发成功绿态。只有完整 issued build-ready envelope 且 build-ready projects/files 非零才允许成功。

## 3. 验证结果

在 `frontend/benchmark-review` 执行：

```powershell
npm test
```

结果：

```text
Test Files  10 passed (10)
Tests       192 passed (192)
```

使用显式身份复跑 production build：

```text
VITE_BUILD_REVISION=469112c-dirty
VITE_BUILD_TIME=2026-07-22T15:33:23Z
npm run build
```

结果：

```text
tsc -b && vite build
12540 modules transformed
✓ built in 1.59s
```

检查生成的主 bundle，确认实际内嵌：

```text
version: 0.1.0
revision: 469112c-dirty
builtAt: 2026-07-22T15:33:23Z
```

Vite 仍报告既有的 chunk-size 非致命警告；本轮未将其误报为产品上线通过。由于当前 `vite.config.ts` 的 outDir 指向 `src/agent/web/static/benchmark-review-next`，验证结束后已精确还原该越界静态目录，最终没有保留或暂存任何 `src/agent` 构建产物变更。

## 4. 状态

M1 UI 的“可见且可核对 build/version 身份”、测试与 production build 缺口已关闭。部署该 bundle、完整依赖跨层收集和真实浏览器/API A–C 仍须由整体 L2 流程验收；本报告不宣称整体 M1 READY、产品正式可用或 production GO。

M1_UI_CLOSE_STATUS: READY_FOR_GROK
