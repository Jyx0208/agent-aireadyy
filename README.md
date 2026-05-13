# PRIDE AI-ready Agent

面向 PRIDE Archive 的蛋白质组学自动化 Agent：输入一个 PRIDE 里的原始文件名（例如 `P17_severe_NoPOTS.raw`），自动解析项目、下载 RAW、转换 mzML、推断 DDA 搜库参数、推荐/下载 FASTA，并生成可交给 `MSDT-Converter` Docker 使用的输入目录。

> 当前目标是“先跑起来”：默认会更信任 PRIDE 元数据、SDRF 和大模型推断；不再强制每次手工提供网页或 FASTA URL。批量跑时可用 `-y` 自动确认。

## 产品化一键入口

给普通用户推荐只记一个入口：`start.ps1`。

```powershell
.\start.ps1 "P17_severe_NoPOTS.raw"
```

第一次运行会自动检查/创建 `.venv`、安装 Python 包、创建 `.env`，然后开始准备 PRIDE -> mzML -> MSDT Docker 输入。

如果 Windows 执行策略拦截 PowerShell 脚本，可以用：

```powershell
.\run.bat "P17_severe_NoPOTS.raw"
```

常用产品化入口：

```powershell
# 第一次配置 API Key
.\start.ps1 -Configure

# 只安装环境，不跑数据
.\start.ps1 -SetupOnly

# 使用 conda 环境安装/运行（已有 Anaconda/Miniconda 时推荐）
.\start.ps1 -SetupOnly -UseConda
.\start.ps1 "P17_severe_NoPOTS.raw" -UseConda

# 单文件准备输入包
.\start.ps1 "P17_severe_NoPOTS.raw"

# 指定输出目录
.\start.ps1 "P17_severe_NoPOTS.raw" -OutputDir ".\runs\p17_severe_nopots"

# 批量运行
.\start.ps1 -BatchFile ".\files.txt"

# 直接跑完整 Docker 流程
.\start.ps1 "P17_severe_NoPOTS.raw" -RunFull
```

## 快速开始（Windows PowerShell）

### 1. 准备环境

```powershell
git clone https://github.com/Jyx0208/agent-aireadyy.git
cd agent-aireadyy
.\scripts\setup.ps1
```

如果你希望使用 conda 而不是项目内 `.venv`：

```powershell
.\scripts\setup.ps1 -UseConda
.\start.ps1 "P17_severe_NoPOTS.raw" -UseConda
```

默认会创建/使用名为 `agent-aiready` 的 conda 环境；也可以指定环境名：

```powershell
.\scripts\setup.ps1 -UseConda -CondaEnvName "agent-aiready"
.\start.ps1 "P17_severe_NoPOTS.raw" -UseConda -CondaEnvName "agent-aiready"
```

如果你不用脚本，也可以手动安装：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

### 2. 配置大模型 API

复制 `.env.example` 为 `.env`，填入自己的 API Key：

```powershell
Copy-Item .env.example .env
notepad .env
```

示例配置（不要把真实 Key 提交到 GitHub）：

```env
AGENT_LLM_API_KEY=your_deepseek_api_key
AGENT_LLM_BASE_URL=https://api.deepseek.com
AGENT_LLM_MODEL=deepseek-v4-flash
AGENT_LLM_TIMEOUT=900
```

### 3. 单文件一键准备

```powershell
.\scripts\run_one.ps1 "P17_severe_NoPOTS.raw"
```

这会默认输出到：

```text
runs\P17_severe_NoPOTS
```

如果要指定输出目录：

```powershell
.\scripts\run_one.ps1 "P17_severe_NoPOTS.raw" ".\runs\p17_severe_nopots"
```

脚本内部等价于：

```powershell
python -m agent.cli prepare-pride-msdt-docker-input "P17_severe_NoPOTS.raw" ".\runs\p17_severe_nopots" -y
```

### 4. 批量准备

先准备一个文件列表：

```powershell
Copy-Item files.example.txt files.txt
notepad files.txt
```

然后运行：

```powershell
.\scripts\run_batch.ps1 .\files.txt
```

`files.txt` 每行一个 PRIDE 文件名，空行和 `#` 注释会被跳过。

## 给别人发 Release ZIP

生成发布包：

```powershell
.\scripts\package_release.ps1 -Version "0.1.0"
```

