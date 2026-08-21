# M1 前端门禁基线报告（@ui）

元信息：

- 权威：`docs/plans/MEETING_CONSENSUS_PLAN.md` §4 L2、§6 可选试跑观察点
- 范围：`frontend/benchmark-review/**` 与相关前端测试
- 工作树：`E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning`
- 日期：2026-07-22
- 结论边界：本报告只证明当前 Node 前端依赖、测试与 production build 基线；不声明 product GO、merge-ready 或完整 L2 已完成

## 1. 环境与真实脚本

检测结果：

```text
node --version
v24.18.0

npm --version
11.16.0
```

`frontend/benchmark-review/package.json` 的真实脚本为：

```json
{
  "dev": "vite",
  "build": "tsc -b && vite build",
  "test": "vitest run"
}
```

### 1.1 `npm ci`

执行：

```powershell
Set-Location frontend/benchmark-review
npm ci
```

结果：

```text
added 257 packages, and audited 258 packages in 2m
found 0 vulnerabilities
```

npm 11 同时报告 29 个 install/postinstall script 尚未进入 `allowScripts` 白名单，涉及 Carbon telemetry、`@parcel/watcher` 等。这没有导致本轮测试或构建失败，但应由依赖/安全负责人在 M1 统一环境中审查，不能由本轮静默批准脚本。

`package.json` 与 `package-lock.json` 在 `npm ci` 后无工作树 diff。

## 2. 测试门禁

### 2.1 全量前端

```powershell
npm test
```

```text
Test Files  9 passed (9)
Tests       191 passed (191)
Duration    10.26s
```

没有新增 skip/xfail。

### 2.2 诚实 UI 定向门禁

```powershell
npm test -- --run `
  src/DiscoveryProgressMessage.test.tsx `
  src/grill-tree.test.ts `
  src/CodexTimeline.test.tsx `
  src/workflow-api.test.ts
```

```text
Test Files  4 passed (4)
Tests       81 passed (81)
```

关键覆盖：

- 32 candidates / 20 reviewed or judgment-qualified / 0 build-ready：server 即使返回 `completed`，UI 仍规范化为 blocked/progress，不显示“已完成”成功绿态；
- `blocked_with_progress` 保留 searched、inspected、judgment-qualified、build-ready=0 与 blocker counts；
- build-ready 成功只有完整 Authority v2 envelope 才能保持 `completed`；
- claimed success 但 build-ready file 为 0 时 fail-closed；
- legacy `discovery_quality_repair_completed` 显示“修复尝试结束，结果待审计”，不回显旧成功宣称；
- `repair_succeeded` / `build_ready_succeeded` 事件本身不授予成功；
- 未知 repair event fail-soft，不获得 `ok/完成` badge；
- API 映射层把 `completed + BusinessCompletionDecision.succeeded=false` 规范化为 `blocked`，避免顶层策略 phase 绕过进度组件画绿。

### 2.3 审计复跑修正

审计将“缺 `business_completion` 的 server completed 必须 blocked”门禁收紧后，发现 `DiscoveryContextRail.test.tsx` 的历史成功结果 fixture 只有 `status=completed`，却仍期待“发现结果已就绪”。本轮没有回退 gate，而是按该用例“验证成功结果单入口与下载弹窗”的原意补入完整 `BusinessCompletionDecision` v2 issued build-ready fixture，包括：

- registry authority source；
- 非零 build-ready project/file；
- canonical build-ready package 及 builder preflight ref；
- issuance token；
- `success_ui_allowed=true`。

复跑结果：

```text
src/DiscoveryContextRail.test.tsx  5 passed (5)
frontend full suite                191 passed (191)
```

因此 stale fixture 已修正，缺 completion 的 fail-closed 行为保持不变。

## 3. 本轮前端门禁加固

只修改前端范围：

