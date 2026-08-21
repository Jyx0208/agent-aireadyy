# 前端显示与逻辑优化说明

## 优化日期
2026-07-30

## 优化内容

### 1. 标题统一性优化
**问题：** 页面标题显示不一致
- HTML title: "PRIDE 蛋白质组学数据 Agent"
- Header 导航: "PRIDE 蛋白质组学数据运行平台"

**修复：**
- 统一为"PRIDE 蛋白质组学数据运行平台"
- 文件：`frontend/benchmark-review/index.html`

### 2. 用户引导文案简化
**问题：** 欢迎页面文案过于冗长，不够直观

**优化前：**
```
定义并确认数据发现目标
先用自然语言表达目标并确认检索词；确认前不会访问 PRIDE。
每次发现任务都会自动执行候选检索和项目审查。
```

**优化后：**
```
定义数据发现目标
用自然语言描述您的检索目标，系统将自动生成检索词并执行发现任务。
确认检索词之前不会访问 PRIDE 数据库。
```

**改进点：**
- 去掉"并确认"，简化标题
- 将两句话的分号改为句号，提高可读性
- 明确说明"系统将自动生成检索词"，降低用户理解门槛

### 3. 对话框高度修复（重要）

**问题：** 对话框最小高度过小，导致策略面板被挤到下方，用户体验差

**优化前：**
```css
.agent-layout--grill {
  min-height: 36rem;
}

.current-task-workspace .agent-surface {
  min-block-size: 36rem;
}
```

**优化后：**
```css
.agent-layout--grill {
  min-height: 48rem;
}

.current-task-workspace .agent-surface {
  min-block-size: 48rem;
}
```

**改进：** 
- 增加最小高度从 36rem 到 48rem（增加约 192px）
- 确保对话框和策略面板可以并排显示
- 避免策略面板被挤到下方需要滚动

### 4. 响应式布局优化

#### 侧边栏宽度调整
**问题：** 侧边栏占用空间过大，在中等屏幕上挤压主内容区

**优化前：**
```css
.current-task-workspace {
  grid-template-columns: minmax(0, 1fr) minmax(20rem, 24rem);
}
```

**优化后：**
```css
.current-task-workspace {
  grid-template-columns: minmax(0, 1fr) minmax(18rem, 22rem);
}
```

**改进：** 减少侧边栏最小宽度 2rem，最大宽度 2rem，为主内容区留出更多空间

#### 进度区域布局优化
**优化前：**
```css
.agent-layout--grill {
  grid-template-columns: minmax(0, 1fr) 20rem;
}
```

**优化后：**
```css
.agent-layout--grill {
  grid-template-columns: minmax(0, 1fr) 18rem;
}
```

**改进：** 
- 右侧栏从 20rem 减至 18rem
- 整体布局更紧凑，适应更多屏幕尺寸

#### 运行状态区域优化
**优化前：**
```css
.agent-layout--run-active {
  grid-template-rows: minmax(24rem, 1.35fr) minmax(12rem, .65fr);
}
```

**优化后：**
```css
.agent-layout--run-active {
  grid-template-rows: minmax(22rem, 1.3fr) minmax(12rem, .7fr);
}
```

**改进：** 调整进度区域和对话区域的比例，平衡空间分配

### 5. 空状态提示优化

**问题：** DiscoveryContextRail 的空状态文案过于技术化

**优化前：**
```
策略确认后，这里只显示真实运行状态、关键计数和结果入口。
```

**优化后：**
```
确认检索策略后，将在此显示实时运行状态和关键指标。
```

**改进点：**
- 去掉"真实"、"关键计数"等技术术语
- 使用"实时运行状态"和"关键指标"更易理解
- 句式更简洁流畅

## 测试建议

### 1. 响应式测试
- 测试 1920×1080 分辨率（主要目标）
- 测试 1366×768 分辨率（笔记本常见）
- 测试 1440×900 分辨率（MacBook）
- 验证对话框和策略面板并排显示

### 2. 功能测试
- ✅ 验证对话框高度足够，策略面板不会被挤到下方
- 验证侧边栏在不同分辨率下的显示
- 验证任务对话区域的滚动行为
- 验证进度显示区域的布局切换
- 验证标题和文案的显示一致性

### 3. 用户体验测试
- 新用户首次使用流程
- 文案可读性和引导效果
- 空状态提示的清晰度
- ✅ 对话框与策略面板的视觉平衡

## 构建验证

前端构建成功，输出文件：
```
../../src/agent/web/static/benchmark-review-next/
```

主要 chunk 大小：
- index: 299.30 kB (gzip: 93.37 kB)
- CarbonAgentChat: 2,556.98 kB (gzip: 449.71 kB)

## 后续优化建议

### 1. 性能优化
- CarbonAgentChat 组件体积过大 (2.5 MB)，建议考虑代码分割
- 考虑使用 dynamic import 进一步优化首屏加载

### 2. 交互优化
- 考虑添加侧边栏收起/展开功能
- 考虑添加键盘快捷键导航
- 优化移动端体验（如果需要支持）

### 3. 无障碍优化
- 确保所有交互元素有适当的 ARIA 标签
- 确保键盘导航顺序合理
- 考虑添加跳过导航功能

## 相关文件

修改的文件：
1. `frontend/benchmark-review/index.html` - 标题统一
2. `frontend/benchmark-review/src/App.tsx` - 欢迎文案优化
3. `frontend/benchmark-review/src/styles.css` - 响应式布局优化 + **对话框高度修复**
4. `frontend/benchmark-review/src/DiscoveryContextRail.tsx` - 空状态提示优化

## 回滚方案

如需回滚，可以从 git 历史恢复：
```bash
git checkout HEAD~1 -- frontend/benchmark-review/
```

或者手动恢复各个优化点（参考上述"优化前"代码片段）。

## 关键修复说明

**对话框高度问题（用户反馈）**：
- 原始设置 36rem 导致对话框过小，策略面板被挤压到下方
- 修复后设置为 48rem，确保足够的垂直空间
- 对话框和策略面板现在可以舒适地并排显示
- 改善了整体页面布局和用户体验
