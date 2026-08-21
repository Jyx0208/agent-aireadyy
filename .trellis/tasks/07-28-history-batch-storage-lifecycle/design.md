# 技术设计：历史任务与批次存储生命周期

## 1. 设计目标

建立一个明确的数据生命周期：

```text
发现并审查
  → 每 500 个未发布文件冻结一个批次
  → 指定批次结构化交接给批量任务
  → 单文件成功后按用户选项清理任务内源资产
  → 历史页可重新打开发现/批量结果
  → 用户确认后删除终态任务并释放空间
```

不增加“相同任务”自动识别。历史复用完全由用户主动选择。

## 2. 边界与所有权

### 2.1 发现批次

- 权威来源：`runs/discovery/<discovery_id>/verified_batches/batch_NNN/dataset_manifest.json`。
- 一个冻结批次的成员身份不可因后续发现而改变。
- `file_identifier = repository + project_accession + native file path/name` 继续作为跨批次去重键。
- 新增服务器端 handoff 投影，前端不自行解析 manifest，也不再读取累计 `batch_inputs_usable.txt` 来代表某个批次。

### 2.2 历史项目

- 发现任务：发现 job JSON + discovery run directory。
- 批量任务：`runs/batches/<batch_id>/batch_manifest.json` + items。
- `project_history.json` 只是索引，不是结果权威来源；列表刷新可从磁盘重建。
- 历史项目“打开”只读取持久化状态，不启动后台任务、不发起 PRIDE 请求。

### 2.3 可清理源资产

只允许清理单项任务目录中的：

- `items/<item>/assets/downloads/`
- `items/<item>/assets/prepared/`

禁止清理：

- 用户提供的本地源路径；
- `.agent_cache` 或其他共享缓存；
- 批次目录外路径；
- 参数、日志、审计、manifest、Excel、工作流、最终执行结果与结果 ZIP。

## 3. API 契约

### 3.1 指定发现批次交接

`GET /api/discovery/jobs/{job_id}/batches/{batch_index}/handoff`

返回：

```json
{
  "job_id": "discovery_job_...",
  "discovery_id": "agents_job_...",
  "batch_index": 1,
  "file_count": 500,
  "inputs": ["https://...", "PXD.../file.raw"],
  "input_records": [
    {
      "repository": "pride",
      "project_accession": "PXD...",
      "file_name": "file.raw",
      "download_url": "https://...",
      "acquisition_mode": "DDA",
      "source_discovery_job_id": "discovery_job_...",
      "source_discovery_id": "agents_job_...",
      "source_batch_index": 1,
      "source_file_identifier": "pride:PXD...:file.raw"
    }
  ]
}
```

服务端验证：

- job 与 batch 存在；
- manifest 路径位于 discovery root 下；
- manifest 内实际唯一文件数与发布记录一致；
- 完整批次最多 500；尾批次按实际数量；
- inputs 与 input_records 一一对应。

原 JSON manifest 下载接口保留，避免破坏已有链接。

### 3.2 历史任务打开

复用现有：

- `GET /api/discovery/jobs/{job_id}?detail=1`
- `GET /api/discovery/{discovery_id}`
- `GET /api/batches/{batch_id}`

历史列表新增稳定字段：

- `open_kind`: `discovery_job | discovery_run | batch | task`
- `open_id`
- `size_bytes`
- `deletable`
- `delete_block_reason`
- 发现记录尽量同时返回 `job_id` 与 `discovery_id`。

前端 discovery “打开”流程：

1. 有 `job_id`：读取 discovery job detail。
2. 只有 `discovery_id`：读取 discovery record，并包装成只读 job view。
3. 切回“数据发现”，打开结果/过程视图。
4. 不触发 confirm、start 或 resume。

### 3.3 删除预览与执行

`GET /api/history/{kind}/{id}/delete-preview?include_linked_batches=false`

返回目标、关联关系、每类预计字节数、是否允许删除和阻止原因。

`DELETE /api/history/{kind}/{id}`

请求：

```json
{
  "confirmation_id": "<preview nonce>",
  "include_linked_batches": false
}
```

执行约束：

- 必须使用刚生成且范围一致的短期 confirmation id；
- 重新检查任务状态；
- active 状态拒绝；
- 每个目标 resolve 后必须位于对应受管 root；
- 不跟随受管目录外的链接目标；
- 逐个删除精确目录，仅对删除成功的目标移除索引；失败目标保留历史可见性并返回逐项结果；
- 返回 `estimated_bytes`、`released_bytes`、`deleted`、`failed`。

实现一个独立的 `storage_lifecycle.py`，集中拥有安全路径解析、大小统计、受控目录删除和清理回执，避免 API、历史和批量执行各写一套路径逻辑。

### 3.4 创建批量任务

`POST /api/batches/parameters` 增加：

```json
{
  "delete_source_files_after_success": false,
  "source_discovery_job_id": "...",
  "source_discovery_id": "...",
  "source_batch_index": 1
}
```

