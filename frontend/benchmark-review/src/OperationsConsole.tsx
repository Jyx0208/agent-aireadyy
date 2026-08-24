import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Accordion,
  AccordionItem,
  Button,
  DataTable,
  InlineNotification,
  Layer,
  Loading,
  Pagination,
  ProgressBar,
  ProgressIndicator,
  ProgressStep,
  Search,
  SkeletonText,
  Tab,
  TabList,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  Tag,
  Tile,
} from "@carbon/react";
import {
  useDeferredValue,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  cancelOperationsJob,
  getOperationsBatches,
  getOperationsFile,
  getOperationsFiles,
  getOperationsReviews,
  getOperationsTerms,
  getOperationsWorkers,
  operationsTerminal,
  resumeOperationsJob,
  type OperationsBatch,
  type OperationsEvent,
  type OperationsFile,
  type OperationsJob,
  type OperationsReview,
  type OperationsTerm,
} from "./operations-api";
import { describeOperationsEvent } from "./operations-event-copy";
import {
  getDiscoveryBatchHandoff,
  type DiscoveryBatchHandoff,
  type WorkflowRecord,
} from "./workflow-api";
import { useOperationsJob } from "./use-operations-job";

export type OperationsTab =
  | "overview"
  | "search"
  | "reviews"
  | "files"
  | "batches"
  | "events"
  | "chat";

type Props = {
  jobId: string;
  activeTab: OperationsTab;
  onTabChange: (tab: OperationsTab) => void;
  onSeedBatchInputs?: (handoff: DiscoveryBatchHandoff) => void;
};

const tabs: Array<{ id: OperationsTab; label: string }> = [
  { id: "overview", label: "总览" },
  { id: "search", label: "检索词" },
  { id: "reviews", label: "项目审查" },
  { id: "files", label: "文件评审" },
  { id: "batches", label: "交付批次" },
  { id: "events", label: "运行事件" },
  { id: "chat", label: "任务对话" },
];

const CARBON_MD_MEDIA_QUERY = "(max-width: 42rem)";

const projectJudgmentReason = (review: OperationsReview) => {
  const recordedReason = review.reasons.find(
    (reason) => typeof reason === "string" && reason.trim().length > 0,
  );
  if (recordedReason) return recordedReason.trim();

  const evidence = review.evidence_summary || {};
  for (const key of ["explanation", "selection_reason", "judgment_reason"]) {
    const value = evidence[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }

  if (review.status === "running") return "大模型正在判断，请稍候。";
  return "文件清单已检查，等待大模型生成判断理由。";
};

const hasProjectJudgment = (review: OperationsReview) =>
  review.reasons.some(
    (reason) => typeof reason === "string" && reason.trim().length > 0,
  ) ||
  review.score != null ||
  review.confidence != null;

const asRecord = (value: unknown): WorkflowRecord =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as WorkflowRecord)
    : {};

const stringList = (value: unknown) =>
  Array.isArray(value)
    ? value.map((item) => String(item || "").trim()).filter(Boolean)
    : [];

const metadataFieldLabel = (value: string) =>
  ({
    experimentTypes: "实验类型",
    keywords: "关键词",
    sampleProcessing: "样本处理说明",
    dataProcessing: "数据处理说明",
    semantic_metadata: "语义 metadata",
    "structured:organisms.accession": "物种编号",
    "structured:organisms.name": "物种名称",
    "structured:taxon": "分类学编号",
  })[value] || value;

const evidenceRefLabel = (value: string) =>
  ({
    acquisition_mode: "采集模式",
    data_processing_excerpt: "数据处理说明",
    evidence_level_counts: "证据层级统计",
    immunopeptide_evidence_terms: "免疫肽证据词",
    instrument_names: "仪器名称",
    project_description_excerpt: "项目描述",
    project_publication_date: "发布日期",
    project_title: "项目标题",
    sample_processing_excerpt: "样本处理说明",
    selected_file_examples: "文件示例",
    species: "物种",
    validity_status_counts: "有效性统计",
    sdrf: "SDRF",
  })[value] || value.replaceAll("_", " ");

const fileRoleLabel = (value: string) =>
  ({
    raw_acquisition: "原始采集文件",
    converted_peaklist: "转换峰表",
    search_result: "检索结果",
    report_table: "报告表格",
    metadata: "元数据文件",
    unknown: "未识别类型",
  })[value] || value.replaceAll("_", " ");

const missingColumnLabel = (value: string) =>
  ({
    cell_line: "细胞系",
    organism: "物种",
    disease: "疾病",
    treatment: "处理条件",
    control: "对照信息",
    assay: "实验方法",
    fraction: "分级信息",
  })[value] || value.replaceAll("_", " ");

const filterReasonLabel = (value: string) => {
  const [code, detail] = value.split(":", 2);
  if (code === "unsupported_file_role") {
    return `${fileRoleLabel(detail || "unknown")}不是本任务的采集文件或峰表`;
  }
  return value.replaceAll("_", " ");
};

function ChipList({
  items,
  empty = "暂无记录",
}: {
  items: string[];
  empty?: string;
}) {
  if (!items.length) return <p className="ops-muted">{empty}</p>;
  return (
    <div className="ops-chip-list">
      {items.map((item) => <Tag size="sm" type="cool-gray" key={item}>{item}</Tag>)}
    </div>
  );
}

function CountList({
  values,
  label,
}: {
  values: WorkflowRecord;
  label: (key: string) => string;
}) {
  const rows = Object.entries(values).filter(([, value]) => Number(value || 0) > 0);
  if (!rows.length) return <p className="ops-muted">暂无记录</p>;
  return (
    <dl className="ops-count-list">
      {rows.map(([key, value]) => (
        <div key={key}><dt>{label(key)}</dt><dd>{Number(value)}</dd></div>
      ))}
    </dl>
  );
}

