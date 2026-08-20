# Dataset Construction 开源复用调研

调研日期：2026-08-11
范围：最新版工作树 `benchmark-review-planning` 的 Dataset Construction、task readiness、split/leakage、hard benchmark、provenance 与 dataset versioning。
来源规则：只使用官方 GitHub、官方文档、正式规范和原始论文；“活跃度”以调研日可见的官方 GitHub API `pushed_at` 和 release 为准，不用 star 数代替工程成熟度。

## 1. 结论先行

没有一个成熟开源项目能整体替代本项目的 Dataset Construction。建议保留当前产品的 Agent 编排、批量作业、审核队列和产物接口，把底层能力替换成一组成熟组件：

| 能力 | 首选复用 | 采用方式 |
|---|---|---|
| 样本元数据标准化 | SDRF-Proteomics + `sdrf-pipelines` | 直接集成官方 validator；不要自行维护另一套 ontology validator |
| MS 数据读取与 QC | OpenMS/pyOpenMS + mzQC | 作为独立 QC worker，输出标准化 metrics/mzQC；不要塞进 Web 进程 |
| task-specific 表格契约 | Pandera | 直接嵌入每个 task 的 post-processing admission contract |
| 基础 group-aware split | scikit-learn | `GroupShuffleSplit` / `StratifiedGroupKFold` 作为 v1 默认实现 |
| peptide/protein similarity-aware split | DataSAIL | 作为可选高级 splitter；不能替代项目级 must-link 与最终 leakage audit |
| Benchmark Factory 设计 | MassSpecGym 模式 + ProteomicsML 数据约定 + DLOmix baseline | 复用接口和实验范式；不直接复用 MassSpecGym 的小分子数据逻辑 |
| provenance 语义 | W3C PROV + Python `prov` | 用标准 Entity/Activity/Agent 和关系替换自由文本边语义 |
| 可交付 evidence package | RO-Crate + `ro-crate-py` | Dataset Release 时导出 `ro-crate-metadata.json` |
| 本地数据版本 | DVC | 当前阶段优先；版本化 release payload 与 manifest |
| 对象存储数据分支 | lakeFS | S3/MinIO、多用户、多工作流阶段再引入，不与 DVC 同时首发 |

关键原则：开源库负责“通用机制”，本项目只保留必须由蛋白质组任务定义的领域策略，例如哪些字段是 de novo 的硬门槛、TMT plex 如何形成 must-link group、什么叫 PTM localization ambiguity，以及缺失证据何时必须返回 `INCONCLUSIVE`。

## 2. Task Spec 不应在 Dataset Construction 再问一遍

用户在 Discovery 已经确认的目标应成为唯一、不可变且可版本化的 `TaskSpec`。Dataset Construction 不再创建第二份目标，而是把同一个 `task_spec_id` 编译为一个运行时 `DatasetContract`：

```text
Discovery TaskSpec
  ├─ search/candidate constraints        （Discovery 使用）
  └─ compiled DatasetContract
       ├─ required columns and semantics （处理后才能验证）
       ├─ label/QC admission gates       （处理后才能验证）
       ├─ grouping and split policy      （看到全体数据后才能求解）
       └─ benchmark and release policy   （构建时执行）
```

因此，这不是“又做一次 Task Spec”，而是同一目标在两个阶段的不同投影：

- Discovery 的 task readiness 是候选适配性：这个文件是否值得下载和处理。
- Dataset Construction 的 task readiness 是成品适配性：实际生成的 spectrum/PSM/peptide rows 是否满足训练或 benchmark 的入场合同。

当前 [`src/agent/discovery/task_readiness.py`](../src/agent/discovery/task_readiness.py) 明确把大量 label 要求标为“需要 downstream generation”，并允许进入 `weak_ready`。这证明它是 pre-processing eligibility，而不是最终 dataset admission；不应删除，但建议在产品术语上改称 `candidate_task_fit`，避免与 post-processing readiness 混淆。

## 3. 当前代码哪些保留，哪些替换

