import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Checkbox,
  ComposedModal,
  ContentSwitcher,
  DataTable,
  InlineNotification,
  ModalBody,
  ModalFooter,
  ModalHeader,
  Pagination,
  Search,
  Select,
  SelectItem,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tag,
  Tile,
} from "@carbon/react";
import { useDeferredValue, useMemo, useState } from "react";

import {
  archiveOperationsHistory,
  getOperationsHistory,
  markOperationsHistoryDeleted,
  type OperationsHistoryItem,
} from "./operations-api";
import {
  deleteHistoryItem,
  previewHistoryDelete,
  type WorkflowRecord,
} from "./workflow-api";

type Props = {
  onOpenJob: (jobId: string) => void;
  onOpenTask: (id: string) => void;
  onOpenBatch: (id: string) => void;
};

const formatBytes = (value: unknown) => {
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

const formatTime = (value: string) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
};

const tagType = (
  status: string,
): "blue" | "green" | "red" | "gray" | "purple" => {
  if (status === "completed") return "green";
  if (status === "failed") return "red";
  if (status === "cancelled") return "gray";
  if (status === "blocked" || status === "interrupted") return "purple";
  return "blue";
};

export function OperationsHistory({
  onOpenJob,
  onOpenTask,
  onOpenBatch,
}: Props) {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [search, setSearch] = useState("");
  const [statusGroup, setStatusGroup] = useState("");
  const [kind, setKind] = useState("");
  const [view, setView] = useState<"current" | "archived" | "trash">("current");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleteItem, setDeleteItem] = useState<OperationsHistoryItem | null>(null);
  const [deletePreview, setDeletePreview] = useState<WorkflowRecord | null>(null);
  const [includeLinked, setIncludeLinked] = useState(false);
  const deferredSearch = useDeferredValue(search);
  const query = useQuery({
    queryKey: [
      "operations-history",
      page,
      pageSize,
      deferredSearch,
      statusGroup,
      kind,
      view,
    ],
    queryFn: ({ signal }) =>
      getOperationsHistory(
        {
          page,
          pageSize,
          query: deferredSearch,
          statusGroup,
          kind,
          archived: view === "archived",
          trash: view === "trash",
        },
        signal,
      ),
    placeholderData: (previous) => previous,
  });

  const items = query.data?.items || [];
  const byId = useMemo(
    () => new Map(items.map((item) => [item.history_id, item])),
    [items],
  );
  const rows = items.map((item) => ({
    id: item.history_id,
    name: item.display_name,
    kind:
      item.kind === "discovery"
        ? "发现与审查"
        : item.kind === "batch"
          ? "批量处理"
          : "单文件处理",
    status: item.status,
    updated: formatTime(item.updated_at),
    projects: item.project_count,
    files: item.file_count,
    size: formatBytes(item.size_bytes),
  }));
  const headers = [
    { key: "name", header: "任务" },
    { key: "kind", header: "类型" },
    { key: "status", header: "状态" },
    { key: "updated", header: "最近更新" },
    { key: "projects", header: "项目" },
    { key: "files", header: "文件" },
    { key: "size", header: "磁盘占用" },
    { key: "actions", header: "操作" },
  ];

  const open = (item: OperationsHistoryItem) => {
    if (item.kind === "discovery") onOpenJob(item.source_id);
    else if (item.kind === "batch") onOpenBatch(item.source_id);
    else onOpenTask(item.source_id);
  };

  const archive = async (item: OperationsHistoryItem, archived: boolean) => {
    setBusy(true);
    setError("");
    try {
      await archiveOperationsHistory(item.history_id, archived);
      setNotice(archived ? "任务已归档。" : "任务已移回当前历史。");
      await queryClient.invalidateQueries({ queryKey: ["operations-history"] });
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  const showDelete = async (
    item: OperationsHistoryItem,
    linked = false,
  ) => {
    setBusy(true);
    setError("");
    try {
      const preview = await previewHistoryDelete(
        item.kind,
        item.source_id,
        linked,
      );
      setDeleteItem(item);
      setIncludeLinked(linked);
      setDeletePreview(preview);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteItem || !deletePreview) return;
    setBusy(true);
    setError("");
    try {
      const result = await deleteHistoryItem(
        deleteItem.kind,
        deleteItem.source_id,
        String(deletePreview.confirmation_id || ""),
        includeLinked,
      );
      const released = Number(result.released_bytes || 0);
      await markOperationsHistoryDeleted(deleteItem.history_id, released);
      setNotice(`已释放 ${formatBytes(released)}。删除回执已写入历史索引。`);
      setDeleteItem(null);
      setDeletePreview(null);
      await queryClient.invalidateQueries({ queryKey: ["operations-history"] });
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="history-console" aria-label="历史任务">
      <header className="page-title">
        <div>
          <p className="ops-eyebrow">OPERATIONS HISTORY</p>
          <h1>历史任务与磁盘空间</h1>
          <p>列表来自数据库索引；打开历史不会重跑检索，也不会读取完整运行日志。</p>
        </div>
      </header>
      {error || query.error ? (
        <InlineNotification
          kind="error"
          title="历史操作失败"
          subtitle={error || String(query.error)}
          onCloseButtonClick={() => setError("")}
        />
      ) : null}
      {notice ? (
        <InlineNotification
          kind="success"
          title="历史已更新"
          subtitle={notice}
          onCloseButtonClick={() => setNotice("")}
        />
      ) : null}
      <div className="ops-metric-grid">
        <Tile className="ops-metric">
          <span className="ops-metric__label">有效历史</span>
          <strong>{String(query.data?.summary?.total || 0)}</strong>
          <span className="ops-metric__detail">数据库索引条目</span>
        </Tile>
        <Tile className="ops-metric">
          <span className="ops-metric__label">运行中</span>
          <strong>{String(query.data?.summary?.active || 0)}</strong>
          <span className="ops-metric__detail">可实时打开</span>
        </Tile>
        <Tile className="ops-metric">
          <span className="ops-metric__label">需要处理</span>
          <strong>{String(query.data?.summary?.needs_attention || 0)}</strong>
          <span className="ops-metric__detail">失败、阻塞或中断</span>
        </Tile>
        <Tile className="ops-metric">
          <span className="ops-metric__label">磁盘占用</span>
          <strong>{formatBytes(query.data?.summary?.storage_bytes)}</strong>
          <span className="ops-metric__detail">受管任务资产</span>
        </Tile>
      </div>
      <Tile className="history-filters">
        <div className="history-module-switcher">
          <span className="cds--label">任务模块</span>
          <ContentSwitcher
            selectedIndex={["", "discovery", "batch", "task"].indexOf(kind)}
            onChange={(selection) => {
              setKind(String(selection.name || ""));
              setPage(1);
            }}
          >
            <Switch name="" text="全部" />
            <Switch name="discovery" text="数据发现" />
            <Switch name="batch" text="批量处理" />
            <Switch name="task" text="单文件" />
          </ContentSwitcher>
        </div>
        <Search
          id="history-search"
          labelText="搜索历史"
          placeholder="搜索任务目标或编号"
          value={search}
          onChange={(event) => {
            setSearch(event.currentTarget.value);
            setPage(1);
          }}
        />
        <Select
          id="history-status-filter"
          labelText="状态"
          value={statusGroup}
          onChange={(event) => {
            setStatusGroup(event.currentTarget.value);
            setPage(1);
          }}
        >
          <SelectItem value="" text="全部状态" />
          <SelectItem value="active" text="运行中" />
          <SelectItem value="completed" text="已完成" />
          <SelectItem value="needs_attention" text="需要处理" />
        </Select>
        <Select
          id="history-view-filter"
          labelText="视图"
          value={view}
          onChange={(event) => {
            setView(event.currentTarget.value as typeof view);
            setPage(1);
          }}
        >
          <SelectItem value="current" text="当前历史" />
          <SelectItem value="archived" text="已归档" />
          <SelectItem value="trash" text="回收记录" />
        </Select>
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
                  const item = byId.get(row.id);
                  if (!item) return null;
                  return (
                    <TableRow key={key} {...rest}>
                      {row.cells.slice(0, -1).map((cell, index) => (
                        <TableCell key={cell.id}>
                          {index === 2 ? (
                            <Tag size="sm" type={tagType(String(cell.value))}>
                              {String(cell.value)}
                            </Tag>
                          ) : cell.value}
                        </TableCell>
                      ))}
                      <TableCell>
                        <div className="ops-action-row">
                          <Button
                            size="sm"
                            kind="ghost"
                            disabled={!item.open_available}
                            onClick={() => open(item)}
                          >
                            打开
                          </Button>
                          {view !== "trash" ? (
                            <Button
                              size="sm"
                              kind="ghost"
                              disabled={busy}
                              onClick={() => void archive(item, !item.archived_at)}
                            >
                              {item.archived_at ? "移出归档" : "归档"}
                            </Button>
                          ) : null}
                          {item.deletable && view !== "trash" ? (
                            <Button
                              size="sm"
                              kind="danger--ghost"
                              disabled={busy}
                              onClick={() => void showDelete(item)}
                            >
                              删除
                            </Button>
                          ) : null}
                        </div>
                      </TableCell>
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

      {deleteItem && deletePreview ? (
        <ComposedModal
          open
          size="sm"
          onClose={() => {
            setDeleteItem(null);
            setDeletePreview(null);
          }}
        >
          <ModalHeader
            label="DISK CLEANUP"
            title="确认删除历史任务"
            closeModal={() => {
              setDeleteItem(null);
              setDeletePreview(null);
            }}
          />
          <ModalBody>
            <p>{deleteItem.display_name}</p>
            <p>
              <strong>
                预计释放 {formatBytes(deletePreview.estimated_bytes)}
              </strong>
            </p>
            <p>
              只删除预览中列出的受管任务目录；共享缓存和用户原始路径不在删除范围。
            </p>
            {deleteItem.kind === "discovery" ? (
              <Checkbox
                id="delete-linked-batches-operations"
                labelText="同时删除由此发现批次创建的关联批量任务"
                checked={includeLinked}
                onChange={(_event, state) =>
                  void showDelete(deleteItem, Boolean(state.checked))
                }
              />
            ) : null}
          </ModalBody>
          <ModalFooter>
            <Button
              kind="secondary"
              onClick={() => {
                setDeleteItem(null);
                setDeletePreview(null);
              }}
            >
              取消
            </Button>
            <Button
              kind="danger"
              disabled={busy || !Boolean(deletePreview.deletable)}
              onClick={() => void confirmDelete()}
            >
              确认删除
            </Button>
          </ModalFooter>
        </ComposedModal>
      ) : null}
    </section>
  );
}