- `frontend/benchmark-review/src/workflow-api.ts`
  - `BusinessCompletionDecision` 类型补入 v2 可见字段：`schema_version`、`authority_source`、`build_ready_package`、`issuance_token`；
  - 客户端 success gate 除既有 `succeeded/status/package_kind/success_ui_allowed` 和非零 build-ready projects/files 外，还要求：
    - `schema_version === "business-completion/v2"`；
    - `authority_source === "publication_contract_registry"`；
    - `build_ready_package` 存在；
    - `issuance_token` 非空。
  - 前端不尝试自行做密码学验签；它只做防御性 envelope 检查，真正 issuance/package 验证仍由后端 Authority/web adapter 负责。
- `frontend/benchmark-review/src/workflow-api.test.ts`
  - 新增真实 API fetch 路径的 `completed + blocked_with_progress → blocked` 测试；
  - 新增缺 v2 package/issuance 时不得保留 completed 的测试，并保留完整 v2 envelope 正例。
- `frontend/benchmark-review/src/DiscoveryProgressMessage.test.tsx`
  - 正向 UI fixture 升级为 v2 Authority envelope，避免旧 v1 裸计数测试误当现行成功合同。

未修改 `control_plane`、`publication`、`repair` 或其它 `src/agent` 业务代码。

## 4. Production build

执行：

```powershell
npm run build
```

结果：

```text
tsc -b && vite build
12539 modules transformed
build completed successfully
```

Vite 报告既有 chunk-size warning：主 JS 约 3.0 MB、gzip 约 587 KB；不影响退出码，但属于后续性能基线，不应被忽略为 product GO。

`vite.config.ts` 将产物写入 `src/agent/web/static/benchmark-review-next`，超出本轮文件所有权。为满足“仅 frontend”约束，本轮构建完成后已精确恢复构建前静态目录，最终该目录无 diff、无 staged 变化。

## 5. 如何区分旧静态 bundle

### 5.1 当前可用办法

当前没有页面可见的 build/commit stamp。`package.json` 只有固定版本 `0.1.0`，不足以区分不同提交。

Vite 会为主资源生成 content hash，可用以下办法人工核对：

1. 执行 `npm run build`，记录输出中的主 JS 名；本轮最终 build 生成：

   ```text
   index-oFiB64zu.js
   ```

2. 对已部署页面打开“查看网页源代码”或 DevTools → Network，检查 `/benchmark-review/assets/index-*.js`。
3. 构建前仓库静态基线引用：

   ```text
   index-DTAwiSh7.js
   ```

4. 若页面仍加载 `index-DTAwiSh7.js`，它不是本轮最终 source build；不要用该页面做 L2 UI 验收。
5. 若直接验证当前源码，可用 Vite dev server（真实配置为前端 `5174`、API proxy `8001`），但仍应记录源码 commit/worktree 与依赖版本。

### 5.2 尚未闭合的 L2 缺口

- 页面没有显式 build ID / commit SHA / build time；用户无法只看 UI 判断 bundle 身份。
- 本轮未获授权提交后端静态产物，因此 Docker/后端 `8000` 页面仍可能服务旧 bundle。
- 未执行真实浏览器 A/B/C、桌面/移动、刷新恢复或 Network `record.business_completion` 跨层试跑。

建议后续在前端构建时注入非秘密 build ID，并在关于/诊断区可见；CI/CD 应校验 `index.html` hash 与部署 artifact manifest，一起纳入 L2 出口。

## 6. 门禁判断

已完成：

- Node/npm 可执行；
- `npm ci` 成功、0 vulnerabilities；
- 全量 191 tests 通过；
- 诚实 UI 81 tests 通过；
- Authority v2 envelope 的客户端 fail-closed 防线已补强；
- TypeScript/Vite production build 通过；
- 构建副作用已清理，未越权保留 `src/agent` 变化。

尚未完成：

- 可见且可机器核对的 build/version stamp；
- 当前前端源码对应的静态 artifact 正式部署；
- 完整 L2 浏览器/API 跨层试跑。

因此，本轮前端代码测试/构建基线为绿，但 M1/L2 的“部署版本可核对 + 跨层浏览器”出口尚未闭合，不能写 `READY_FOR_GROK`，也不能宣称 product GO。

M1_UI_STATUS: PARTIAL