当前 [`src/agent/ai_ready/dataset_recipe.py`](../src/agent/ai_ready/dataset_recipe.py) 已经有 split manifest、leakage report、hard/counterfactual manifest、curation queue 和 evidence graph 产物外壳，这些接口和 UI 可以保留。但内部算法仍是 smoke/prototype：

- `auto` 只有“项目数至少 3 就 project-disjoint，否则 file-disjoint”的简单判断。
- `_assign_groups` 把最后两个 group 分别给 val/test，其余全给 train，不能控制样本量、标签分布或 covariate coverage。
- peptide/protein/modification split 只读取每个 Parquet preview 的第一个值作为整个输出的 split key，不是 spectrum/PSM row-level split。
- leakage 检查是当前 manifest 字段的集合重叠检查，尚未覆盖相似肽、同一 TMT plex、fraction/technical replicate、派生格式和不完整 lineage。
- hard case 主要是 preview 上的启发式 tag；尚未形成冻结、可复现、带 task metric 的 benchmark release。
- evidence graph 是自由定义的 `nodes/edges` JSON，尚未使用标准 provenance 语义、稳定 ID、活动时间、工具版本与文件摘要。

推荐做法不是推倒重来，而是保持现有 artifact contract，逐个替换 engine：

```text
现有 dataset_recipe API / job / artifacts
  ├─ readiness_engine  -> SDRF + OpenMS/mzQC + Pandera
  ├─ split_engine      -> sklearn baseline + optional DataSAIL
  ├─ leakage_auditor   -> 独立的领域 overlap/similarity audit
  ├─ benchmark_engine  -> frozen selector registry + task metrics
  ├─ provenance_export -> W3C PROV + RO-Crate
  └─ version_backend   -> DVC（以后可换 lakeFS adapter）
```

## 4. Readiness：具体复用边界

### 4.1 四层 admission contract

一个 processed asset 只有依次通过以下合同，才可进入 `TRAIN_ELIGIBLE` 或 `BENCHMARK_ELIGIBLE`：