产物会写到：

```text
dist\agent-aireadyy-v0.1.0.zip
```

把这个 ZIP 上传到 GitHub Release。用户下载解压后只需要：

```powershell
.\start.ps1 -Configure
.\start.ps1 "P17_severe_NoPOTS.raw"
```

发布包不会包含 `.env`、`.venv`、`runs`、`.agent_cache`、RAW/mzML/parquet 等本地大文件。

## 常用命令

### 只生成计划，不下载大文件

```powershell
python -m agent.cli plan-pride-dda-run "P17_severe_NoPOTS.raw" ".\runs\p17_severe_nopots"
```

适合先检查 PRIDE 项目是否匹配、物种/仪器/酶切酶/修饰/FASTA 是否合理。

### 下载并准备 MSDT Docker 输入

```powershell
python -m agent.cli prepare-pride-msdt-docker-input "P17_severe_NoPOTS.raw" ".\runs\p17_severe_nopots" -y
```

会完成：

- 根据文件名搜索 PRIDE 项目和匹配文件。
- 下载 RAW 到 `.agent_cache\pride\...`，并硬链接/复制到当前 run。
- RAW 需要时转换为 mzML。
- 自动推断 DDA 搜库参数。
- 由大模型推荐 FASTA；确认后自动下载 UniProt FASTA。
- 写出 `converter_config.json`、FragPipe manifest、workflow 等输入文件。
- 打印下一步可运行的 `MSDT-Converter` Docker 命令。

内置 FragPipe workflow 模板来自 FragPipe 21.1 官方发布，包含 62 个完整 workflow 配置。支持的 workflow 类型包括：

**DDA LFQ：**
- `Default.workflow` - 默认 LFQ DDA
- `LFQ-MBR.workflow` - LFQ with Match-Between-Runs
- `LFQ-phospho.workflow` - LFQ 磷酸化修饰
- `LFQ-ubiquitin.workflow` - LFQ 泛素化修饰

**DDA TMT：**
- `TMT10.workflow` - TMT 10-plex
- `TMT10-MS3.workflow` - TMT 10-plex MS3
- `TMT10-phospho.workflow` - TMT 10-plex 磷酸化
- `TMT10-ubiquitin.workflow` - TMT 10-plex 泛素化
- `TMT16.workflow` - TMT 16-plex
- `TMT16-MS3.workflow` - TMT 16-plex MS3
- `TMT16-phospho.workflow` - TMT 16-plex 磷酸化

**DDA iTRAQ：**
- `iTRAQ4.workflow` - iTRAQ 4-plex
- `iTRAQ4-phospho.workflow` - iTRAQ 4-plex 磷酸化

**DDA SILAC：**
- `SILAC3.workflow` - SILAC 3-plex
- `SILAC3-phospho.workflow` - SILAC 3-plex 磷酸化

**DIA：**
- `DIA_SpecLib_Quant.workflow` - DIA 标准定量
- `DIA_SpecLib_Quant_Phospho.workflow` - DIA 磷酸化
- `DIA_DIA-Umpire_SpecLib_Quant.workflow` - DIA-Umpire 方法

**特殊应用：**
- `Open.workflow` - 开放搜索
- `FPOP.workflow` - FPOP 氧化标记
- `Nonspecific-HLA.workflow` - HLA 非特异性酶切
- `glyco-N-LFQ.workflow` - N-糖基化 LFQ
- `glyco-N-TMT.workflow` - N-糖基化 TMT
- 其他 60+ 个专业 workflow...

运行时会根据 LLM/PRIDE 推断到的酶、修饰和质量误差自动选择最合适的 workflow 并覆盖关键参数。

### 直接跑完整 Docker 流程

```powershell
python -m agent.cli run-pride-dda-msdt-docker "P17_severe_NoPOTS.raw" ".\runs\p17_severe_nopots" -y
```

如果想通过脚本直接跑完整流程：

```powershell
.\scripts\run_one.ps1 "P17_severe_NoPOTS.raw" -RunFull
```

## 依赖要求

必需：

- Windows PowerShell
- Python `>=3.13`
- 网络访问 PRIDE、UniProt、Docker Hub

推荐：

