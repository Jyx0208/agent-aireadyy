## 一、背景
随着蛋白质组学公共数据库中原始质谱数据的持续积累，PRIDE、iProX 等平台已沉淀了大量具有潜在再利用价值的原始文件。然而，这些数据在进入可计算、可复现、可用于模型训练与方法开发的 AI-ready data 形态之前，仍需经历文件归属识别、项目元数据解析、实验属性判定、搜库参数配置、数据库选择、工作流匹配和结果标准化等多个关键环节。当前，这一过程在很大程度上依赖人工检索与经验判断，存在效率低、标准不统一、可扩展性差和可追溯性不足等问题。

尤其是在面向单个原始文件开展再分析时，研究者通常首先需要确定该文件所属的 project，并进一步结合项目级描述信息、样本元数据和实验注释来推断该文件的采集方式、仪器平台、样本物种、蛋白酶类型及搜库参数等关键信息。若项目同时提供 SDRF 等结构化注释文件，则可较为准确地完成属性识别；但在大量历史数据或注释不完整项目中，相关信息往往仅分散存在于项目网页描述、附属文献或文件命名模式中，导致后续搜库与结果标准化过程存在较大的不确定性。

因此，有必要构建一个面向蛋白质组学原始文件的 AI-ready data 自动生成 Agent，以“单文件输入—项目定位—属性识别—流程匹配—标准化输出”为主线，建立具有自动化、标准化和可追溯能力的数据再处理框架。该 Agent 的建设不仅能够提升公共蛋白质组学数据资源的利用效率，也将为后续的算法训练、跨项目比较分析和大规模数据整合提供统一的数据基础。

## 二、目标
本项目拟构建一个面向蛋白质组学原始质谱文件的自动化 Agent，实现从单文件输入到 AI-ready data 输出的全流程自动解析与标准化处理。系统以项目元数据驱动为核心思想，利用 PRIDE（<font style="color:#DF2A3F;">首选</font>）、iProX 、MassIVE等公共数据库中的结构化注释信息，在此基础上自动识别文件属性、匹配合适的搜库数据库与分析 workflow，并最终生成标准化的 MSDT 结果。具体目标包括以下四个方面：

### 1. 建立单文件到项目的自动追溯机制
针对输入的原始文件，自动检索其在公共蛋白质组学数据库中的所属 project；当同一文件可能对应多个项目时，建立明确的冲突消解策略，并优先保留时间最早的 project 作为主归属项目。

### 2. 建立项目驱动的文件属性识别体系
围绕 project 级元数据，自动判定文件的实验属性，包括但不限于采集类型（DDA/DIA）、仪器平台、物种信息、实验条件、蛋白酶类型、质谱参数以及潜在搜库设置等；在存在 SDRF 文件时优先使用结构化元数据，在缺失 SDRF 时基于网页描述、文献方法学信息和文件组织模式进行推断。

### 3. 建立面向搜库与结果生成的决策引擎
根据已识别的文件属性，自动选择对应的 FASTA、搜库workflow，形成标准化的搜库与结果处理链路，并支持不同实验类型下的差异化处理。

### 4. 构建可追溯的 AI-ready data 输出体系
在生成 MSDT 和 AI-ready data 的同时，完整记录项目归属依据、属性识别证据、workflow 选择逻辑和执行参数，实现结果可解释、可追踪、可复核，为后续模型训练和数据治理提供可靠支撑。

## 三、流程图
本系统的技术路线可概括为“输入标准化—项目检索—元数据解析—属性判定—流程决策—搜库执行—结果标准化”的七阶段闭环流程。若在语雀中配图，建议将流程图绘制为自左向右或自上而下的主流程，并在关键节点设置异常分支与回退路径。