1. **结构合同**：列存在、类型、非空、取值范围、唯一键、数组长度一致性。用 [Pandera](https://github.com/unionai-oss/pandera)；Pydantic 继续校验 job/config，不用来扫描 DataFrame 全表。
2. **语义元数据合同**：sample-file mapping、organism、instrument、acquisition、labeling、enrichment、fraction/replicate/plex。用 [SDRF-Proteomics 规范](https://github.com/bigbio/proteomics-sample-metadata/tree/master/sdrf-proteomics) 与官方 [`sdrf-pipelines`](https://github.com/bigbio/sdrf-pipelines) validator。
3. **MS/QC 合同**：mzML 可解析、MS level/precursor/charge/RT/fragmentation 完整度、谱图和 identification QC。用 [OpenMS/pyOpenMS](https://github.com/OpenMS/OpenMS)；QC 结果优先输出为 HUPO-PSI [mzQC](https://hupo-psi.github.io/mzQC/mzQC-intro/) 而不是自定义散乱字段。
4. **任务标签合同**：这是本项目必须定义的领域政策。例如 de novo 需要可追踪 spectrum、peptide sequence、precursor/charge 与 label confidence；PSM rescoring 需要 target/decoy、原始 score、q-value/PEP、search/database provenance；RT 需要 peptide、run-level RT 与可执行的校准策略。Pandera 可以执行合同，但不会替我们决定阈值。

第五个横切门槛是 **group/provenance completeness**：如果无法恢复 `project_id`、`source_file_id`、`sample_group_id`、`spectrum_id` 以及任务需要的 `peptide_id/modified_peptide_id`，不能把 leakage 标成 PASS；结果应是 `REVIEW` 或 `INCONCLUSIVE`。

### 4.2 Pandera 与 Great Expectations 的选择

- **现在选 Pandera**：它是轻量 Python 库，直接支持 pandas/polars/pyspark 等 dataframe-like 对象，适合当前 pandas/Parquet、本地 worker 和 pytest 架构。官方 GitHub 为 MIT license，2026-08-07 仍有 repo push，最新 release `v0.32.1`（2026-06-29）：[repo metadata](https://api.github.com/repos/unionai-oss/pandera)、[releases](https://github.com/unionai-oss/pandera/releases)。
- **暂不引入 Great Expectations**：GX 的 Expectation Suite、Checkpoint、Validation Result 和 Data Docs 很成熟，适合跨数据源、独立质量平台和团队门户；但对当前单应用会引入 Data Context、stores/actions 等第二套控制平面。官方文档说明 [Checkpoint 会执行 Validation Definitions 并触发 Actions](https://docs.greatexpectations.io/docs/core/trigger_actions_based_on_results/run_a_checkpoint/)。Apache-2.0，2026-08-11 仍有 repo push并持续发版：[repo metadata](https://api.github.com/repos/great-expectations/great_expectations)。等项目需要独立 Data Quality service 时再评估。

### 4.3 SDRF/OpenMS 不等于 task readiness score

SDRF validator 能验证结构、唯一性与 ontology term；OpenMS 能读取标准 MS 格式并计算大量 QC；mzQC 能无歧义交换 QC metrics。这些都不能自动回答“对 de novo 是否达到 0.85 readiness”。最终 readiness 应是：

```text
hard gates（任何一项失败即不得训练）
  + calibrated quality dimensions（用于排序/人工审核，不覆盖 hard gate）
  + explicit evidence references（每个判断可追溯）
```

不要先拍一个加权总分再决定入场。先有可解释的硬门槛和状态，再把分数用于优先级。阈值必须通过本项目的 baseline 模型和人工抽检校准，不能声称来自 Pandera、SDRF 或 OpenMS。

## 5. Split 与 leakage：两级 splitter + 独立 auditor

### 5.1 v1：scikit-learn 做确定性 group split

[GroupShuffleSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupShuffleSplit.html) 接收任意 domain group；[StratifiedGroupKFold](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.StratifiedGroupKFold.html) 在 group 不重叠约束下尽量保持类别比例。scikit-learn 是 BSD-3-Clause，2026-08-10 仍有 repo push，`1.9.0` 于 2026-06-02 发布：[repo metadata](https://api.github.com/repos/scikit-learn/scikit-learn)。

具体用法：

1. 在拆分前，把同一 project/sample/biological replicate/technical replicate/fraction/TMT plex/派生格式形成 must-link connected component。
2. 默认用 project/component 作为 group，先冻结 test，再从剩余 group 产生 validation。
3. 分类任务才用 `StratifiedGroupKFold`；连续任务不能把 RT/intensity 粗暴当分类标签，可按预先声明的 quantile/covariate bins 做受限平衡并报告误差。
4. splitter 输入必须是 materialized row index，不允许再用“每个 Parquet 的首行值”。
5. split 完成后由独立 auditor 重新计算所有禁止交叉的 identity 和 similarity；不能把 splitter 成功当作 leakage PASS。

### 5.2 v2：DataSAIL 做 similarity-aware cold split

[DataSAIL](https://github.com/kalininalab/DataSAIL) 通过 clustering + ILP 优化 split size、stratification 和跨 split similarity。论文明确说明随机拆分会高估生物医学 OOD 泛化，并强调 similarity function 必须真正对应预期部署场景：[Nature Communications 2025 原始论文](https://www.nature.com/articles/s41467-025-58606-8)。官方文档定义了 random、identity-based 1D/2D 与 similarity-based 1D/2D split：[split techniques](https://datasail.readthedocs.io/en/latest/workflow/splits.html)。

适合本项目的接法：

- de novo/fragment intensity：把 canonical peptide 或 modified peptidoform 作为 entity，输入 sequence similarity/cluster；输出严格 unseen-similarity test view。
- protein generalization：把 protein sequence/family cluster 作为 entity。
- interaction-like 任务才考虑 2D split；普通谱图到肽序列任务通常是 1D entity + observation rows。

不能直接交给 DataSAIL 的部分：

- 它的 Python 实现主要支持一维或二维 entity，不知道 project、fraction、plex、lab 等本项目全部层级。
- similarity 的定义和阈值仍由任务决定；错误 similarity 会产生形式正确但科学问题错误的 split。
- 大数据 clustering 可能耗时数小时，且 full/lite 安装和外部 clustering/solver 依赖不同，应该作为独立可选 worker/container，而不是主 Web 依赖。

DataSAIL 本体 MIT，2026-07-06 有 repo push；最新正式 release `v1.2.4`（2025-12-01），文档 main 已显示 1.3.0 内容：[repo metadata](https://api.github.com/repos/kalininalab/DataSAIL)、[releases](https://github.com/kalininalab/DataSAIL/releases)。因此需要 pin 正式版本，不要按未发布文档写适配器。

### 5.3 Leakage Auditor 必须由本项目保留

无论使用 sklearn 还是 DataSAIL，最终审核至少计算：

- hard identity overlap：project、must-link component、sample、source file checksum、spectrum/USI；
- task identity overlap：peptide、modified peptidoform、protein/family；
- contextual overlap：lab、instrument、acquisition、gradient/search workflow；这些通常是测试视角，不一定全部要求为零；
- similarity leakage：按 TaskSpec 声明的 peptide/protein similarity threshold；
- evidence completeness：任何必需 group ID 缺失时，该层为 `INCONCLUSIVE`，不是零 overlap。

这层不是重复造 splitter，而是安全验证器；相当于“生成器”和“审计器”独立，避免算法 bug 或输入缺失造成假 PASS。

## 6. Benchmark Factory：复用范式，不照搬领域

### 6.1 MassSpecGym 可复用什么

[MassSpecGym](https://github.com/pluskal-lab/MassSpecGym) 已实现固定 dataset/fold、PyTorch Dataset/DataModule、任务抽象类、统一 metrics 和 leaderboard。其论文用 MCES chemical bond edit distance 约束 train/test，使近重复分子不跨 split，并为 retrieval 构造 mass-matched 或 formula-matched candidates：[NeurIPS 2024 原始论文](https://proceedings.neurips.cc/paper_files/paper/2024/file/c6c31413d5c53b7d1c343c1498734b0f-Paper-Datasets_and_Benchmarks_Track.pdf)、[supplement](https://proceedings.neurips.cc/paper_files/paper/2024/file/c6c31413d5c53b7d1c343c1498734b0f-Supplemental-Datasets_and_Benchmarks_Track.pdf)。MIT，2026-05-08 有 repo push，正式 release `v1.3.1`：[repo metadata](https://api.github.com/repos/pluskal-lab/MassSpecGym)。

应该复用其 **benchmark contract**：固定 fold、冻结 candidate pool、task-specific metrics、baseline adapter、versioned leaderboard input。不能直接复用其 MCES/smiles/inchikey split 或数据类，因为 MassSpecGym 是小分子 MS/MS，不是 peptide/proteomics。

### 6.2 ProteomicsML 与 DLOmix 的位置

- [ProteomicsML](https://github.com/ProteomicsML/ProteomicsML) 提供 fragmentation、ion mobility、retention time、detectability 的社区数据和教程，并强调 ML-ready 文件仍需保留到原始 ProteomeXchange/PRIDE 的 provenance：[官方数据页](https://proteomicsml.org/datasets/)、[JPR 2023 项目论文](https://doi.org/10.1021/acs.jproteome.2c00629)。它适合作为 schema/数据卡/基准数据参考，不是 dataset planner。仓库是 CC-BY-4.0（不是常规软件许可），2025-10-31 有 repo push且无 GitHub releases：[repo metadata](https://api.github.com/repos/ProteomicsML/ProteomicsML)。复用教程或内容时必须保留 attribution。
- [DLOmix](https://github.com/wilhelm-lab/dlomix) 有 RT、fragment intensity、charge、detectability 等数据类、模型和报告，可作为第一批 baseline adapter。MIT，2026-08-11 仍有 repo push，`v0.2.7` 于 2026-05-06 发布：[repo metadata](https://api.github.com/repos/wilhelm-lab/dlomix)、[官方 API 文档](https://dlomix.readthedocs.io/en/main/tensorflow/dlomix.html)。但官方文档仍提示 early-stage/API 高概率变化，应 pin 版本并放在独立 benchmark 环境，不能成为核心 dataset schema 的依赖。

### 6.3 我们的 Benchmark Factory 应输出什么

每个 benchmark case 必须是实际 row/spectrum，而不是 project-level tag：

```text
benchmark_id
task_type
source_row_id / spectrum_id / USI
frozen_split_id
case_type + selector_version
observed_evidence（例如 q-value、charge、localization probability）
expected_failure_mode
metric_set
provenance links
```

构建顺序必须是：先冻结 leakage-aware test，再从 test 中选择 hard slices。不能先挑 hard case 再让它参与 split，更不能把 benchmark case 回流进训练集。

蛋白质组 hard selectors 仍需本项目定义：PSM 的 target-decoy boundary，de novo 的 chimeric/low-intensity/unseen peptide，PTM 的 localization ambiguity/isobaric PTM，RT 的跨 gradient/instrument shift 等。OpenMS/pyOpenMS 提供谱图和 QC 计算，DataSAIL 提供 unseen similarity，DLOmix 提供部分 baseline；不存在一个成熟库能替我们定义全部 hard-case 科学语义。

如需标准化谱库交付，可选 HUPO-PSI [`mzspeclib-py`](https://github.com/HUPO-PSI/mzspeclib-py)；它能读写/验证 mzSpecLib 和多种现有 library format，Apache-2.0，最新 release `v1.0.7`，但 repo 最近 push 为 2025-07-11，建议仅作为 exporter/validator adapter，而不是核心内部表格式。

## 7. Evidence Graph：W3C PROV 作语义，RO-Crate 作交付

### 7.1 内部模型

不要一开始部署 Neo4j。当前 SQLAlchemy/SQLite 足以存标准化 provenance ledger；关键是使用标准语义和稳定 ID：

| W3C PROV | 本项目对象 |
|---|---|
| `prov:Entity` | TaskSpec、project、sample、source file、spectrum、PSM/label、QC report、split manifest、benchmark bundle、dataset release |
| `prov:Activity` | discovery、download、convert、search、label export、validate、split、leak audit、benchmark select、human review、release |
| `prov:Agent` | 软件/版本、模型/版本、自动 Agent、human reviewer |
| 关系 | `used`、`wasGeneratedBy`、`wasDerivedFrom`、`wasAssociatedWith`、`wasAttributedTo`、`wasInvalidatedBy` |

[W3C PROV-O Recommendation](https://www.w3.org/TR/prov-o/) 定义 Entity/Activity/Agent 和标准关系；Python [`prov`](https://github.com/trungdong/prov) 可直接生成 PROV-O RDF、PROV-JSON、PROV-JSONLD 并转换为 NetworkX，而不必自己实现序列化。该库 MIT，2026-08-10 仍有 repo push：[repo metadata](https://api.github.com/repos/trungdong/prov)。

稳定 ID 建议：project 用 repository accession；file 用 repository URI + SHA-256；公开 spectrum 优先用 [Universal Spectrum Identifier (USI)](https://www.nature.com/articles/s41592-021-01184-6)；内部 row 用 release-scoped immutable ID。当前按 filename 拼接 node ID 容易碰撞，也无法证明内容未变。

### 7.2 Release package

[RO-Crate 1.3](https://www.researchobject.org/ro-crate/specification.html) 用 JSON-LD 描述 Dataset、File、Person、Organization、Software、equipment/workflow 等 contextual entity，适合把 dataset payload、TaskSpec、QC、split、leakage、benchmark 和 provenance 一起交付。Python [`ro-crate-py`](https://github.com/ResearchObject/ro-crate-py) 可创建/读取 directory、zip 和 detached crate，Apache-2.0，2026-07-10 release `0.15.1`：[repo metadata](https://api.github.com/repos/ResearchObject/ro-crate-py)。

限制：`ro-crate-py 0.15.1` 官方 README 声明支持 RO-Crate 1.2/1.1/1.0，而规范当前长期版本已经是 1.3；首版应明确输出 1.2，或在升级前增加兼容性验证，不要宣称 1.3 合规。

PROV 和 RO-Crate 不重复：PROV 回答“谁在什么活动中使用什么生成了什么”，RO-Crate 回答“这个 release 包含什么、如何引用和复用”。内部 SQL 是查询/运行态 source of truth，PROV/RO-Crate 是标准导出。

## 8. Dataset versioning：DVC 先，lakeFS 后

### 8.1 当前优先 DVC

[DVC](https://github.com/iterative/dvc) 将大文件内容放在 remote storage，Git 保存轻量引用；`--rev` 可恢复指定 Git revision 的数据，pipeline 还能记录依赖与产物：[官方 `dvc get` 文档](https://dvc.org/doc/command-reference/get)。Apache-2.0，2026-08-10 仍有 repo push，最新 release `3.67.1`（2026-03-31）：[repo metadata](https://api.github.com/repos/iterative/dvc)、[releases](https://github.com/iterative/dvc/releases)。

它适合当前本地 Git + runs/Parquet 形态。建议只对 **正式 Dataset Release payload** 启用 DVC，不要把所有临时下载/cache 纳入版本库。release manifest 保存：Git commit、DVC revision/content hashes、TaskSpec version、schema/policy/tool versions、split seed/solver 与 provenance crate。

### 8.2 lakeFS 的触发条件

[lakeFS](https://github.com/treeverse/lakeFS) 在 S3/GCS/Azure/MinIO 等 object store 上提供 repository/commit/branch/merge/revert 和 zero-copy branching；官方架构还需要 lakeFS service 与 metadata store：[architecture](https://docs.lakefs.io/understand/architecture/)、[model](https://docs.lakefs.io/latest/understand/model/)。Apache-2.0，2026-08-05 有 repo push并发布 `v1.86.0`：[repo metadata](https://api.github.com/repos/treeverse/lakeFS)。

只有满足以下条件才升级 lakeFS：payload 已迁到对象存储；需要多用户并行构建数据分支；需要 commit/merge hook 保护生产 dataset；DVC/Git 工作流已经成为瓶颈。当前直接引入会多出服务、数据库、权限与运维面，不会提升 readiness/split 科学性。

DVC/lakeFS 解决内容版本，不能替代 W3C PROV/RO-Crate 的语义 lineage。两者也不应在首版同时作为正式 backend；定义一个 `DatasetVersionBackend` 接口后选其一。

## 9. 推荐依赖分层

不要把所有库塞入主运行环境，建议按 optional extra / worker image 隔离：

```text
dataset-core:
  pandera, scikit-learn

proteomics-metadata:
  sdrf-pipelines[ontology]

proteomics-qc worker:
  pyopenms / OpenMS image

similarity-split worker:
  datasail（pin 正式版本；full/lite 由部署选择）

benchmark-models worker:
  dlomix（pin；TensorFlow/PyTorch backend 显式选择）

provenance:
  prov, rocrate

external release tooling:
  dvc；未来 lakeFS client
```

MassSpecGym 和 ProteomicsML 先作为规范/测试 fixture/baseline 参考，不加入生产依赖。Great Expectations、lakeFS、图数据库目前不引入。

## 10. 建议实施顺序

### Milestone 1：可发布的 project-disjoint Dataset v0.1

1. 复用 Discovery 的 `task_spec_id`，生成一个 post-processing `DatasetContract`。
2. 为一个任务实现 Pandera admission schema；接入 SDRF validation；已有处理输出先做可计算 QC，OpenMS/mzQC 放在独立 adapter。
3. materialize row-level entity index 和 must-link component。
4. 用 sklearn 生成 project/component-disjoint train/val/test；独立 leakage audit，缺证据返回 `INCONCLUSIVE`。
5. 冻结 manifest，并导出 PROV + RO-Crate；payload 用 DVC 版本化。

### Milestone 2：严格泛化与 Benchmark v0.2

1. 接 DataSAIL optional backend，生成 peptide/protein similarity-aware test view。
2. 实现 task-specific hard selector registry，只从 frozen test 选择。
3. 用 DLOmix 或现有简单模型跑 baseline；比较 random/file/project/similarity split，报告性能下降与 slice metrics。

### Milestone 3：gap expansion / closed loop

只有前两步能稳定输出可复现实验后，才根据失败 slice 生成缺口向量，调用现有 Discovery 与 verified batch 补数据。否则所谓 gap-aware 只是根据 metadata 猜测，无法证明新增项目提高了模型泛化。

## 11. 项目成熟度与许可证快照

| 项目 | License | 2026-08-11 活跃度证据 | 采用判断 |
|---|---|---|---|
| DataSAIL | MIT | [API](https://api.github.com/repos/kalininalab/DataSAIL)，2026-07-06 push；`v1.2.4` | optional 高级 splitter |
| MassSpecGym | MIT | [API](https://api.github.com/repos/pluskal-lab/MassSpecGym)，2026-05-08 push；`v1.3.1` | 复用 benchmark 范式，不复用领域 split |
| ProteomicsML | CC-BY-4.0 | [API](https://api.github.com/repos/ProteomicsML/ProteomicsML)，2025-10-31 push；无 release | reference datasets/tutorials；注意署名 |
| DLOmix | MIT | [API](https://api.github.com/repos/wilhelm-lab/dlomix)，2026-08-11 push；`v0.2.7` | 独立 baseline adapter，pin 版本 |
| sdrf-pipelines | Apache-2.0 | [API](https://api.github.com/repos/bigbio/sdrf-pipelines)，2026-08-10 push；`v0.1.6` | 直接复用官方 validator |
| OpenMS/pyOpenMS | BSD-3-Clause | [API](https://api.github.com/repos/OpenMS/OpenMS)，2026-08-11 push；`release/3.5.0`；[官方论文确认许可](https://www.nature.com/articles/s41592-024-02197-7) | 独立 QC/processing adapter |
| mzQC | CC-BY-4.0（规范仓库） | [API](https://api.github.com/repos/HUPO-PSI/mzQC)，2026-08-07 push；`v1.0.0` | QC 交换格式/JSON Schema |
| Pandera | MIT | [API](https://api.github.com/repos/unionai-oss/pandera)，2026-08-07 push；`v0.32.1` | v1 核心 dependency |
| Great Expectations | Apache-2.0 | [API](https://api.github.com/repos/great-expectations/great_expectations)，2026-08-11 push；持续发版 | 暂缓，未来独立 QA 平台再用 |
| scikit-learn | BSD-3-Clause | [API](https://api.github.com/repos/scikit-learn/scikit-learn)，2026-08-10 push；`1.9.0` | v1 核心 splitter |
| Python `prov` | MIT | [API](https://api.github.com/repos/trungdong/prov)，2026-08-10 push | W3C PROV serialization |
| ro-crate-py | Apache-2.0 | [API](https://api.github.com/repos/ResearchObject/ro-crate-py)，2026-07-10 push；`0.15.1` | release exporter；先声明 RO-Crate 1.2 |
| DVC | Apache-2.0 | [API](https://api.github.com/repos/iterative/dvc)，2026-08-10 push；`3.67.1` | 当前正式 release payload versioning |
| lakeFS | Apache-2.0 | [API](https://api.github.com/repos/treeverse/lakeFS)，2026-08-05 push；`v1.86.0` | 对象存储规模化后采用 |

## 12. 最终取舍

最值得立即复用的四项是：`sdrf-pipelines`、Pandera、scikit-learn group split、W3C PROV/RO-Crate。它们能在不改变现有 Agent/Batch 产品面的情况下，把当前 smoke artifacts 升级为有标准、有硬门槛、可复现、可审计的 Dataset Release。

DataSAIL、OpenMS/pyOpenMS、DLOmix 很有价值，但应作为独立可选 worker，先用一个真实 task 和一个真实 processed batch 验证接口与运行成本。MassSpecGym/ProteomicsML 主要用来定义论文级 benchmark 与数据卡的形状。Great Expectations、lakeFS、图数据库在当前阶段会增加控制面，暂不引入。