function ProjectMetadataSummary({ metadata }: { metadata: WorkflowRecord }) {
  const sdrf = asRecord(metadata.sdrf);
  const matchCounts = asRecord(sdrf.match_status_counts);
  const examples = Array.isArray(sdrf.file_match_examples)
    ? sdrf.file_match_examples.map(asRecord).filter((item) => item.file_name)
    : [];
  const sdrfStatus = String(metadata.sdrf_status || sdrf.status || "unknown");
  const statusLabels: Record<string, string> = {
    available: "已找到",
    not_found: "未找到",
    invalid: "解析失败",
    unknown: "未知",
  };
  const species = stringList(metadata.species).map((item) =>
    item.toLowerCase() === "human" ? "人类（human）" : item,
  );
  const hla = stringList(metadata.hla_class).map((item) =>
    item === "class_i" ? "HLA I 类" : item === "class_ii" ? "HLA II 类" : item,
  );
  const acquisition = String(metadata.acquisition_mode || "unknown").toUpperCase();
  const validity = String(metadata.validity_status || "unknown");

  return (
    <div className="ops-product-summary">
      <dl className="ops-fact-grid">
        <div><dt>物种</dt><dd>{species.join("、") || "未记录"}</dd></div>
        <div><dt>采集模式</dt><dd>{acquisition === "UNKNOWN" ? "未确定" : acquisition}</dd></div>
        <div><dt>HLA 类型</dt><dd>{hla.join("、") || "未记录"}</dd></div>
        <div><dt>项目有效性</dt><dd>{validity === "valid" ? "通过基础校验" : validity}</dd></div>
        <div><dt>SDRF</dt><dd>{statusLabels[sdrfStatus] || sdrfStatus}</dd></div>
        <div><dt>SDRF 行数</dt><dd>{Number(metadata.sdrf_row_count || sdrf.row_count || 0)}</dd></div>
      </dl>

      <section className="ops-detail-section">
        <h3>质谱仪器</h3>
        <ChipList items={stringList(metadata.instrument_names)} empty="PRIDE metadata 未记录仪器" />
      </section>
      <section className="ops-detail-section">
        <h3>判断用到的 metadata 字段</h3>
        <ChipList items={stringList(metadata.evidence_fields).map(metadataFieldLabel)} />
      </section>
      {metadata.sample_processing_excerpt ? (
        <section className="ops-detail-section ops-prose-block">
          <h3>样本如何处理</h3><p>{String(metadata.sample_processing_excerpt)}</p>
        </section>
      ) : null}
      {metadata.data_processing_excerpt ? (
        <section className="ops-detail-section ops-prose-block">
          <h3>数据如何处理</h3><p>{String(metadata.data_processing_excerpt)}</p>
        </section>
      ) : null}

      <section className="ops-detail-section ops-sdrf-card">
        <div className="ops-detail-heading">
          <div><h3>SDRF 样本表</h3><p>用于把每个文件对应到样本、疾病、处理和分级信息。</p></div>
          <Tag size="sm" type={sdrfStatus === "available" ? "green" : "purple"}>
            {statusLabels[sdrfStatus] || sdrfStatus}
          </Tag>
        </div>
        {sdrfStatus === "not_found" ? (
          <p className="ops-callout-text">PRIDE 中没有找到 SDRF；文件仍可评审，但样本条件只能依赖项目描述，不能做精确的文件—样本对应。</p>
        ) : null}
        <dl className="ops-count-list ops-count-list--three">
          <div><dt>已匹配</dt><dd>{Number(matchCounts.matched || 0)}</dd></div>
          <div><dt>未匹配文件</dt><dd>{Number(matchCounts.no_file_match || 0)}</dd></div>
          <div><dt>无 SDRF</dt><dd>{Number(matchCounts.no_sdrf || 0)}</dd></div>
        </dl>
        {stringList(sdrf.missing_columns).length ? (
          <div><h4>缺少的样本字段</h4><ChipList items={stringList(sdrf.missing_columns).map(missingColumnLabel)} /></div>
        ) : null}
        {examples.length ? (
          <div className="ops-compact-table-wrap">
            <table className="ops-compact-table">
              <thead><tr><th>文件示例</th><th>SDRF 对应情况</th><th>匹配行</th></tr></thead>
              <tbody>{examples.map((item, index) => (
                <tr key={`${String(item.file_name)}-${index}`}>
                  <td>{String(item.file_name)}</td>
                  <td>{String(item.status) === "no_sdrf" ? "无 SDRF" : String(item.status || "未知")}</td>
                  <td>{Number(item.matched_row_count || 0)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : null}
        {stringList(sdrf.conflicts).length ? <div><h4>冲突</h4><ul>{stringList(sdrf.conflicts).map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
        {stringList(sdrf.errors).length ? <div><h4>解析问题</h4><ul>{stringList(sdrf.errors).map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
      </section>
    </div>
  );
}

function ProjectEvidenceSummary({ evidence }: { evidence: WorkflowRecord }) {
  const refs = stringList(evidence.available_evidence_refs).map(evidenceRefLabel);
  const examples = stringList(evidence.selected_file_examples);
  const roles = asRecord(evidence.file_role_counts);
  const reasons = asRecord(evidence.filter_reason_counts);
  const missing = stringList(evidence.missing_information);
  const limitations = stringList(evidence.limitations);
  const selectionReason = String(
    evidence.selection_reason || evidence.explanation || evidence.judgment_reason || "",
  ).trim();

  return (
    <div className="ops-product-summary">
      {selectionReason ? <section className="ops-detail-section ops-prose-block"><h3>证据结论</h3><p>{selectionReason}</p></section> : null}
      <section className="ops-detail-section">
        <h3>文件类型统计</h3>
        <CountList values={roles} label={fileRoleLabel} />
      </section>
      <section className="ops-detail-section">
        <h3>文件被过滤的原因</h3>
        <CountList values={reasons} label={filterReasonLabel} />
      </section>
      <section className="ops-detail-section">
        <h3>可追溯证据来源</h3>
        <ChipList items={refs} empty="暂无额外证据引用" />
      </section>
      {examples.length ? (
        <section className="ops-detail-section">
          <h3>通过初筛的文件示例</h3>
          <ul className="ops-file-example-list">{examples.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>
      ) : null}
      {missing.length ? <section className="ops-detail-section"><h3>仍缺少的信息</h3><ul>{missing.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
      {limitations.length ? <section className="ops-detail-section"><h3>限制与注意事项</h3><ul>{limitations.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
      {!selectionReason && !refs.length && !examples.length && !Object.keys(roles).length && !Object.keys(reasons).length ? (
        <p className="ops-muted">暂无额外证据摘要；核心依据请查看项目 metadata 和判断理由。</p>
      ) : null}
    </div>
  );
}

function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(
    () => typeof window !== "undefined" && window.matchMedia(query).matches,
  );

  useEffect(() => {
    const mediaQuery = window.matchMedia(query);
    const update = () => setMatches(mediaQuery.matches);
    update();
    mediaQuery.addEventListener("change", update);
    return () => mediaQuery.removeEventListener("change", update);
  }, [query]);

  return matches;
}

const phaseIndex = (job: OperationsJob) => {
  const progress = job.progress;
  const reviewComplete =
    progress.candidate_count > 0 &&
    progress.reviewed_count >= progress.candidate_count;
  if (job.status === "completed") return 3;
  if (
    job.phase === "finalizing" ||
    progress.batch_count > 0 ||
    reviewComplete
  ) {
    return 2;
  }
  if (
    job.phase === "reviewing" ||
    progress.reviewed_count > 0 ||
    progress.candidate_count > 0
  ) {
    return 1;
  }
  return 0;
};

const statusLabel = (value: string) =>
  ({
    queued: "等待执行",
    searching: "检索候选",
    reviewing: "项目审查",
    finalizing: "冻结结果",
    completed: "已完成",
    blocked: "需要处理",
    failed: "运行失败",
    cancelled: "已停止",
    interrupted: "可恢复",
  })[value] || value;

const statusTagType = (
  value: string,
): "blue" | "green" | "red" | "gray" | "purple" | "magenta" => {
  if (value === "completed") return "green";
  if (value === "failed") return "red";
  if (value === "blocked" || value === "interrupted") return "purple";
  if (value === "cancelled") return "gray";
  return "blue";
};

const formatTime = (value?: string | null) => {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(date);
};

const formatBytes = (value?: number | null) => {
  let current = Number(value || 0);
  if (!Number.isFinite(current) || current <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;
  while (current >= 1024 && unit < units.length - 1) {
    current /= 1024;
    unit += 1;
  }
  return `${current >= 10 || unit === 0 ? current.toFixed(0) : current.toFixed(1)} ${units[unit]}`;
};

const reviewStepLabel = (value: unknown) =>
  ({
    metadata_read: "读取项目 metadata",
    metadata_score: "解析并评分项目 metadata",
    scientific_gate: "检查科学硬约束",
    file_inventory: "拉取完整文件清单",
    sdrf_lookup: "查找并解析 SDRF",
    sdrf_download: "下载 SDRF",
    file_filter: "逐文件分类与筛选",
    completed: "审查完成",
    failed: "审查失败",
    waiting: "等待项目",
  })[String(value || "")] || String(value || "等待项目");

function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: number | string;
  detail: string;
}) {
  return (
    <Tile className="ops-metric">
      <span className="ops-metric__label">{label}</span>
      <strong>{value}</strong>
      <span className="ops-metric__detail">{detail}</span>
    </Tile>
  );
}

function SearchTerms({ jobId }: { jobId: string }) {
  const query = useQuery({
    queryKey: ["operations-job-detail", jobId, "terms"],
    queryFn: ({ signal }) => getOperationsTerms(jobId, signal),
  });
  if (query.isLoading) return <SkeletonText paragraph lineCount={8} />;
  if (query.error) {
    return (
      <InlineNotification
        kind="error"
        title="检索词读取失败"
        subtitle={String(query.error)}
        hideCloseButton
      />
    );
  }
  const rows = (query.data?.items || []).map((term) => ({
    id: String(term.position),
    position: `${term.position}/${query.data?.total || 0}`,
    term: term.term,
    role: term.role === "primary_theme" ? "核心主题" : "主题同义词",
    status: statusLabel(term.status),
    page_count: term.page_count,
    raw_count: term.raw_count,
    unique_count: term.unique_count,
    error: term.error?.message || "—",
  }));
  const headers = [
    { key: "position", header: "顺序" },
    { key: "term", header: "提交给 PRIDE" },
    { key: "role", header: "角色" },
    { key: "status", header: "状态" },
    { key: "page_count", header: "已翻页" },
    { key: "raw_count", header: "原始返回" },
    { key: "unique_count", header: "去重新增" },
    { key: "error", header: "失败原因" },
  ];
  return (
    <div className="ops-section-stack">
      <Tile className="ops-search-page-summary">
        <strong>
          本任务累计翻页{" "}
          {(query.data?.items || []).reduce(
            (total, term) => total + Number(term.page_count || 0),
            0,
          )}{" "}
          页
        </strong>
        <span>
          每个关键词分别记录已翻页数、原始返回、去重新增和失败原因。
        </span>
      </Tile>
      <DataTable rows={rows} headers={headers} size="lg" useZebraStyles>
      {({
        rows: tableRows,
        headers: tableHeaders,
        getHeaderProps,
        getRowProps,
        getTableProps,
      }) => (
        <div className="ops-table-wrap">
          <Table {...getTableProps()}>
            <TableHead>
              <TableRow>
                {tableHeaders.map((header) => {
                  const headerProps = getHeaderProps({ header });
                  const { key, ...rest } = headerProps;
                  return <TableHeader key={key} {...rest}>{header.header}</TableHeader>;
                })}
              </TableRow>
            </TableHead>
            <TableBody>
              {tableRows.map((row) => {
                const rowProps = getRowProps({ row });
                const { key, ...rest } = rowProps;
                return (
                  <TableRow key={key} {...rest}>
                    {row.cells.map((cell) => <TableCell key={cell.id}>{cell.value}</TableCell>)}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
      </DataTable>
    </div>
  );
}

function WorkerBoard({ jobId }: { jobId: string }) {
  const query = useQuery({
    queryKey: ["operations-job-detail", jobId, "workers"],
    queryFn: ({ signal }) => getOperationsWorkers(jobId, signal),
    // SSE invalidates this projection on every committed job event. This
    // interval is only reconciliation for a disconnected browser.
    refetchInterval: 5_000,
    refetchIntervalInBackground: true,
  });
  return (
    <div className="ops-worker-grid" aria-label="并行审查 worker">
      {(query.data?.items || []).map((worker) => (
        <Layer key={worker.slot} className="ops-worker">
          <div className="ops-worker__heading">
            <strong>Worker {worker.slot}</strong>
            <Tag size="sm" type={worker.status === "busy" ? "blue" : "gray"}>
              {worker.status === "busy" ? "处理中" : "空闲"}
            </Tag>
          </div>
          <span>{worker.project_accession || "等待候选项目"}</span>
          <small>{reviewStepLabel(worker.step)}</small>
        </Layer>
      ))}
      {!query.isLoading && !query.data?.items.length ? (
        <p className="ops-empty">尚未进入并行审查阶段。</p>
      ) : null}
    </div>
  );
}

function Reviews({
  jobId,
  onSelect,
}: {
  jobId: string;
  onSelect: (review: OperationsReview) => void;
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const query = useQuery({
    queryKey: [
      "operations-job-detail",
      jobId,
      "reviews",
      page,
      pageSize,
      deferredSearch,
    ],
    queryFn: ({ signal }) =>
      getOperationsReviews(
        jobId,
        {
          page,
          pageSize,
          query: deferredSearch,
          sort: "position",
          direction: "asc",
        },
        signal,
      ),
    placeholderData: (previous) => previous,
  });
  const byId = new Map(
    (query.data?.items || []).map((review) => [String(review.id), review]),
  );
  const rows = (query.data?.items || []).map((review) => {
    const judged = hasProjectJudgment(review);
    return {
      id: String(review.id),
      accession: review.accession,
      title: review.title || "—",
      status: review.status === "running"
        ? `Worker ${review.worker_slot || "—"} · ${reviewStepLabel(review.current_step)}`
        : !judged && review.status === "completed"
          ? "文件检查完成，等待大模型判断"
          : statusLabel(review.status),
      decision: judged ? (review.decision || "pending") : "待判断",
      score: review.score == null ? "—" : review.score.toFixed(1),
      confidence:
        review.confidence == null ? "—" : review.confidence.toFixed(2),
      usable: review.usable_file_count,
      reason: projectJudgmentReason(review),
    };
  });
  const headers = [
    { key: "accession", header: "项目" },
    { key: "title", header: "标题" },
    { key: "status", header: "当前步骤" },
    { key: "decision", header: "判断" },
    { key: "score", header: "分数" },
    { key: "confidence", header: "置信度" },
    { key: "usable", header: "可用文件" },
    { key: "reason", header: "判断理由" },
  ];
  return (
    <div className="ops-section-stack">
      <Search
        id="project-review-search"
        labelText="搜索项目编号、标题或理由"
        placeholder="搜索项目编号、标题或失败原因"
        value={search}
        onChange={(event) => {
          setSearch(event.currentTarget.value);
          setPage(1);
        }}
      />
      {query.error ? (
        <InlineNotification
          kind="error"
          title="项目审查读取失败"
          subtitle={String(query.error)}
          hideCloseButton
        />
      ) : null}
      <DataTable rows={rows} headers={headers} size="lg" useZebraStyles>
        {({
          rows: tableRows,
          headers: tableHeaders,
          getHeaderProps,
          getRowProps,
          getTableProps,
        }) => (
          <div className="ops-table-wrap">
            <Table {...getTableProps()}>
              <TableHead>
                <TableRow>
                  {tableHeaders.map((header) => {
                    const headerProps = getHeaderProps({ header });
                    const { key, ...rest } = headerProps;
                    return <TableHeader key={key} {...rest}>{header.header}</TableHeader>;
                  })}
                </TableRow>
              </TableHead>
              <TableBody>
                {tableRows.map((row) => {
                  const rowProps = getRowProps({ row });
                  const { key, ...rest } = rowProps;
                  const review = byId.get(row.id);
                  return (
                    <TableRow key={key} {...rest}>
                      {row.cells.map((cell, index) => (
                        <TableCell key={cell.id}>
                          {index === 0 && review ? (
                            <Button
                              size="sm"
                              kind="ghost"
                              onClick={() => onSelect(review)}
                            >
                              {cell.value}
                            </Button>
                          ) : cell.value}
                        </TableCell>
                      ))}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </DataTable>
      <Pagination
        page={page}
        pageSize={pageSize}
        pageSizes={[10, 25, 50, 100]}
        totalItems={query.data?.total || 0}
        onChange={({ page: nextPage, pageSize: nextSize }) => {
          setPage(nextPage);
          setPageSize(nextSize);
        }}
      />
    </div>
  );
}

function Files({ jobId }: { jobId: string }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const query = useQuery({
    queryKey: [
      "operations-job-detail",
      jobId,
      "files",
      page,
      pageSize,
      deferredSearch,
    ],
    queryFn: ({ signal }) =>
      getOperationsFiles(
        jobId,
        {
          page,
          pageSize,
          eligible: true,
          query: deferredSearch,
        },
        signal,
      ),
    placeholderData: (previous) => previous,
  });
  const rows = (query.data?.items || []).map((file) => ({
    id: String(file.id),
    project: file.project_accession,
    file: file.file_name,
    role: file.file_role || file.file_category || "—",
    format: file.file_format || "—",
    acquisition: file.acquisition_mode || "unknown",
    size: formatBytes(file.size_bytes),
    status: file.status,
    reason: file.reason_code || file.reasons[0] || "—",
  }));
  const headers = [
    { key: "project", header: "项目" },
    { key: "file", header: "文件" },
    { key: "role", header: "文件角色" },
    { key: "format", header: "格式" },
    { key: "acquisition", header: "采集方式" },
    { key: "size", header: "大小" },
    { key: "status", header: "状态" },
    { key: "reason", header: "依据" },
  ];
  return (
    <div className="ops-section-stack">
      <Search
        id="usable-file-search"
        labelText="搜索文件"
        placeholder="搜索项目编号、文件名或格式"
        value={search}
        onChange={(event) => {
          setSearch(event.currentTarget.value);
          setPage(1);
        }}
      />
      <DataTable rows={rows} headers={headers} size="lg" useZebraStyles>
        {({
          rows: tableRows,
          headers: tableHeaders,
          getHeaderProps,
          getRowProps,
          getTableProps,
        }) => (
          <div className="ops-table-wrap">
            <Table {...getTableProps()}>
              <TableHead>
                <TableRow>
                  {tableHeaders.map((header) => {
                    const headerProps = getHeaderProps({ header });
                    const { key, ...rest } = headerProps;
                    return <TableHeader key={key} {...rest}>{header.header}</TableHeader>;
                  })}
                </TableRow>
              </TableHead>
              <TableBody>
                {tableRows.map((row) => {
                  const rowProps = getRowProps({ row });
                  const { key, ...rest } = rowProps;
                  return (
                    <TableRow key={key} {...rest}>
                      {row.cells.map((cell) => <TableCell key={cell.id}>{cell.value}</TableCell>)}
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </DataTable>
      <Pagination
        page={page}
        pageSize={pageSize}
        pageSizes={[10, 25, 50, 100]}
        totalItems={query.data?.total || 0}
        onChange={({ page: nextPage, pageSize: nextSize }) => {
          setPage(nextPage);
          setPageSize(nextSize);
        }}
      />
    </div>
  );
}

const fileReviewViews = [
  { id: "all", label: "全部" },
  { id: "unreviewed", label: "未评审" },
  { id: "reviewing", label: "正在评审" },
  { id: "reviewed", label: "已评审" },
  { id: "selected", label: "已入选" },
  { id: "investigate", label: "待核查" },
  { id: "excluded", label: "未纳入" },
  { id: "error", label: "错误" },
] as const;

function FileReviewBoard({ jobId }: { jobId: string }) {
  const [pageSize, setPageSize] = useState(25);
  const [search, setSearch] = useState("");
  const [viewIndex, setViewIndex] = useState(0);
  const [cursors, setCursors] = useState<number[]>([0]);
  const [selectedFileId, setSelectedFileId] = useState("");
  const deferredSearch = useDeferredValue(search);
  const view = fileReviewViews[viewIndex].id;
  const cursor = cursors.at(-1) || 0;
  const reviewStatus = ["unreviewed", "reviewing", "reviewed", "error"].includes(view)
    ? view
    : "";
  const decision = view === "selected"
    ? "include"
    : view === "investigate"
      ? "investigate"
      : view === "excluded"
        ? "exclude"
        : "";

  useEffect(() => {
    setCursors([0]);
    setSelectedFileId("");
  }, [deferredSearch, pageSize, view]);

  const query = useQuery({
    queryKey: ["operations-job-detail", jobId, "file-review", cursor, pageSize, view, deferredSearch],
    queryFn: ({ signal }) => getOperationsFiles(jobId, {
      page: 1,
      pageSize,
      cursor,
      reviewStatus,
      decision,
      query: deferredSearch,
      sort: "id",
    }, signal),
    placeholderData: (previous) => previous,
  });
  const detailQuery = useQuery({
    queryKey: ["operations-job-detail", jobId, "file", selectedFileId],
    queryFn: ({ signal }) => getOperationsFile(jobId, selectedFileId, signal),
    enabled: Boolean(selectedFileId),
  });
  const filesByRowId = new Map((query.data?.items || []).map((file) => [String(file.id), file]));
  const rows = (query.data?.items || []).map((file) => ({
    id: String(file.id),
    project: file.project_accession,
    file: file.file_name,
    role: file.selection_role || file.file_role || "—",
    status: file.review_status || file.status,
    decision: file.decision || "—",
    reason: file.reason_preview || file.reason_code || "点击文件名查看理由",
  }));
  const headers = [
    { key: "project", header: "项目" },
    { key: "file", header: "文件" },
    { key: "role", header: "文件角色" },
    { key: "status", header: "评审状态" },
    { key: "decision", header: "判断" },
    { key: "reason", header: "文件级理由" },
  ];
  const summary = query.data?.summary || {};
  const cards: Array<[string, unknown]> = [
    ["总文件", summary.total],
    ["未评审", summary.unreviewed],
    ["正在评审", summary.reviewing],
    ["已入选", summary.selected],
    ["待核查", summary.investigate],
    ["未纳入", summary.excluded],
  ];

  return (
    <div className="ops-section-stack">
      <div className="ops-file-summary" aria-label="文件评审进度">
        {cards.map(([label, value]) => (
          <Tile
            key={String(label)}
            className={label === "正在评审" && Number(value || 0) > 0
              ? "ops-file-summary__active"
              : undefined}
          >
            <p className="ops-eyebrow">{label}</p>
            <div className="ops-file-summary__value">
              {label === "正在评审" && Number(value || 0) > 0
                ? <Loading small withOverlay={false} description="文件正在评审" />
                : null}
              <strong>{Number(value || 0)}</strong>
            </div>
          </Tile>
        ))}
      </div>
      <Tabs selectedIndex={viewIndex} onChange={({ selectedIndex }) => setViewIndex(selectedIndex)}>
        <TabList aria-label="按文件评审状态查看">
          {fileReviewViews.map((item) => <Tab key={item.id}>{item.label}</Tab>)}
        </TabList>
      </Tabs>
      <Search
        id="file-review-search"
        labelText="搜索文件"
        placeholder="搜索项目编号、文件名或格式"
        value={search}
        onChange={(event) => setSearch(event.currentTarget.value)}
      />
      <DataTable rows={rows} headers={headers} size="lg" useZebraStyles>
        {({ rows: tableRows, headers: tableHeaders, getHeaderProps, getRowProps, getTableProps }) => (
          <div className="ops-table-wrap">
            <Table {...getTableProps()}>
              <TableHead><TableRow>
                {tableHeaders.map((header) => {
                  const props = getHeaderProps({ header });
                  const { key, ...rest } = props;
                  return <TableHeader key={key} {...rest}>{header.header}</TableHeader>;
                })}
              </TableRow></TableHead>
              <TableBody>
                {tableRows.map((row) => {
                  const props = getRowProps({ row });
                  const { key, ...rest } = props;
                  return <TableRow key={key} {...rest}>
                    {row.cells.map((cell, index) => <TableCell key={cell.id}>
                      {index === 1 ? (
                        <Button size="sm" kind="ghost" onClick={() => {
                          const file = filesByRowId.get(row.id);
                          setSelectedFileId(String(file?.file_id || ""));
                        }}>{cell.value}</Button>
                      ) : cell.value}
                    </TableCell>)}
                  </TableRow>;
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </DataTable>
      <div className="ops-action-row">
        <Button kind="secondary" size="sm" disabled={cursors.length === 1} onClick={() => setCursors((values) => values.slice(0, -1))}>上一页</Button>
        <span>第 {cursors.length} 页，每页 {pageSize} 个文件</span>
        <Button kind="secondary" size="sm" disabled={!query.data?.has_next || !query.data.next_cursor} onClick={() => {
          const next = Number(query.data?.next_cursor || 0);
          if (next) setCursors((values) => [...values, next]);
        }}>下一页</Button>
        {[25, 50, 100].map((size) => (
          <Button key={size} kind={pageSize === size ? "primary" : "ghost"} size="sm" onClick={() => setPageSize(size)}>{size}/页</Button>
        ))}
      </div>
      {selectedFileId ? (
        <FileReasonPanel file={detailQuery.data as OperationsFile | undefined} loading={detailQuery.isLoading} onClose={() => setSelectedFileId("")} />
      ) : null}
    </div>
  );
}

function FileReasonPanel({ file, loading, onClose }: { file?: OperationsFile; loading: boolean; onClose: () => void }) {
  return (
    <aside className="ops-evidence-panel" aria-label="文件判断详情">
      <Layer className="ops-evidence-panel__inner">
        <div className="ops-evidence-panel__header">
          <div><p className="ops-eyebrow">FILE REVIEW</p><h2>{file?.file_name || "正在读取…"}</h2></div>
          <Button size="sm" kind="ghost" onClick={onClose}>关闭</Button>
        </div>
        {loading ? <SkeletonText paragraph lineCount={5} /> : <>
          <dl className="ops-evidence-summary">
            <div><dt>项目</dt><dd>{file?.project_accession || "—"}</dd></div>
            <div><dt>评审状态</dt><dd>{file?.review_status || "—"}</dd></div>
            <div><dt>判断</dt><dd>{file?.decision || "—"}</dd></div>
            <div><dt>等级</dt><dd>{file?.grade ?? "—"}</dd></div>
            <div><dt>置信度</dt><dd>{file?.confidence ?? "—"}</dd></div>
            <div><dt>理由状态</dt><dd>{file?.reason_status || "—"}</dd></div>
          </dl>
          <h3>{file?.decision === "include" ? "入选理由" : "未入选理由"}</h3>
          <p>{file?.reason_text || "该文件尚未完成评审，因此还没有理由。"}</p>
          {file?.companion_file_ids?.length ? <p>配套文件：{file.companion_file_ids.join("、")}</p> : null}
          <Accordion align="start">
            <AccordionItem title="限制与注意事项">
              {file?.limitations?.length ? <ul>{file.limitations.map((item) => <li key={item}>{item}</li>)}</ul> : <p>没有额外限制。</p>}
            </AccordionItem>
            <AccordionItem title="证据记录"><pre>{JSON.stringify(file?.evidence || {}, null, 2)}</pre></AccordionItem>
          </Accordion>
        </>}
      </Layer>
    </aside>
  );
}

function Batches({
  jobId,
  onSeedBatchInputs,
}: {
  jobId: string;
  onSeedBatchInputs?: (handoff: DiscoveryBatchHandoff) => void;
}) {
  const [busyBatch, setBusyBatch] = useState(0);
  const [error, setError] = useState("");
  const query = useQuery({
    queryKey: ["operations-job-detail", jobId, "batches"],
    queryFn: ({ signal }) => getOperationsBatches(jobId, signal),
  });
  const send = async (batch: OperationsBatch) => {
    setBusyBatch(batch.batch_index);
    setError("");
    try {
      const handoff = await getDiscoveryBatchHandoff(jobId, batch.batch_index);
      onSeedBatchInputs?.(handoff);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusyBatch(0);
    }
  };
  return (
    <div className="ops-section-stack">
      {error ? (
        <InlineNotification
          kind="error"
          title="批次交接失败"
          subtitle={error}
          onCloseButtonClick={() => setError("")}
        />
      ) : null}
      <div className="ops-batch-grid">
        {(query.data?.items || []).map((batch) => (
          <Tile key={batch.batch_id} className="ops-batch-card">
            <div>
              <p className="ops-eyebrow">文件批次 {batch.batch_index}</p>
              <h3>{batch.file_count} 个文件</h3>
              <p>
                {batch.project_count} 个项目 · 累计已交付{" "}
                {batch.cumulative_file_count} 个文件
              </p>
            </div>
            <div className="ops-action-row">
              <Button
                size="sm"
                kind="secondary"
                href={`/api/discovery/jobs/${encodeURIComponent(jobId)}/batches/${batch.batch_index}/download`}
              >
                下载清单
              </Button>
              <Button
                size="sm"
                disabled={busyBatch === batch.batch_index}
                onClick={() => void send(batch)}
              >
                送入批量处理
              </Button>
            </div>
          </Tile>
        ))}
      </div>
      {!query.isLoading && !query.data?.items.length ? (
        <p className="ops-empty">
          尚未凑满 500 个可交付文件；尾批次会在任务结束时冻结。
        </p>
      ) : null}
    </div>
  );
}

function EventFeed({
  events,
  overview = false,
}: {
  events: OperationsEvent[];
  overview?: boolean;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);
  const [showTechnical, setShowTechnical] = useState(false);
  const describedEvents = useMemo(
    () =>
      events.map((event) => ({
        event,
        copy: describeOperationsEvent(event),
      })),
    [events],
  );
  const technicalCount = describedEvents.filter(
    ({ copy }) => copy.technical,
  ).length;
  const visibleEvents = useMemo(
    () =>
      describedEvents.filter(
        ({ copy }) => !copy.technical || (!overview && showTechnical),
      ),
    [describedEvents, overview, showTechnical],
  );
  const virtualizer = useVirtualizer({
    count: visibleEvents.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 92,
    overscan: 12,
  });
  const lastSequence = visibleEvents.at(-1)?.event.sequence || 0;

  useEffect(() => {
    if (!following || !parentRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      const element = parentRef.current;
      if (!element) return;
      element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [following, lastSequence]);

  return (
    <div className="ops-event-stream">
      {!overview && technicalCount > 0 ? (
        <div className="ops-event-stream__toolbar">
          <span>
            默认只显示检索、翻页、去重和审查进度；另有 {technicalCount} 条底层技术事件。
          </span>
          <Button
            size="sm"
            kind="ghost"
            onClick={() => setShowTechnical((value) => !value)}
          >
            {showTechnical
              ? "收起技术详情"
              : `展开技术详情（${technicalCount}）`}
          </Button>
        </div>
      ) : null}
      <div
        ref={parentRef}
        className={`ops-event-feed${overview ? " ops-event-feed--overview" : ""}`}
        role="log"
        aria-label="运行事件"
        aria-live="polite"
        onScroll={(event) => {
          const element = event.currentTarget;
          setFollowing(
            element.scrollHeight - element.scrollTop - element.clientHeight < 48,
          );
        }}
      >
        <div
          className="ops-event-feed__canvas"
          style={{ height: `${virtualizer.getTotalSize()}px` }}
        >
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const { event, copy } = visibleEvents[virtualRow.index];
            return (
              <article
                key={event.sequence}
                className={`ops-event ops-event--${event.level}`}
                ref={virtualizer.measureElement}
                data-index={virtualRow.index}
                style={{ transform: `translateY(${virtualRow.start}px)` }}
              >
                <div className="ops-event__meta">
                  <span>#{event.sequence}</span>
                  <strong>{copy.actor}</strong>
                  <Tag size="sm" type={event.level === "error" ? "red" : event.level === "warning" ? "purple" : "gray"}>
                    {copy.typeLabel}
                  </Tag>
                  <time>{formatTime(event.created_at)}</time>
                </div>
                <p>{copy.title}</p>
                {copy.detail ? <small>{copy.detail}</small> : null}
              </article>
            );
          })}
        </div>
        {!following ? (
          <div className="ops-event-follow">
            <Button
              size="sm"
              kind="primary"
              onClick={() => setFollowing(true)}
            >
              跟随最新事件
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function EvidencePanel({
  review,
  onClose,
}: {
  review: OperationsReview;
  onClose: () => void;
}) {
  const rawSteps = review.evidence_summary.steps;
  const steps = Array.isArray(rawSteps)
    ? rawSteps.filter(
        (item): item is WorkflowRecord =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
  return (
    <aside className="ops-evidence-panel" aria-label={`${review.accession} 项目证据`}>
      <Layer className="ops-evidence-panel__inner">
        <div className="ops-evidence-panel__header">
          <div>
            <p className="ops-eyebrow">PROJECT EVIDENCE</p>
            <h2>{review.accession}</h2>
          </div>
          <Button size="sm" kind="ghost" onClick={onClose}>关闭</Button>
        </div>
        <p>{review.title || "未提供项目标题"}</p>
        <dl className="ops-evidence-summary">
          <div><dt>判断</dt><dd>{review.decision}</dd></div>
          <div><dt>分数</dt><dd>{review.score ?? "—"}</dd></div>
          <div><dt>置信度</dt><dd>{review.confidence ?? "—"}</dd></div>
          <div><dt>可用文件</dt><dd>{review.usable_file_count}</dd></div>
          <div><dt>耗时</dt><dd>{review.elapsed_ms ? `${review.elapsed_ms} ms` : "—"}</dd></div>
        </dl>
        <Accordion align="start">
          <AccordionItem title={`逐步审查轨迹（${steps.length}）`} open>
            {steps.length ? (
              <ol className="ops-evidence-steps">
                {steps.map((step, index) => (
                  <li key={`${String(step.step)}-${index}`}>
                    <div>
                      <strong>{reviewStepLabel(step.step)}</strong>
                      <Tag
                        size="sm"
                        type={
                          step.status === "failed"
                            ? "red"
                            : step.status === "completed"
                              ? "green"
                              : "blue"
                        }
                      >
                        {String(step.status || "running")}
                      </Tag>
                    </div>
                    <p>
                      {step.reason
                        ? String(step.reason)
                        : step.error
                          ? String(step.error)
                          : `耗时 ${Number(step.elapsed_ms || 0)} ms`}
                    </p>
                  </li>
                ))}
              </ol>
            ) : (
              <p>等待结构化步骤事件。</p>
            )}
          </AccordionItem>
          <AccordionItem title="入选或排除理由" open>
            {review.reasons.length ? (
              <ul>{review.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
            ) : <p>{projectJudgmentReason(review)}</p>}
          </AccordionItem>
          <AccordionItem title="项目信息与实验设计">
            <ProjectMetadataSummary metadata={review.metadata_summary} />
          </AccordionItem>
          <AccordionItem title="文件证据摘要">
            <ProjectEvidenceSummary evidence={review.evidence_summary} />
          </AccordionItem>
          <AccordionItem title="命中的检索词">
            <ChipList
              items={review.discovered_by_terms}
              empty="这条旧记录没有保存检索词；不代表项目没有命中检索。"
            />
          </AccordionItem>
        </Accordion>
      </Layer>
    </aside>
  );
}

export function OperationsConsole({
  jobId,
  activeTab,
  onTabChange,
  onSeedBatchInputs,
}: Props) {
  const useVerticalPhaseIndicator = useMediaQuery(CARBON_MD_MEDIA_QUERY);
  const queryClient = useQueryClient();
  const { job, events, connection, isLoading, error } = useOperationsJob(jobId);
  const [selectedReview, setSelectedReview] = useState<OperationsReview | null>(null);
  const [actionError, setActionError] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const activeIndex = Math.max(0, tabs.findIndex((tab) => tab.id === activeTab));
  const running = !operationsTerminal(job?.status || "");
  const latestVisibleEvent = [...events]
    .reverse()
    .map((event) => ({ event, copy: describeOperationsEvent(event) }))
    .find(({ copy }) => !copy.technical);
  const fileReviewActive = running && Boolean(
    latestVisibleEvent?.event.type.startsWith("file_"),
  );

  const percentage = useMemo(() => {
    if (!job) return 0;
    if (job.status === "completed") return 100;
    const { term_total, term_completed, candidate_count, reviewed_count } = job.progress;
    const searchRatio =
      term_total > 0
        ? Math.min(1, term_completed / term_total)
        : candidate_count > 0
          ? 1
          : 0;
    const reviewRatio =
      candidate_count > 0
        ? Math.min(1, reviewed_count / candidate_count)
        : 0;
    const fileCredit = job.progress.usable_file_count > 0 ? 10 : 0;
    const finalizingCredit =
      job.phase === "finalizing" || job.progress.batch_count > 0 ? 5 : 0;
    return Math.min(
      99,
      searchRatio * 35 +
        reviewRatio * 50 +
        fileCredit +
        finalizingCredit,
    );
  }, [job]);

  const act = async (kind: "cancel" | "resume") => {
    setActionBusy(true);
    setActionError("");
    try {
      const next =
        kind === "cancel"
          ? await cancelOperationsJob(jobId)
          : await resumeOperationsJob(jobId);
      queryClient.setQueryData(["operations-job", jobId], next);
    } catch (reason) {
      setActionError(String(reason));
    } finally {
      setActionBusy(false);
    }
  };

  if (isLoading) {
    return (
      <section className="ops-console" aria-label="当前任务控制台">
        <SkeletonText heading />
        <SkeletonText paragraph lineCount={8} />
      </section>
    );
  }
  if (error || !job) {
    return (
      <InlineNotification
        kind="error"
        title="当前任务读取失败"
        subtitle={String(error || "任务不存在")}
        hideCloseButton
      />
    );
  }

  return (
    <section className="ops-console" aria-label="当前任务控制台">
      <header className="ops-command-bar">
        <div className="ops-command-bar__identity">
          <p className="ops-eyebrow">CURRENT TASK · {job.job_id}</p>
          <h1>{job.objective || "PRIDE 数据发现任务"}</h1>
          <div className="ops-command-bar__status">
            <Tag type={statusTagType(job.status)}>{statusLabel(job.status)}</Tag>
            <span>
              实时连接：
              {connection === "live"
                ? "正常"
                : connection === "reconnecting"
                  ? "重连中"
                  : connection === "closed"
                    ? "已结束"
                    : "连接中"}
            </span>
            <span>最近心跳：{formatTime(job.heartbeat_at)}</span>
          </div>
          {running ? (
            <div className="ops-running-status" role="status" aria-live="polite">
              <Loading small withOverlay={false} description="任务仍在运行" />
              <div>
                <strong>
                  正在运行：{latestVisibleEvent?.copy.title || statusLabel(job.phase)}
                </strong>
                <span>
                  {latestVisibleEvent?.copy.detail || "后台仍在工作，新的判断完成后会自动更新。"}
                  {latestVisibleEvent?.event.created_at
                    ? ` · 更新于 ${formatTime(latestVisibleEvent.event.created_at)}`
                    : ""}
                </span>
              </div>
            </div>
          ) : null}
        </div>
        <div className="ops-command-bar__actions">
          {!operationsTerminal(job.status) ? (
            <Button
              kind="danger--tertiary"
              disabled={actionBusy || job.cancel_requested}
              onClick={() => void act("cancel")}
            >
              {job.cancel_requested ? "正在安全停止" : "停止任务"}
            </Button>
          ) : null}
          {job.resumable ? (
            <Button disabled={actionBusy} onClick={() => void act("resume")}>
              从断点继续
            </Button>
          ) : null}
        </div>
      </header>

      {actionError ? (
        <InlineNotification
          kind="error"
          title="任务操作失败"
          subtitle={actionError}
          onCloseButtonClick={() => setActionError("")}
        />
      ) : null}
      {job.error?.message && operationsTerminal(job.status) ? (
        <InlineNotification
          kind={job.status === "failed" ? "error" : "warning"}
          title={statusLabel(job.status)}
          subtitle={job.error.message}
          hideCloseButton
        />
      ) : job.status === "blocked" ? (
        <InlineNotification
          kind="warning"
          title="结果已保存，但任务未形成完整交付"
          subtitle="当前索引没有结构化终止理由；旧版结果仍可查看，后续新任务会在这里显示精确失败步骤和原因。"
          hideCloseButton
        />
      ) : null}

      <div className="ops-phase-card">
        <ProgressIndicator
          currentIndex={phaseIndex(job)}
          spaceEqually={!useVerticalPhaseIndicator}
          vertical={useVerticalPhaseIndicator}
          className="ops-phase-indicator"
        >
          <ProgressStep
            label="检索候选"
            secondaryLabel={
              job.progress.term_total > 0
                ? `${job.progress.term_completed}/${job.progress.term_total} 个检索词`
                : job.progress.candidate_count > 0
                  ? "旧版摘要未记录检索词"
                  : "等待检索词"
            }
          />
          <ProgressStep
            label="项目审查"
            secondaryLabel={`${job.progress.reviewed_count}/${job.progress.candidate_count} 个项目`}
          />
          <ProgressStep
            label="冻结结果"
            secondaryLabel={`${job.progress.batch_count} 个批次`}
          />
          <ProgressStep
            label="完成"
            secondaryLabel={`${job.progress.usable_file_count} 个文件`}
          />
        </ProgressIndicator>
        <ProgressBar
          label={`总体完成度 ${Math.round(percentage)}%`}
          helperText={
            job.progress.current_term
              ? `当前检索词：${job.progress.current_term}`
              : statusLabel(job.phase)
          }
          value={percentage}
          max={100}
          status={
            job.status === "failed"
              ? "error"
              : job.status === "completed"
                ? "finished"
                : "active"
          }
        />
      </div>

      <div className="ops-metric-grid">
        <Metric
          label="候选项目（去重）"
          value={job.progress.candidate_count}
          detail={`原始命中 ${job.progress.raw_hit_count}`}
        />
        <Metric
          label="已审项目"
          value={job.progress.reviewed_count}
          detail={`待审 ${job.progress.pending_review_count}`}
        />
        <Metric
          label="判断合格"
          value={job.progress.qualified_count}
          detail="项目级结论"
        />
        <Metric
          label="可交付文件"
          value={job.progress.usable_file_count}
          detail={`${job.progress.batch_count} 个冻结批次`}
        />
      </div>

      {job.progress.batch_count > 0 ? (
        <Tile className="ops-delivery-ready">
          <div>
            <p className="ops-eyebrow">DELIVERY READY</p>
            <h2>
              已生成 {job.progress.batch_count} 个交付批次
            </h2>
            <p>
              当前已冻结批次可直接下载清单或送入批量处理；每批最多 500
              个新文件，不会重复带入上一批。
            </p>
          </div>
          <div className="ops-delivery-ready__actions">
            {job.status === "completed" || job.status === "blocked" ? (
              <Button
                kind="secondary"
                href={`/api/discovery/${encodeURIComponent(`agents_job_${jobId}`)}/download?file=discovery_run_bundle_zip`}
              >
                下载完整结果包
              </Button>
            ) : null}
            <Button onClick={() => onTabChange("batches")}>
              查看并下载交付批次
            </Button>
          </div>
        </Tile>
      ) : null}

      <Tabs
        selectedIndex={activeIndex}
        onChange={({ selectedIndex }) => onTabChange(tabs[selectedIndex].id)}
      >
        <TabList aria-label="当前任务详情">
          {tabs.map((tab) => <Tab key={tab.id}>{tab.label}</Tab>)}
        </TabList>
      </Tabs>

      <div className="ops-console__body">
        <main className="ops-console__main">
          <section hidden={activeTab !== "overview"} className="ops-tab-panel">
            <div className="ops-overview-grid">
              <Tile>
                <p className="ops-eyebrow">CURRENT ACTIVITY</p>
                <h2>{fileReviewActive ? "文件级评审" : statusLabel(job.phase)}</h2>
                <p>
                  {fileReviewActive
                    ? `${latestVisibleEvent?.copy.title || "正在逐文件评审"}。${latestVisibleEvent?.copy.detail || "完成后会自动领取下一批。"}`
                    : job.phase === "searching"
                    ? `正在按确认顺序拉取检索词；当前为“${job.progress.current_term || "等待下一个检索词"}”。`
                    : job.phase === "reviewing"
                      ? "候选已经全局去重，worker 正在读取项目 metadata、SDRF 和文件线索并形成项目级结论。"
                      : job.phase === "finalizing"
                        ? "正在冻结可交付文件、生成精确 500 文件批次和审计索引。"
                        : job.status === "blocked"
                          ? "检索与审查结果已保存，但任务以“需要处理”状态结束；请查看顶部失败理由或运行事件。"
                          : job.status === "failed"
                            ? "运行已失败，已完成的检索、审查和文件记录仍保留，可从运行事件定位失败步骤。"
                            : job.status === "cancelled"
                              ? "任务已安全停止，持久断点和已经产生的结果仍保留。"
                              : "任务当前没有需要后台推进的步骤。"}
                </p>
              </Tile>
              <Tile>
                <p className="ops-eyebrow">RECOVERY</p>
                <h2>{job.resumable ? "断点可恢复" : "状态已持久化"}</h2>
                <p>
                  事件序号 {job.last_event_sequence} · 快照版本 {job.version} ·
                  最近更新 {formatTime(job.updated_at)}
                </p>
              </Tile>
            </div>
            <Tile>
              <div className="ops-section-heading">
                <div>
                  <p className="ops-eyebrow">PARALLEL REVIEW</p>
                  <h2>{job.progress.worker_count} 路项目审查</h2>
                </div>
                <span>每个槽位只显示一个当前项目</span>
              </div>
              <WorkerBoard jobId={jobId} />
            </Tile>
            <Tile className="ops-live-activity">
              <div className="ops-live-activity__header">
                <div>
                  <p className="ops-eyebrow">LIVE ACTIVITY</p>
                  <h2>Agent 实时执行流</h2>
                </div>
                <div className="ops-command-bar__status">
                  <Tag
                    size="sm"
                    type={connection === "live" ? "green" : "cool-gray"}
                  >
                    {connection === "live"
                      ? "实时"
                      : connection === "reconnecting"
                        ? "正在重连"
                        : connection === "closed"
                          ? "已结束"
                          : "正在连接"}
                  </Tag>
                  <span>事件 #{events.at(-1)?.sequence || job.last_event_sequence}</span>
                </div>
              </div>
              <EventFeed events={events} overview />
            </Tile>
          </section>
          <section hidden={activeTab !== "search"} className="ops-tab-panel">
            <div className="ops-section-heading">
              <div>
                <p className="ops-eyebrow">REPOSITORY SEARCH</p>
                <h2>检索词与分页末尾</h2>
              </div>
              <span>严格按用户确认顺序逐词拉取到末尾</span>
            </div>
            <SearchTerms jobId={jobId} />
          </section>
          <section hidden={activeTab !== "reviews"} className="ops-tab-panel">
            <div className="ops-section-heading">
              <div>
                <p className="ops-eyebrow">PROJECT REVIEW</p>
                <h2>项目级证据与判断</h2>
              </div>
              <span>选择项目可查看 metadata、理由、分数与证据</span>
            </div>
            <Reviews jobId={jobId} onSelect={setSelectedReview} />
          </section>
          <section hidden={activeTab !== "files"} className="ops-tab-panel">
            <div className="ops-section-heading">
              <div>
                <p className="ops-eyebrow">DELIVERABLE FILES</p>
                <h2>文件评审与选择</h2>
              </div>
              <span>全部候选文件；可切换未评审、正在评审和已评审</span>
            </div>
            <FileReviewBoard jobId={jobId} />
          </section>
          <section hidden={activeTab !== "batches"} className="ops-tab-panel">
            <div className="ops-section-heading">
              <div>
                <p className="ops-eyebrow">INCREMENTAL DELIVERY</p>
                <h2>每批最多 500 个新文件</h2>
              </div>
              <span>批次冻结后不会重复带入之前文件</span>
            </div>
            <Batches jobId={jobId} onSeedBatchInputs={onSeedBatchInputs} />
          </section>
          <section hidden={activeTab !== "events"} className="ops-tab-panel">
            <div className="ops-section-heading">
              <div>
                <p className="ops-eyebrow">EVENT STREAM</p>
                <h2>结构化运行事件</h2>
              </div>
              <span>最近 {events.length} 条 · 支持断线续传</span>
            </div>
            <EventFeed events={events} />
          </section>
          <section
            hidden={activeTab !== "chat"}
            className="ops-tab-panel ops-tab-panel--chat"
            aria-label="任务对话区域"
          >
            <p className="ops-empty">任务对话显示在下方，原会话保持挂载，不会因切换控制台而丢失。</p>
          </section>
        </main>
        {activeTab === "reviews" && selectedReview ? (
          <EvidencePanel
            review={selectedReview}
            onClose={() => setSelectedReview(null)}
          />
        ) : null}
      </div>
    </section>
  );
}
