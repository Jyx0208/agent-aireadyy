# 实施计划：历史任务与批次存储生命周期

## 1. 后端测试先行

- [ ] 为指定 verified batch handoff 增加测试：
  - 完整批次恰好 500 个唯一文件；
  - 尾批次小于 500；
  - 不包含先前批次文件；
  - 返回 inputs 与 input_records 一一对应；
  - 越界 manifest、缺失 manifest 和计数不一致 fail closed。
- [ ] 为存储生命周期纯函数增加测试：
  - 只接受受管 root 内路径；
  - 拒绝路径穿越和目录外链接目标；
  - 正确统计与报告释放字节；
  - 只清理 `assets/downloads`、`assets/prepared`。
- [ ] 为历史删除 API 增加测试：
  - active 拒绝；
  - preview/confirmation 范围绑定；
  - 默认不级联；
  - 显式级联只删除关联终态 batch；
  - 索引和内存状态同步。
- [ ] 为 batch cleanup 增加测试：
  - 默认关闭；
  - completed 即时清理；
  - failed/needs_review/cancelled 保留；
  - 清理错误不覆盖业务成功；
  - 旧 manifest 兼容。

## 2. 后端实现

- [ ] 新增 `src/agent/web/storage_lifecycle.py`，集中实现受管路径、统计、清理与删除回执。
- [ ] 扩展 discovery verified batch 投影与 handoff API。
- [ ] 扩展 `_clean_batch_input_records` 的 acquisition/source 字段白名单。
- [ ] 在 batch manifest 中持久化 cleanup 选项、discovery 来源和每项 cleanup 回执。
- [ ] 在每个成功 item 的最终落盘点调用源资产清理；对非成功终态写 retained。
- [ ] 扩展 public batch record 的清理汇总。
- [ ] 扩展历史记录 open/deletable/size/source 字段。
- [ ] 实现删除 preview 与 DELETE API，更新历史索引、discovery job 文件和 batch 内存缓存。
- [ ] 为旧 discovery job 从 `verified_batches/` 重建批次投影。

## 3. 前端测试先行

- [ ] DiscoveryContextRail/批次视图：点击指定批次只交接该批次的 inputs 与 records。
- [ ] BatchPanel：结构化 records 与 cleanup 选项进入 preflight 和 create payload；默认关闭。
- [ ] HistoryPanel：discovery 可打开；大小可见；active 不可删；删除预览默认不级联。
- [ ] App：历史 discovery 打开后回到数据发现并展示持久化结果，不启动任务。
- [ ] 清理状态与释放空间渲染测试。

## 4. 前端实现

- [ ] 扩展 workflow API 类型与 handoff/history delete 调用。
- [ ] App 保存 batch seed 的 `inputs + input_records + source`，不只保存文本。
- [ ] DiscoveryContextRail 增加逐批交接按钮，累计清单只作为下载。
- [ ] BatchPanel 增加 cleanup 复选框、来源提示和清理监控。
- [ ] HistoryPanel 增加 discovery 打开、大小、删除预览 modal 与结果通知。
- [ ] 补充历史列表、批次列表和 modal 的滚动/响应式样式。

## 5. 验证

- [ ] 运行目标 Python 测试。
- [ ] 运行完整 Python 测试或记录与本改动无关的既有失败。
- [ ] 运行 `npm test`。
- [ ] 运行 `npm run build`。
- [ ] 构建前端静态资源并启动本地服务。
- [ ] 浏览器验证：
  - 历史 discovery 打开；
  - 一个指定 500 文件批次送入 BatchPanel；
  - cleanup 默认关闭与开启 payload；
  - 删除预览、默认非级联、完成后空间刷新；
  - active 删除禁用。

## 6. 风险点与回滚

- 高风险文件：`src/agent/web/app.py`、`DiscoveryContextRail.tsx`、`BatchPanel.tsx`、`HistoryPanel.tsx`、`App.tsx`。
- 路径删除逻辑独立封装且测试后再接 API。
- 所有破坏性默认值均为关闭。
- 若 UI 集成出现问题，可保留后端新字段与 API并回退前端入口；旧任务仍可正常读取。

