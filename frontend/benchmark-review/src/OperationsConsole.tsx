import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Accordion,
  AccordionItem,
  Button,
  DataTable,
  InlineNotification,
  Layer,
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
  getOperationsFiles,
  getOperationsReviews,
  getOperationsTerms,
  getOperationsWorkers,
  operationsTerminal,
  resumeOperationsJob,
  type OperationsBatch,
  type OperationsEvent,
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
  { id: "files", label: "可用文件" },
  { id: "batches", label: "交付批次" },
  { id: "events", label: "运行事件" },
  { id: "chat", label: "任务对话" },
];

const CARBON_MD_MEDIA_QUERY = "(max-width: 42rem)";

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
  const rows = (query.data?.items || []).map((review) => ({
    id: String(review.id),
    accession: review.accession,
    title: review.title || "—",
    status: review.status === "running" ? `Worker ${review.worker_slot || "—"} · ${reviewStepLabel(review.current_step)}` : statusLabel(review.status),
    decision: review.decision || "pending",
    score: review.score == null ? "—" : review.score.toFixed(1),
    confidence:
      review.confidence == null ? "—" : review.confidence.toFixed(2),
    usable: review.usable_file_count,
    reason: review.reason_code || review.reasons[0] || "—",
  }));
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
            ) : <p>{review.reason_code || "暂无结构化理由。"}</p>}
          </AccordionItem>
          <AccordionItem title="项目 metadata">
            <pre>{JSON.stringify(review.metadata_summary, null, 2)}</pre>
          </AccordionItem>
          <AccordionItem title="证据摘要">
            <pre>{JSON.stringify(review.evidence_summary, null, 2)}</pre>
          </AccordionItem>
          <AccordionItem title="命中的检索词">
            <p>{review.discovered_by_terms.join("；") || "暂无记录。"}</p>
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
                <h2>{statusLabel(job.phase)}</h2>
                <p>
                  {job.phase === "searching"
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
                <h2>验证通过的文件</h2>
              </div>
              <span>混合项目只纳入通过文件级判断的文件</span>
            </div>
            <Files jobId={jobId} />
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