<!-- 这是一张图片，ocr 内容为： -->
![](https://cdn.nlark.com/yuque/0/2026/png/22361524/1776916976757-607baf10-a67f-4ac9-a496-1807709e667b.png)

### 3.1 主流程说明
#### （1）输入标准化
系统接收单个原始文件、文件名、下载链接或本地路径作为输入，首先完成文件对象标准化处理，包括文件名提取、扩展名识别、唯一任务标识生成以及必要的哈希值计算等。该步骤的目标是将不同来源的输入统一映射为标准任务对象，为后续数据库检索和流程跟踪提供基础。

#### （2）公共数据库项目检索
以标准化后的文件信息为检索起点，到 PRIDE、iProX 等数据库中搜索可能关联的 project。检索策略包括文件名精确匹配、去扩展名匹配、文件名前缀匹配以及项目文件清单扫描等。该阶段输出候选项目集合，而非立即做唯一判定。

#### （3）候选项目排序与归属判定
当输入文件匹配到多个候选 project 时，系统根据预设规则进行排序和冲突消解。总体原则是：优先保留时间最早的 project 作为主项目，同时保留其他可能的关联项目，并对存在多项目冲突的任务进行标记。该阶段输出主项目、关联项目及项目匹配置信度。

#### （4）项目元数据采集与整合
在确定主项目后，系统从项目页面、API 接口、附属文件和样本注释中收集 project 级元数据，重点识别是否存在 SDRF 或其他结构化注释文件。若存在结构化元数据，则进入“结构化优先”模式；若不存在，则进入“描述推断”模式。此阶段的目标是构建完整的项目上下文，为文件属性识别提供证据基础。

#### （5）文件属性识别
在结构化元数据可用时，系统优先利用 SDRF 或等效样本表识别文件对应的实验属性，如 DDA/DIA 类型、仪器型号、物种、蛋白酶、修饰、分级信息和参数线索等。若缺失结构化元数据，则基于项目网页描述、方法学文本、publication 和文件命名规则进行推断，并为每项属性赋予证据来源和置信度。

```r
##R code
##for "Sample Processing Protocol", "Description", "Data Processing Protocol" columns
##需要排除靶向蛋白质组学数据

keywords <- c("DDA", "TMT", "iTRAQ", "SILAC","Data dependent","Data-dependent","Tandem Mass","isobaric tag","Stable isotope")

##筛选all DDA
exclude_keywords <- c(" DIA", "DIA ","SWATH","Data independent","Data-independent","Sequential Window Acquisition") 
matching_rows <- apply(df[, c(4:7,9)], 1, function(row) {
  any(grepl(paste(keywords, collapse = "|"), row, ignore.case = TRUE)) & 
  !any(grepl(paste(exclude_keywords, collapse = "|"), row, ignore.case = TRUE))
})
DDA <- df[matching_rows, ]


##筛选lable-free DDA（排除了其他三类，基本上可以确定是lable-free DDA）
keywords1 <- c("DDA", "Data dependent","Data-dependent")
exclude_keywords1 <- c("TMT", "iTRAQ", "SILAC","Tandem Mass","isobaric tag","Stable isotope") 
matching_rows1 <- apply(DDA[, c(4:7,9)], 1, function(row) {
  any(grepl(paste(keywords1, collapse = "|"), row, ignore.case = TRUE)) & 
  !any(grepl(paste(exclude_keywords1, collapse = "|"), row, ignore.case = TRUE))
})
lf_DDA <- DDA[matching_rows1, ]

new_col <- rep("lf_DDA", nrow(lf_DDA))
lf_DDA <- cbind(lf_DDA, new_col)


##筛选TMT-6
keywords2 <- c("6plex","6 plex","6-plex")
exclude_keywords2 <- c("iTRAQ", "SILAC","isobaric tag","Stable isotope") 
matching_rows2 <- apply(DDA[, c(4:7,9)], 1, function(row) {
    any(grepl(paste(keywords2, collapse = "|"), row, ignore.case = TRUE)) & 
        !any(grepl(paste(exclude_keywords2, collapse = "|"), row, ignore.case = TRUE))
})
tmt6 <- DDA[matching_rows2, ]

new_col <- rep("tmt6", nrow(tmt6))
tmt6 <- cbind(tmt6, new_col)


##筛选TMT-10
keywords2 <- c("10plex","10 plex","10-plex")
exclude_keywords2 <- c("iTRAQ", "SILAC","isobaric tag","Stable isotope") 
matching_rows2 <- apply(DDA[, c(4:7,9)], 1, function(row) {
    any(grepl(paste(keywords2, collapse = "|"), row, ignore.case = TRUE)) & 
        !any(grepl(paste(exclude_keywords2, collapse = "|"), row, ignore.case = TRUE))
})
tmt10 <- DDA[matching_rows2, ]

new_col <- rep("tmt10", nrow(tmt10))
tmt10 <- cbind(tmt10, new_col)


##筛选TMT-16
keywords2 <- c("16plex","16 plex","16-plex")
exclude_keywords2 <- c("iTRAQ", "SILAC","isobaric tag","Stable isotope") 
matching_rows2 <- apply(DDA[, c(4:7,9)], 1, function(row) {
    any(grepl(paste(keywords2, collapse = "|"), row, ignore.case = TRUE)) & 
        !any(grepl(paste(exclude_keywords2, collapse = "|"), row, ignore.case = TRUE))
})
tmt16 <- DDA[matching_rows2, ]


##筛选TMT-18
keywords2 <- c("18plex","18 plex","18-plex")
exclude_keywords2 <- c("iTRAQ", "SILAC","isobaric tag","Stable isotope") 
matching_rows2 <- apply(DDA[, c(4:7,9)], 1, function(row) {
    any(grepl(paste(keywords2, collapse = "|"), row, ignore.case = TRUE)) & 
        !any(grepl(paste(exclude_keywords2, collapse = "|"), row, ignore.case = TRUE))
})
tmt18 <- DDA[matching_rows2, ]


##筛选iTRAQ
keywords3 <- c("iTRAQ","isobaric tag")
exclude_keywords3 <- c("TMT", "Tandem Mass", "SILAC","Stable isotope") 
matching_rows3 <- apply(DDA[, c(4:7,9)], 1, function(row) {
    any(grepl(paste(keywords3, collapse = "|"), row, ignore.case = TRUE)) & 
        !any(grepl(paste(exclude_keywords3, collapse = "|"), row, ignore.case = TRUE))
})
iTRAQ <- DDA[matching_rows3, ]  ##3 projects

new_col <- rep("iTRAQ", nrow(iTRAQ))
iTRAQ <- cbind(iTRAQ, new_col)

##筛选SILAC
keywords4 <- c("SILAC","Stable isotope")
exclude_keywords4 <- c("TMT", "Tandem Mass", "iTRAQ","isobaric tag") 
matching_rows4 <- apply(DDA[, c(4:7,9)], 1, function(row) {
    any(grepl(paste(keywords4, collapse = "|"), row, ignore.case = TRUE)) & 
        !any(grepl(paste(exclude_keywords4, collapse = "|"), row, ignore.case = TRUE))
})
SILAC <- DDA[matching_rows4, ]

new_col <- rep("SILAC", nrow(SILAC))
SILAC <- cbind(SILAC, new_col)


##筛选all DIA
tmp <- df[!(df$Accession %in% DDA$Accession), ]
keywords_dia <- c(" DIA", "DIA ","SWATH","Data independent","Data-independent","Sequential Window Acquisition") 
DIA <- tmp[rowSums(sapply(tmp[, c(4:7,9)], function(x) grepl(paste(keywords_dia, collapse = "|"), x, ignore.case = TRUE))) > 0, ] ##69 projects

new_col <- rep("DIA", nrow(DIA))
DIA <- cbind(DIA, new_col)

data <- rbind(tmt6,tmt10,lf_DDA,iTRAQ,SILAC,DIA)
write.csv(data,paste0(Sys.Date(),"_data.csv"),row.names = F) 
```



```r
##使用python脚本时注意
BUG描述: 在蛋白质组学技术分类过程中，DIA（Data Independent Acquisition）的关键词匹配存在逻辑缺陷。根本原因是使用了空格边界匹配而非单词边界匹配：
# ❌ 错误方式
keywords_dia = [" DIA", "DIA ", ...]
# ✅ 正确方式
keywords_dia = [r"\bDIA\b", ...]
影响范围
会导致包含"DIA"子字符串的其他词被误匹配，例如：
diameter（直径）→ 错误地被识别为DIA技术
```

#### （6）FASTA 与 workflow 决策
在完成属性识别后，系统进入决策阶段，根据物种、采集方式、仪器平台、实验设计和搜库线索，自动选择合适的参考 FASTA、参数模板和分析 workflow。该阶段应同时输出决策轨迹，即说明某一 workflow 与数据库为何被选中，以及其中哪些决策源于直接证据，哪些属于规则推断或默认配置。

#### （7）搜库执行与结果标准化
系统调用相应 workflow 执行搜库、质控和结果后处理，并将生成结果统一整理为内部标准化格式，最终输出 MSDT 与 AI-ready data。同时，系统保存全过程日志、属性识别证据和参数信息，以支持后续复核与批量治理。

### 3.2 异常分支说明
为提升系统鲁棒性，流程图中建议增加以下异常回退路径：

1. **无法匹配 project**：进入待人工复核分支，并保留弱匹配结果； 
2. **存在多个 project 且属性冲突**：保留最早 project 为主项目，同时降低整体置信度； 
3. **无 SDRF 且描述信息不足**：进入保守推断模式，使用默认参数模板并标记为待复核； 
4. **关键属性无法唯一确定**：如 DDA/DIA 或物种识别不清时，启动候选策略或人工审核； 
5. **搜库失败或结果质量不足**：记录失败原因，允许在限定条件下进行一次受控重试。 

因此，整个系统并非单一线性流程，而是一个带有证据层级、置信度评估和异常回退机制的多分支决策框架。

## 四、模块设计
为实现上述技术路线，建议将 Agent 设计为若干功能解耦、逻辑衔接的核心模块。各模块既能独立开发，也能作为统一流水线协同运行。

### 4.1 输入解析模块
该模块负责接收和标准化输入文件对象，是系统的入口层。其主要功能包括：识别输入类型、提取文件名、解析扩展名、构建任务 ID、记录来源信息以及生成统一的文件任务对象。对于直接上传的原始文件，还可进一步计算哈希值作为跨平台匹配的辅助标识。

该模块的目标并非完成复杂判断，而是尽可能消除输入异构性，为后续项目检索与流程记录提供统一接口。

### 4.2 项目检索模块
该模块面向 PRIDE、iProX 等公共数据资源执行 project 检索。核心任务是围绕输入文件构建候选 project 集合，并基于匹配规则进行排序。

该模块至少应支持以下能力：  
（1）文件名精确和模糊检索；  
（2）项目文件列表扫描；  
（3）基于文件名前缀、批次命名模式的扩展检索；  
（4）多数据库并行检索与结果融合；  
（5）候选项目的排序与最早项目优先策略。

输出结果不仅应包含主项目 accession，还应包含候选项目列表、匹配证据和归属置信度，以便后续模块继承使用。

### 4.3 元数据抓取与整合模块
该模块负责获取 project 级上下文信息，是整个 Agent 的核心支撑模块。其主要任务是从不同来源整合实验元数据，包括项目标题、描述、样本信息、物种、仪器、实验类型、附属文件、publication 以及 SDRF 等结构化元数据。

建议将本模块内部进一步分为两层：

一是**结构化元数据子模块**，面向 SDRF、项目 API 字段和样本表，提取标准化字段；  
二是**非结构化信息子模块**，面向网页描述、方法学文本和文献摘要进行自然语言解析和关键词抽取。

该模块最终输出统一的项目元数据对象，并标注每项信息的来源和完整度。

### 4.4 文件属性识别模块
该模块在项目元数据基础上，对单个文件的实验属性进行判定，是系统从“项目级信息”过渡到“文件级处理策略”的关键环节。

其识别内容至少包括：

+  采集方式：DDA 或 DIA； 
+  仪器名称与平台类型； 
+  物种及样本来源； 
+  蛋白酶和酶切策略； 
+  标记方式与实验设计； 
+  可能的固定/可变修饰； 
+  质谱参数和搜库线索。 

在设计上，该模块应遵循“结构化优先、证据分级、推断回退”的原则。对于直接来源于 SDRF 的属性，可赋予较高置信度；对于从网页或文献中推断得到的属性，则需附带证据摘要和较低置信度评分。若多个来源之间存在冲突，应触发冲突标记机制而非直接覆盖。

### 4.5 决策引擎模块
该模块负责将属性识别结果映射为可执行的分析策略，包括 FASTA 选择、workflow 选择和参数模板装配。它是系统中最具方法学特征的模块之一。

在 FASTA 选择方面，决策引擎应综合考虑物种、项目描述、数据库版本线索和实验设计，优先复原原始项目所采用的数据库；在无法复原时，使用预定义的标准参考库，并记录数据库可复现等级。

在 workflow 选择方面，应首先根据 DDA/DIA 进行一级分流，再根据仪器平台、实验类型和样本设计进行二级细分。例如，DDA 与 DIA 应进入不同主分析路径，不同仪器平台可调用不同参数模板，特殊 PTM 场景、TMT 场景或多酶切场景则需进一步调整 workflow 细节。

该模块最终输出的不仅是“选什么”，还应包括“为什么这样选”的决策轨迹。

### 4.6 搜库执行模块
该模块负责实际调用 workflow 执行原始文件处理、参数配置、数据库装载、搜索任务运行和结果后处理。其本质是将前述的知识推断结果转化为实际计算任务。

该模块应支持以下基本功能：

+  原始文件格式转换与预处理； 
+  workflow 启动与资源调度； 
+  运行状态监控； 
+  搜库失败诊断； 
+  受控重试机制； 
+  中间产物与日志归档。 

需要强调的是，搜库执行模块不仅要输出计算结果，也要完整保存执行轨迹，以便在结果复核、流程调优和批量部署中使用。

### 4.7 结果标准化模块
该模块负责将 workflow 输出统一转换为系统内部认可的标准结构，形成 MSDT 及下游 AI-ready data。

标准化内容包括：

+  统一字段命名； 
+  结果层级映射； 
+  来源项目与文件标识绑定； 
+  参数版本记录； 
+  属性证据链附加； 
+  可供模型训练或再分析调用的表型结构输出。 

从系统定位来看，该模块不是简单的数据导出组件，而是完成“结果可计算化”和“结果可治理化”的关键环节。

### 4.8 审计与评估模块
该模块负责全流程可追溯性管理和性能评估，是系统走向规模化应用的必要组成部分。其主要任务包括：

+  记录项目匹配依据； 
+  记录属性识别来源与置信度； 
+  记录 workflow 选择逻辑； 
+  记录运行日志与错误原因； 
+  评估任务成功率与准确率； 
+  生成待人工复核清单。 

在科研学术意义上，该模块为系统提供了方法学评估接口，使 Agent 不仅是一个自动化工具，也是一套可验证、可比较、可优化的研究基础设施。

## 五、里程碑
本项目建议分阶段实施，以降低系统建设复杂度并逐步提升自动化能力与泛化能力。整体可设置为三个主要里程碑。

### 里程碑一：基础原型构建阶段
该阶段的核心目标是实现最小可运行闭环，即完成从单文件输入到标准化结果输出的基础链路打通。重点任务包括：输入标准化、PRIDE/iProX 项目检索、最早 project 选择策略、基础元数据抓取、SDRF 检测与解析，以及首版 FASTA 和 workflow 匹配机制。

本阶段结束时，系统应能够在一批具有较完整元数据的项目上，实现“文件定位—属性识别—workflow 选择—搜库执行—结果输出”的端到端运行。

### 里程碑二：属性推断与异常处理增强阶段
该阶段的核心目标是提升系统在元数据不完整场景下的适用性和鲁棒性。重点任务包括：网页文本与 publication 方法学信息解析、无 SDRF 场景下的属性推断、多项目冲突识别、置信度评分体系、关键属性冲突标记机制以及搜库失败后的受控回退策略。

本阶段结束时，系统应具备处理复杂公共数据项目的能力，能够对缺失结构化注释的文件给出可解释的候选属性与 workflow，并在不确定场景下自动标记复核需求。

### 里程碑三：系统评估与规模化部署阶段
该阶段的核心目标是形成面向批量任务的稳定运行能力，并建立标准化评估体系。重点任务包括：构建 benchmark 测试集，评估 project 匹配准确率、属性识别准确率、workflow 选择一致率和搜库成功率；建立日志审计与人工复核界面；支持批量文件处理和多任务队列调度；形成 workflow 版本管理和参数模板管理机制。

本阶段结束时，系统应从研究性原型提升为可持续运行的蛋白质组学 AI-ready data 自动生成平台，为公共质谱数据再利用和大规模模型训练提供标准化数据基础。

## 六、预期意义
本项目拟构建的 AI-ready data 自动生成 Agent，将蛋白质组学公共原始数据的再分析过程从依赖经验的人工流程，转化为以项目元数据为核心驱动的自动化、可追溯、可扩展的数据处理框架。其意义不仅在于提升公共数据资源再利用效率，更在于建立面向后续算法研究和数据治理的统一数据入口。

从方法学角度看，该系统通过引入“项目归属识别—属性推断—workflow 决策—证据留存”的完整链路，有望形成一套适用于蛋白质组学公共原始数据再处理的标准范式；从应用角度看，该系统可为下游模型训练、跨项目整合、AI 数据集构建及标准规范制定提供坚实的数据基础和流程支撑。