字段持久化到 batch manifest。历史页通过这些来源字段展示关联关系，并供可选级联删除预览使用。

## 4. 单文件成功清理状态机

每个 item 新增：

```json
{
  "source_cleanup": {
    "requested": true,
    "status": "not_requested | pending | completed | partial | failed | retained",
    "started_at": null,
    "finished_at": null,
    "released_bytes": 0,
    "removed_paths": [],
    "errors": []
  }
}
```

状态规则：

- 选项关闭：`not_requested`。
- 运行中：`pending`。
- item 成功且所有结果已落盘：清理两个允许目录，然后写 `completed/partial/failed`。
- item 为 `failed/needs_review/blocked/cancelled`：`retained`，不清理。
- 清理异常不会改变 item 的业务 `completed` 状态。
- 在更新 item 为 completed 前完成清理回执落盘，保证轮询看到一致状态。

对于完整工作流，先完成结果打包，再清理 `assets`。当前结果 ZIP 不包含 `assets/downloads` 或 `assets/prepared`，因此不会在 ZIP 中重复保留源数据。

## 5. 前端设计

### 5.1 发现批次

- 在已验证批次列表中，每批显示：
  - “批次 N”
  - “500 个文件 / 尾批次 X 个”
  - 项目数与累计文件数
  - “下载清单”
  - “送入批量处理”
- 点击后请求 handoff API，同时把 `inputs` 和 `input_records` 传到 BatchPanel。
- BatchPanel 显示来源标签，创建任务时原样提交结构化 records。
- 删除当前全局累计清单作为主要批量交接入口；保留其下载能力。

### 5.2 历史任务

- discovery、batch 和普通任务都显示“打开”。
- 列表显示任务类型、状态、时间、项目/文件数和磁盘占用。
- 删除按钮先打开预览 modal；发现任务可额外勾选关联批量任务，默认关闭。
- 运行中删除按钮禁用并解释“请先停止任务”。
- 删除完成后显示实际释放空间并刷新列表。

### 5.3 源文件选项

- BatchPanel 创建区增加复选框：
  - 标题：“每个文件处理成功后删除本地源文件”
  - 默认关闭。
  - 说明：“只删除本批次目录内下载和转换文件；失败/需复核项保留；结果和审计不删除。”
- 批量监控显示整批清理汇总及每项清理状态、释放空间和错误。

## 6. 兼容与迁移

- 旧 batch manifest 缺少清理字段时按 `false / not_requested` 读取。
- 旧 batch 缺少 discovery 来源字段时仍可打开和单独删除，但不会被发现任务的“关联批量任务”识别。
- 旧 discovery job 的 `result_batches` 若未持久化，可从 control-plane 运行记录或 `verified_batches/` 目录重建只读投影。
- 现有累计下载链接、manifest JSON 下载和普通历史 API 保持兼容。

## 7. 失败与恢复

- handoff manifest 不可读：返回明确错误，不退回累计清单。
- 删除某个关联目标失败：其他目标结果如实返回，不谎报全部成功。
- 索引写失败：不删除未确认的额外目标；下次刷新可从磁盘重建。
- 清理失败：保留剩余资产，记录错误，item 处理结果仍为 completed。
- 服务重启：从 batch manifest 恢复清理选项与回执；不会对旧 completed item 自动补删。

## 8. 安全与回滚

- 默认行为均保持现状：不自动删源文件、不级联删除、不自动识别相同任务。
- 删除 API 仅接受枚举 kind 和安全 stem id。
- 所有递归删除前再次验证 resolve 后路径属于精确受管 root。
- 代码回滚不会影响已存在 manifest；新增字段可被旧代码忽略。

## 9. 评估但暂不采用：Payload CMS

Payload 能快速提供数据库模型、REST/GraphQL、认证、权限和管理后台，适合未来把任务元数据建设成多人协作的长期运营平台。但本次不引入，原因如下：

- 当前运行时是 Python FastAPI + React/Vite；Payload 是 Next.js/TypeScript 全栈框架，引入后会新增第二套服务运行时、构建链和部署边界。
- 当前任务目录与 manifest 是运行权威来源。把元数据同步到 Payload 的 MongoDB/Postgres/SQLite 会产生双重真相和恢复一致性问题。
- 本次最关键的能力是受控目录删除、运行状态锁和单项完成后的本地资产清理，这些逻辑必须贴近现有 Python worker；Payload 的通用 CRUD/Admin 并不能替代它。
- 现有历史 API、磁盘索引和 React 工作台已经具备最小扩展点，直接完善的迁移成本与风险更低。

可借鉴 Payload 的产品设计：把任务、批次、来源关系、状态、占用空间和删除权限建模为明确字段，并提供可筛选的管理视图。若未来出现多用户登录、角色权限、远程数据库查询、跨服务器集中管理或非技术人员维护元数据等需求，再单独评估 Payload。
