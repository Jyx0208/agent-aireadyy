import type { OperationsEvent } from "./operations-api";

export type OperationsEventCopy = {
  title: string;
  detail: string;
  actor: string;
  typeLabel: string;
  technical: boolean;
};

const text = (value: unknown) => String(value ?? "").trim();

const number = (value: unknown, fallback = 0) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const project = (event: OperationsEvent) =>
  text(event.payload.project_accession || event.payload.accession);

const query = (event: OperationsEvent) =>
  text(event.payload.executed_query || event.payload.query || event.payload.term);

const reviewStep = (value: unknown) =>
  ({
    metadata_read: "读取项目 metadata",
    metadata_score: "分析 metadata 并评分",
    scientific_gate: "核对物种、主题和采集方式",
    file_inventory: "读取项目文件清单",
    sdrf_lookup: "查找 SDRF",
    sdrf_download: "下载并解析 SDRF",
    file_filter: "逐个筛选可用文件",
    completed: "完成项目审查",
    failed: "项目审查失败",
  })[text(value)] || text(value) || "处理项目";

const auditIssueLabel = (value: unknown) =>
  ({
    quality_audit_policy_denied: "质量检查策略未获授权",
    candidate_manifest_missing: "候选文件清单缺失",
    inspected_projects_missing_judgments: "部分已审项目还没有模型判断",
    constraint_assessment_evidence_invalid: "约束判断缺少可追溯证据",
    selected_manifest_contains_unqualified_projects: "最终清单仍含未合格项目",
    qualified_project_has_no_inspected_files: "合格项目尚未完成文件检查",
    selected_manifest_contains_non_delivery_files: "最终清单仍含不可交付文件",
    qualified_project_has_no_delivery_assets: "合格项目没有可交付文件",
    hard_per_project_min_files_not_met: "单项目最低文件数未满足",
    hard_per_project_min_samples_not_met: "单项目最低样本数未满足",
    hard_builtin_constraint_not_met: "物种、采集模式等硬条件未满足",
    hard_portfolio_constraint_not_met: "项目组合的硬条件未满足",
    qualified_projects_have_unresolved_constraints: "合格项目仍有未解决的科学条件",
    qualified_project_still_needs_review: "合格项目仍被标记为需要复核",
    zero_strict_valid_files_with_qualified_projects: "合格项目中没有严格有效的文件",
    candidate_inspections_failed: "部分候选项目读取失败",
    preview_coverage_not_backed_by_selection: "检索到的主题覆盖没有进入最终选择",
    high_relevance_inspection_coverage_incomplete: "高相关候选还没有审完",
    high_relevance_inspection_coverage_stopped: "高相关候选审查提前停止",
    portfolio_search_not_converged: "检索结果尚未稳定收敛",
    portfolio_search_stopped_at_hard_ceiling: "检索达到硬上限后停止",
    portfolio_search_stopped_before_convergence: "检索在结果稳定前停止",
    quality_target_not_reached: "目标数量或质量尚未达到",
    fixed_quality_target_shortfall: "固定质量目标仍有缺口",
    quality_target_shortfall_at_stop: "停止时质量目标仍有缺口",
    autonomous_repair_ceiling_exhausted: "自动补救次数已经用完",
    selected_manifest_missing: "最终文件清单缺失",
  })[text(value)] || text(value).replaceAll("_", " ");

const technicalEvent = (event: OperationsEvent) => {
  const kind = event.type.toLowerCase();
  const actor = event.actor.toLowerCase();
  return (
    kind.startsWith("sdk_") ||
    kind === "run_started" ||
    kind === "agent-turn" ||
    kind === "semantic-verification" ||
    actor.includes("openai agents sdk")
  );
};

