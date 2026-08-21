# Windows 单机生产运行手册

本项目在 Windows 服务器上由两个独立常驻服务组成：

- `PRIDEAgentWeb`：FastAPI、Carbon 前端和轻量查询接口；
- `PRIDEAgentWorker`：Huey 持久队列消费者，默认 4 个项目审查线程。

任务状态、检索词、项目审查、可用文件、500 文件批次、事件和历史索引
都保存在 `operations.sqlite`；等待执行的任务保存在独立的
`queue.sqlite`。Web 进程重启不会让正在排队的任务消失，Worker 重启后会
从持久队列和任务断点继续。

## 首次安装

管理员 PowerShell 中执行：

```powershell
Set-Location C:\path\to\agent-aireadyy
.\scripts\install-windows-services.ps1 `
  -ListenHost 0.0.0.0 `
  -Port 8000 `
  -WorkerCount 4
```

安装器会完成 Python Web 依赖、前端生产构建，并从 WinSW 官方发布页下载
固定的稳定版 `v2.12.0`，校验 SHA-256 后安装两个自动启动的 Windows
服务。WinSW 是服务包装器，不承担任务状态存储。

默认持久数据位于 `C:\ProgramData\PRIDEAgent`，不会因更新代码目录而被
覆盖。可通过 `-DataRoot D:\PRIDEAgentData` 改到容量更大的磁盘。API key
应通过机器级环境变量或
`<DataRoot>\config\llm_config.json` 提供，不写入仓库和服务 XML。

## 日常状态检查

```powershell
.\scripts\check-platform-health.ps1 -HostName 127.0.0.1 -Port 8000
Get-Service PRIDEAgentWeb, PRIDEAgentWorker
```

服务日志位于 `<DataRoot>\logs\web` 和 `<DataRoot>\logs\worker`，按大小
滚动保留。界面中的当前状态来自数据库快照和 SSE 事件流，不依赖读取巨大
日志文件。

## 在线备份

```powershell
.\scripts\backup-operations.ps1 -DataRoot D:\PRIDEAgentData
```

脚本使用 SQLite Online Backup API，运行中也能获得一致快照，并为每个
数据库记录 SHA-256。默认保留 30 天。任务产生的大型源文件和结果文件不
自动复制；生产切换前应另行对 `runs` 和需要保留的 artifacts 做存储级
快照。

## 更新与维护窗口

1. 在界面确认没有正在执行的外部调用，或先点击停止并等待状态变为
   `cancelled`/`interrupted`；
2. 执行在线备份；
3. 停止 Worker，再停止 Web；
4. 更新代码、安装依赖并构建前端；
5. 若服务配置变化，运行安装器的 `-ForceReinstall`；
6. 启动 Web 和 Worker，执行健康检查与一个小型发现任务；
7. 确认 SSE、停止/恢复、历史打开和 500 文件交付均正常后结束维护窗口。

不要在任务运行期间直接删除 `operations.sqlite`、`queue.sqlite`、WAL
文件或任务目录。历史界面的删除操作会先预览受管路径，确认后才删除，并在
数据库保存删除回执。

WinSW 官方项目与服务配置说明：
[winsw/winsw](https://github.com/winsw/winsw)。

