import { Tag, Tile } from "@carbon/react";

type AnyRecord = Record<string, unknown>;

const asRecord = (value: unknown): AnyRecord | null =>
  value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as AnyRecord)
    : null;

const count = (value: unknown): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : 0;
};

const text = (value: unknown): string => String(value || "").trim();

function requirement(spec: AnyRecord, key: string): number | null {
  const value = spec[key];
  const parsed = count(value);
  return parsed > 0 ? parsed : null;
}

/**
 * Progressive portfolio explanation: one compact line for ordinary runs, with
 * dimension-level detail only when the deterministic evaluator found a gap.
 */
export function PortfolioCoveragePanel({ state }: { state: unknown }) {
  const root = asRecord(state);
  if (!root) return null;
  const coverage = asRecord(root.coverage);
  const spec = asRecord(root.spec) || {};
  const gaps = Array.isArray(root.gaps)
    ? root.gaps.map(asRecord).filter((item): item is AnyRecord => item != null)
    : [];
  const recoveryActions = Array.isArray(root.recovery_actions)
    ? root.recovery_actions.map(asRecord).filter((item): item is AnyRecord => item != null)
    : [];
  if (!coverage) return null;

  const files = count(coverage.selected_files ?? coverage.candidate_files);
  const projects = count(coverage.distinct_projects);
  const targetFiles = requirement(spec, "target_files");
  const targetProjects = requirement(spec, "target_projects");
  const status = text(root.status) || "planning";
  const frozen = status === "frozen";
  const ready = status === "ready" || status === "frozen";
  const gapCount = gaps.length;

  return (
    <Tile className="portfolio-coverage-panel" aria-label="Portfolio coverage">
      <div className="portfolio-coverage-panel__header">
        <div>
          <p className="eyebrow">PORTFOLIO COVERAGE</p>
          <h3>组合覆盖</h3>
        </div>
        <Tag size="sm" type={ready ? "green" : gapCount ? "purple" : "gray"}>
          {frozen ? "已冻结" : ready ? "已覆盖" : gapCount ? `${gapCount} 个缺口` : status}
        </Tag>
      </div>
      <p className="portfolio-coverage-panel__summary">
        {projects}{targetProjects ? ` / ${targetProjects}` : ""} 个项目，{files}
        {targetFiles ? ` / ${targetFiles}` : ""} 个文件
      </p>
      {gapCount ? (
        <div className="portfolio-coverage-panel__gaps">
          <p className="eyebrow">需要补证据</p>
          <ul>
            {gaps.slice(0, 6).map((gap, index) => (
              <li key={`${text(gap.dimension)}-${index}`}>
                <strong>{text(gap.dimension).replaceAll("_", " ")}</strong>
                {`: 需要 ${count(gap.required)}，目前 ${count(gap.observed)}`}
                {text(gap.severity) === "hard" ? "（硬条件）" : "（可恢复或说明限制）"}
              </li>
            ))}
          </ul>
          {gaps.length > 6 ? <p className="empty-copy">还有 {gaps.length - 6} 个缺口，详细证据在运行记录中。</p> : null}
        </div>
      ) : null}
      {recoveryActions.length ? (
        <div className="portfolio-coverage-panel__recovery">
          <p className="eyebrow">Agent 下一步</p>
          <ul>
            {recoveryActions.slice(0, 3).map((action, index) => (
              <li key={String(text(action.id) || text(action.kind)) + "-" + index}>
                <strong>{text(action.kind).replaceAll("_", " ")}</strong>
                {text(action.expected_gain) || text(action.rationale)}
                {action.requires_approval === true ? "（需要确认）" : "（可自动执行）"}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Tile>
  );
}
