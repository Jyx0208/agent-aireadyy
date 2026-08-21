# Windows 前端快速更新

生产服务器从 release 目录运行 Web/Worker，持久化数据独立保存在
`E:\pride-agent\data`。纯前端修改不需要重启服务，也不会复制或修改数据目录。

## 一键更新

在项目根目录执行：

```powershell
.\scripts\deploy-windows-frontend.ps1
```

已在本地完成构建时，可跳过构建：

```powershell
.\scripts\deploy-windows-frontend.ps1 -SkipBuild
```

脚本会自动：

1. 构建并压缩 `benchmark-review-next`；
2. 临时启动仅监听本机局域网接口的 HTTP 文件服务；
3. 让服务器通过现有 SSH 会话下载发布包与最新版部署辅助脚本；
4. 校验 SHA-256、入口文件以及入口引用的全部静态资源；
5. 备份当前前端目录，并在同一磁盘卷内原子切换；
6. 验证 operations 数据库健康状态和页面入口；
7. 停止临时文件服务，保留部署清单和可回滚备份。

脚本会根据 SSH 目标路由自动选择本机传输地址。如自动判断不适用，可显式指定：

```powershell
.\scripts\deploy-windows-frontend.ps1 -TransferAddress 10.61.24.202
```

## 安全边界

- 只更新编译后的前端静态目录，不重启 `PRIDEAgentWeb` 或 `PRIDEAgentWorker`。
- `operations.sqlite`、`queue.sqlite`、运行断点、历史任务和产物均不在更新范围内。
- 部署失败时，辅助脚本会把原前端目录恢复原位。
- 成功版本的旧目录保存在
  `E:\pride-agent\backups\<release-id>\benchmark-review-next`。
- Python、API、队列或依赖发生变化时，应使用完整 release 发布流程，不使用本脚本。

## 实测基线

2026-07-30 在 tower3 Windows 服务器上，1.47 MB 前端包完成传输、原子切换和健康检查共用时
约 18 秒；构建耗时另计。