- Docker Desktop：用于 ProteoWizard RAW→mzML 备用转换，以及运行 `MSDT-Converter`
- 本地 ProteoWizard `msconvert`：如果系统路径里能找到，会优先用本地转换器

RAW 转换逻辑：

1. 先尝试本地 `msconvert`
2. 失败后自动切换到 Docker ProteoWizard：

```powershell
docker run --rm chambm/pwiz-skyline-i-agree-to-the-vendor-licenses ...
```

## 输出目录说明

一次运行通常会生成：

```text
runs\<task>\
  assets\
    downloads\        # 下载或链接的原始 RAW
    prepared\         # 转换后的 mzML
  fasta\              # 自动下载的 FASTA（如适用）
  fragpipe\
    fragpipe-files.fp-manifest
    exp\              # 预期 FragPipe / pin 输出位置
  rawspectrum\
  msdt\
  ai_ready\
  logs\
    run.log
  converter_config.json
  project_resolution.json
  metadata.json
  attributes.json
  decision_trace.json
```

全局缓存：

```text
.agent_cache\pride\<PXD...>\
```

缓存可避免重复下载大型 RAW 文件。`runs\`、`.agent_cache\`、`.env` 和质谱大文件默认不会提交到 Git。

## 大模型会做什么

大模型主要用于“补齐和放宽”自动推断：

- 汇总 SDRF 文件级属性。
- 从 PRIDE 项目描述和 data processing protocol 中提取搜库参数。
- 推荐 FASTA 名称、来源和下载 URL，例如 UniProt proteome。
- 在缺少明确 FASTA 时，基于物种推荐常用 UniProt proteome。

CLI 流水线**必须**配置 `AGENT_LLM_API_KEY` 才能运行；未配置时 `start.ps1` 会直接报错退出，`python -m agent.cli ...` 也会抛 `ValueError`。Web 模式不从 `.env` 读 API Key，每次提交任务时在页面上填写。

## FASTA 选择逻辑

优先级从高到低：

1. 命令中显式传入 `--reviewed-fasta-path`
2. 命令中显式传入 `--reviewed-fasta-url`
3. 大模型从 PRIDE 文本中推荐的 FASTA / UniProt proteome
4. 根据物种推断的默认 FASTA

批量模式推荐使用 `-y` 自动确认：

```powershell
python -m agent.cli prepare-pride-msdt-docker-input "P17_severe_NoPOTS.raw" ".\runs\p17" -y
```

如果你不想自动确认：

```powershell
.\scripts\run_one.ps1 "P17_severe_NoPOTS.raw" -NoAutoConfirm
```

## 真实例子

### 人脑脊液 ME/CFS 项目

```powershell
.\scripts\run_one.ps1 "P17_severe_NoPOTS.raw" ".\runs\p17_severe_nopots"
```

已观察到的推断：

- PRIDE 项目：`PXD076216`
- 物种：`Homo sapiens`
- 仪器：`Q Exactive Plus`
- 采集：DDA
- 酶切：trypsin
- FASTA：UniProt human proteome `UP000005640`

### 小鼠 BioID 项目

```powershell
.\scripts\run_one.ps1 "junge_jo000024_20210119_17951_BioID_FZD4_2.raw" ".\runs\junge_bioid_fzd4_2"
```

已观察到的推断：

- PRIDE 项目：`PXD077093`
- 物种：`Mus musculus`
- 仪器：`Orbitrap Fusion`
- FASTA：UniProt mouse proteome `UP000000589`

## 开发与测试

```powershell
python -m pytest -q
```

查看 CLI：

```powershell
python -m agent.cli --help
```

可用命令包括：

- `check-runtime`
- `bootstrap-msdt-converter`
- `resolve-project`
- `infer-attributes`
- `plan-dda-run`
- `plan-pride-dda-run`
- `download-pride-asset`
- `prepare-pride-asset`
- `prepare-pride-msdt-docker-input`
- `run-pride-dda-msdt-docker`
- `prepare-msdt-docker-input`
- `run-dda-msdt`
- `export-ai-ready`

## 注意事项

- 不要提交 `.env` 或真实 API Key。
- 第一次运行会下载几百 MB 到数 GB 的 RAW 文件。
- Docker ProteoWizard 镜像首次使用会下载，耗时较长。
- `fake client` 只是在测试里模拟 PRIDE/HTTP 请求的假客户端，不是真实运行组件。
