# PRIDE AI-ready Agent Release 使用说明

## 最快启动

1. 解压 ZIP 到一个英文或中文路径均可的目录。
2. 双击 `start-web.bat`，或在 PowerShell 中运行：

```powershell
.\start-web.ps1
```

首次启动会自动创建 `.venv` 并安装依赖。启动完成后会打开：

```text
http://localhost:8000
```

## 配置 API Key

网页中可以填写本次任务使用的 API Key。也可以提前复制 `.env.example` 为 `.env`，填写：

```env
AGENT_LLM_API_KEY=your_deepseek_api_key
AGENT_LLM_BASE_URL=https://api.deepseek.com
AGENT_LLM_MODEL=deepseek-v4-flash
```

不要把真实 `.env` 发给别人。

## 两种运行模式

- `Parameters only` / `仅搜参数`：只解析 PRIDE 项目、物种、仪器、修饰、酶切、workflow 和 FASTA，不下载 RAW，不跑 Docker，适合 benchmark。
- `Full workflow` / `完整流程`：下载数据、转换、运行 MSDT/FragPipe。RAW 转换和完整运行通常需要 Docker。

## 磁盘建议

软件包不包含 `.venv`、`.env`、`runs`、`.agent_cache`、RAW、mzML、parquet 或 benchmark 输出。

完整流程会产生大文件；如果只想检查参数，请使用 `Parameters only`。

## 命令行入口

```powershell
.\start.ps1 -Configure
.\start.ps1 "HeLa_ArgC-Try_CID_1.raw"
.\start.ps1 -BatchFile .\files.txt
```

## Docker

仅搜参数不需要 Docker。完整流程和 RAW 转换备用路径需要 Docker Desktop 或 Linux Docker daemon。