export function describeOperationsEvent(
  event: OperationsEvent,
): OperationsEventCopy {
  const payload = event.payload;
  const technical = technicalEvent(event);
  const fallback = event.message || event.type.replaceAll("_", " ");

  switch (event.type) {
    case "job_queued":
      return {
        title: "任务已进入持久队列",
        detail: "即使网页关闭，任务记录也不会丢失。",
        actor: "任务系统",
        typeLabel: "等待执行",
        technical,
      };
    case "job_enqueued":
      return {
        title: "任务正在等待 worker 认领",
        detail: "后台 worker 空闲后会自动开始。",
        actor: "任务队列",
        typeLabel: "排队中",
        technical,
      };
    case "job_started":
      return {
        title: "后台 worker 已开始执行",
        detail: "接下来将按确认顺序检索关键词并审查项目。",
        actor: "后台 worker",
        typeLabel: "已开始",
        technical,
      };
    case "candidate_search_started": {
      const plans = Array.isArray(payload.queries)
        ? payload.queries.length
        : number(payload.query_count || payload.plan_count);
      return {
        title: plans
          ? `准备按顺序检索 ${plans} 个关键词`
          : "准备开始检索候选项目",
        detail: event.message,
        actor: "仓库检索",
        typeLabel: "检索准备",
        technical,
      };
    }
    case "repository_query_started": {
      const page = Math.max(
        1,
        number(payload.page_number, number(payload.start_page) + 1),
      );
      const pageSize = number(payload.page_size);
      return {
        title: `开始检索“${query(event) || "未命名关键词"}”第 ${page} 页`,
        detail: pageSize
          ? `本页最多向 PRIDE 读取 ${pageSize} 个项目。`
          : "正在等待 PRIDE 返回这一页项目。",
        actor: "PRIDE 检索",
        typeLabel: "正在翻页",
        technical,
      };
    }
    case "repository_query_page_completed": {
      const page = Math.max(
        1,
        number(payload.page_number, number(payload.page) + 1),
      );
      const returned = number(
        payload.page_result_count,
        number(payload.page_count),
      );
      const cumulative = number(payload.cumulative_count);
      const pages = number(payload.pages_completed, page);
      return {
        title: `“${query(event)}”第 ${page} 页读取完成`,
        detail: `本页返回 ${returned} 个项目；该词已翻 ${pages} 页${
          cumulative ? `，累计读取 ${cumulative} 个` : ""
        }。`,
        actor: "PRIDE 检索",
        typeLabel: "一页完成",
        technical,
      };
    }
    case "repository_query_completed": {
      const raw = number(payload.raw_result_count || payload.raw_count);
      const added = number(
        payload.new_candidate_count || payload.unique_count,
      );
      const duplicates = number(payload.duplicate_count);
      const pages = number(payload.pages_completed || payload.page_count);
      return {
        title: `关键词“${query(event)}”本轮检索完成`,
        detail: `${pages ? `共翻 ${pages} 页；` : ""}原始返回 ${raw} 个，去重后新增 ${added} 个，重复 ${duplicates} 个。`,
        actor: "PRIDE 检索",
        typeLabel: "关键词完成",
        technical,
      };
    }
    case "repository_query_failed": {
      const page = Math.max(1, number(payload.page_number, 1));
      return {
        title: `关键词“${query(event)}”第 ${page} 页检索失败`,
        detail: `${text(payload.error) || "PRIDE 请求失败"}。系统将按恢复策略重试或继续下一个词。`,
        actor: "PRIDE 检索",
        typeLabel: "请求失败",
        technical,
      };
    }
    case "project_review_started":
    case "project_inspection_started":
      return {
        title: `Worker ${number(payload.worker_slot) || "—"} 开始审查 ${project(event)}`,
        detail: `当前步骤：${reviewStep(payload.step || "metadata_read")}。`,
        actor: "项目审查",
        typeLabel: "开始审查",
        technical,
      };
    case "project_review_step": {
      const status = text(payload.status);
      const accession = project(event);
      const step = reviewStep(payload.step);
      const usable = number(payload.usable_file_count);
      const rawFiles = number(payload.raw_file_count);
      const reason = text(payload.reason || payload.error);
      return {
        title: `${accession}：${step}${status === "completed" ? "完成" : status === "failed" ? "失败" : "中"}`,
        detail:
          reason ||
          (rawFiles
            ? `读取 ${rawFiles} 个文件，当前保留 ${usable} 个可用文件。`
            : `Worker ${number(payload.worker_slot) || "—"} 正在处理；已耗时 ${number(payload.elapsed_ms)} ms。`),
        actor: "项目审查",
        typeLabel: status === "failed" ? "步骤失败" : "审查步骤",
        technical,
      };
    }
    case "project_review_completed":
    case "project_inspection_completed": {
      const outcomes = Array.isArray(payload.inspection_outcomes)
        ? payload.inspection_outcomes
        : [];
      const first =
        outcomes[0] && typeof outcomes[0] === "object"
          ? (outcomes[0] as Record<string, unknown>)
          : {};
      const usable = number(
        payload.usable_file_count || first.usable_file_count,
      );
      return {
        title: `${project(event)} 审查完成`,
        detail: usable
          ? `该项目保留 ${usable} 个可用文件。`
          : text(first.reason || payload.reason) || "项目级结论已保存。",
        actor: "项目审查",
        typeLabel: "审查完成",
        technical,
      };
    }
    case "fixed_project_target_reached":
      return {
        title: `已找够 ${number(
          payload.target_project_count ||
            payload.target_projects ||
            payload.target_count,
        )} 个合格项目`,
        detail: "系统已停止安排新的检索页和项目审查，正在冻结结果。",
        actor: "任务调度",
        typeLabel: "达到目标",
        technical,
      };
    case "file_review_batch_started":
      return {
        title: `大模型正在评审新一批 ${number(payload.count)} 个文件`,
        detail: "正在逐文件判断纳入、待核查或排除，并记录文件级理由。",
        actor: "文件评审",
        typeLabel: "正在评审",
        technical,
      };
    case "file_review_batch_completed":
      return {
        title: "一批文件的结构判断已经完成",
        detail: "正在保存判断，并为纳入文件补充连贯理由。",
        actor: "文件评审",
        typeLabel: "批次完成",
        technical,
      };
    case "file_reason_batch_completed":
      return {
        title: `已写好 ${number(payload.count)} 个文件的判断理由`,
        detail: "结果已实时写入文件看板，后台会继续领取下一批。",
        actor: "文件评审",
        typeLabel: "理由完成",
        technical,
      };
    case "file_selection_changed":
      return {
        title: `已更新文件选择，目前纳入 ${number(payload.selected_file_count)} 个`,
        detail: "任务仍会继续评审剩余文件，最终清单尚未冻结。",
        actor: "文件评审",
        typeLabel: "选择更新",
        technical,
      };
    case "discovery_quality_audited": {
      const counts =
        payload.counts && typeof payload.counts === "object" && !Array.isArray(payload.counts)
          ? (payload.counts as Record<string, unknown>)
          : {};
      const issues = Array.isArray(payload.issues)
        ? payload.issues
            .map((item) =>
              item && typeof item === "object" && !Array.isArray(item)
                ? auditIssueLabel((item as Record<string, unknown>).code)
                : "",
            )
            .filter(Boolean)
        : [];
      const status = text(payload.status);
      const projects = number(counts.delivery_eligible_projects);
      const files = number(counts.usable_files);
      return {
        title:
          status === "ready"
            ? "科学质量检查已通过"
            : `科学质量检查发现 ${issues.length} 项需要继续处理`,
        detail: `当前有 ${projects} 个可交付项目、${files} 个可用文件。${
          issues.length ? `待处理：${issues.join("；")}。` : "没有发现阻断交付的问题。"
        }`,
        actor: "科学质量检查",
        typeLabel: status === "ready" ? "检查通过" : "需要处理",
        technical,
      };
    }
    case "discovery_quality_repair_started":
      return {
        title: "正在补齐质量检查发现的缺口",
        detail: "已有结果不会丢失；系统正在完成缺少的判断、证据或文件检查。",
        actor: "科学质量检查",
        typeLabel: "正在修复",
        technical,
      };
    case "discovery_quality_repair_completed":
      return {
        title: "本轮质量补齐已完成，正在重新检查",
        detail: "重新检查通过后才会冻结最终交付清单。",
        actor: "科学质量检查",
        typeLabel: "重新检查",
        technical,
      };
    case "job_cancel_requested":
      return {
        title: "已收到停止请求",
        detail: "正在等待当前安全步骤结束，已完成结果会保留。",
        actor: "任务系统",
        typeLabel: "正在停止",
        technical,
      };
    default:
      return {
        title: fallback,
        detail: technical ? "底层运行遥测；不影响业务进度判断。" : "",
        actor: technical ? "技术组件" : event.actor,
        typeLabel: event.type.replaceAll("_", " "),
        technical,
      };
  }
}
