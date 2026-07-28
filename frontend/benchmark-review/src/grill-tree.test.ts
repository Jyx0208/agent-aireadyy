import { describe, expect, it } from "vitest";

import {
  absorbFreeTextSignals,
  assessStrategyGaps,
  agenticSoftFill,
  applyAnswer,
  applyLocalParse,
  applyRecommendedDefaults,
  applyTargetProjectCount,
  buildStrategyCard,
  composeNextQuestionBody,
  confirmedSearchThemes,
  searchTermCandidates,
  reconcileSearchTermSelection,
  normalizeSearchTerms,
  shouldDeferConfirmCard,
  nextQuestion,
  deriveObjective,
  detectSpeciesSignals,
  extractTargetProjectCount,
  formatAnswerAcknowledgement,
  formatConfirmMessage,
  formatOptionsFooter,
  formatQuestionMessage,
  hasDomainSubstance,
  humanizeJobProgress,
  isGreetingPrompt,
  isMetaOrConfusedPrompt,
  isOrientationPrompt,
  isPollutedObjective,
  isReadyForConfirm,
  isStrategyComplaintPrompt,
  intentSnapshotForLlm,
  matchOptionAnswer,
  mergeLlmFields,
  overlayFilledIntent,
  sanitizeIntentObjective,
  toDiscoveryJobPayload,
} from "./grill-tree";
import { createEmptyIntent } from "./intent-spec";

describe("grill decision tree", () => {
  it("fixes every discovery task to candidate review without asking Q2", () => {
    const draft = createEmptyIntent("Find human immunopeptidomics projects");
    draft.answered.Q1 = true;

    expect(draft.runHorizon).toBe("candidates_reviewed");
    expect(draft.answered.Q2).toBe(true);
    expect(nextQuestion(draft)?.id).not.toBe("Q2");

    draft.runHorizon = "plan_only";
    expect(toDiscoveryJobPayload(draft).run_horizon).toBe("candidates_reviewed");
  });

  it("shows and submits the exact user-confirmed primary search themes", () => {
    const draft = createEmptyIntent("尽可能多找人源免疫肽项目");
    draft.objective = "尽可能多找人源免疫肽项目";
    draft.ptmTypes = ["immunopeptide"];
    draft.specialThemes = ["immunopeptidomics"];
    draft.selectedSearchTerms = ["immunopeptidomics"];
    draft.confirmed = true;

    expect(confirmedSearchThemes(draft)).toEqual(["immunopeptidomics"]);
    expect(formatConfirmMessage(draft)).toContain(
      "待确认检索主题词：immunopeptidomics",
    );
    expect(toDiscoveryJobPayload(draft).query_terms).toEqual(confirmedSearchThemes(draft));
  });

  it("offers repository wording variants for immunopeptidomics and submits only the user selection", () => {
    const draft = createEmptyIntent("Find human immunopeptidomics projects");
    draft.ptmTypes = ["immunopeptide"];

    expect(searchTermCandidates(draft)).toEqual(expect.arrayContaining([
      "immunopeptidomics",
      "immunopeptidome",
      "HLA ligandome",
      "MHC ligandome",
      "HLA peptidome",
      "MHC peptidome",
    ]));

    draft.selectedSearchTerms = ["immunopeptidome", "HLA ligandome"];
    expect(confirmedSearchThemes(draft)).toEqual(["immunopeptidome", "HLA ligandome"]);
    expect(toDiscoveryJobPayload(draft).query_terms).toEqual([
      "immunopeptidome",
      "HLA ligandome",
    ]);
  });

  it("serializes an immunopeptidomics special theme as the immunopeptidomics goal", () => {
    const draft = createEmptyIntent("Explore public data");
    draft.specialThemes = ["immunopeptidomics"];
    draft.selectedSearchTerms = ["immunopeptidomics", "HLA ligandome"];

    const payload = toDiscoveryJobPayload(draft);

    expect(payload.goal).toBe("immunopeptidomics");
    expect(payload.hard_constraint_fields).toContain("goal");
  });

  it("drops stale immunopeptide selections when the scientific theme changes", () => {
    const phospho = createEmptyIntent("Find phosphoproteomics projects");
    phospho.ptmTypes = ["phosphorylation"];

    expect(reconcileSearchTermSelection(["HLA ligandome"], phospho)).toEqual([
      "phosphorylation",
    ]);
  });

  it("keeps a custom term added after all twelve immunopeptide suggestions", () => {
    const draft = createEmptyIntent("Find human immunopeptidomics projects");
    draft.ptmTypes = ["immunopeptide"];
    draft.selectedSearchTerms = [
      ...searchTermCandidates(draft),
      "custom antigen presentation phrase",
    ];

    expect(toDiscoveryJobPayload(draft).query_terms).toContain(
      "custom antigen presentation phrase",
    );
    expect(toDiscoveryJobPayload(draft).query_terms).toHaveLength(13);
  });

  it("normalizes repository terms using the backend-equivalent duplicate key", () => {
    expect(normalizeSearchTerms([
      " HLA   ligandome ",
      "hla ligandome",
      "MHC ligandome",
      "x".repeat(241),
    ])).toEqual(["HLA ligandome", "MHC ligandome"]);
  });

  it("never turns a full free-text objective into an implicit repository query", () => {
    const draft = createEmptyIntent("Find datasets for a novel context and explain every decision");
    draft.objective = "Find datasets for a novel context and explain every decision";

    expect(confirmedSearchThemes(draft)).toEqual([]);
  });

  it("does not start-ready on bare hi and asks Q1 first", () => {
    const draft = applyLocalParse("hi");
    expect(isReadyForConfirm(draft)).toBe(false);
    expect(nextQuestion(draft)?.id).toBe("Q1");
  });
  it("treats hihi/greetings as empty and asks Q1 without inventing defaults", () => {
    expect(isGreetingPrompt("hi")).toBe(true);
    expect(isGreetingPrompt("hihi")).toBe(true);
    expect(isGreetingPrompt("找人源 DDA")).toBe(false);
    const draft = applyLocalParse("hihi");
    expect(isReadyForConfirm(draft)).toBe(false);
    expect(draft.answered.Q4).toBeFalsy();
    expect(draft.answered.Q6).toBeFalsy();
    expect(draft.answered.Q7).toBeFalsy();
    expect(nextQuestion(draft)?.id).toBe("Q1");
  });


  it("infers RT human DDA exhaustive from a rich first message and only asks gaps", () => {
    const draft = applyLocalParse("找人源 DDA 数据做 RT 预测，尽量多一点");
    expect(draft.taskType).toBe("rt_prediction");
    expect(draft.species).toContain("human");
    expect(draft.acquisitionMode).toBe("dda");
    expect(draft.coverageMode).toBe("exhaustive");
    expect(draft.answered.Q1).toBe(true);
    expect(draft.answered.Q3).toBe(true);
    expect(draft.answered.Q4).toBe(true);
    expect(draft.answered.Q7).toBe(true);
    expect(nextQuestion(draft)?.id).toBe("Q6");
  });

  it("requires confirm gate: defaults fill then payload has grill_confirmed", () => {
    let draft = applyLocalParse("hi");
    draft = applyRecommendedDefaults(draft);
    expect(isReadyForConfirm(draft)).toBe(true);
    expect(draft.confirmed).toBe(false);
    expect(toDiscoveryJobPayload(draft).grill_confirmed).toBe(false);
    const payload = toDiscoveryJobPayload({ ...draft, confirmed: true });
    expect(payload.grill_confirmed).toBe(true);
    expect(payload.scale_mode).toBe("balanced");
    expect(payload.max_projects).toBeGreaterThan(0);
  });

  it("treats SILAC separately from generic labeling", () => {
    let draft = applyLocalParse("human DDA for RT prediction, SILAC only");
    if (!draft.answered.Q6) {
      draft = applyAnswer(draft, "Q6", "silac");
    }
    expect(draft.labelingStrategy).toBe("silac");
    expect(draft.labelingHard).toBe(true);
  });

  it("defaults instrument preference to newer when applying recommended defaults", () => {
    const filled = applyRecommendedDefaults(applyLocalParse("随便找点蛋白质组数据"));
    expect(filled.instrumentPreference).toBe("newer");
  });

  it("serializes arbitrary scientific constraints without flattening them into notes", () => {
    const constraint = {
      id: "cohort.min-participants",
      label: "At least 30 participants",
      dimension: "participant_count",
      operator: "gte",
      value: 30,
      strength: "hard" as const,
      scope: "project" as const,
      evidence_required: true,
      source: "user" as const,
    };
    const spec = {
      ...applyRecommendedDefaults(applyLocalParse("human cohort")),
      confirmed: true,
      scientificConstraints: [constraint],
    };

    const payload = toDiscoveryJobPayload(spec);

    expect(payload.scientific_constraints).toEqual([constraint]);
    expect(intentSnapshotForLlm(spec).scientific_constraints).toEqual([constraint]);
  });

  it("preserves soft labeling, mixed-acquisition, and open-ended execution semantics", () => {
    const spec = {
      ...applyRecommendedDefaults(applyLocalParse("explore TMT proteomics")),
      confirmed: true,
      acquisitionMode: "dda" as const,
      mixedAcquisitionPolicy: "allow" as const,
      labelingStrategy: "tmt" as const,
      labelingHard: false,
      quotaFlexibility: "open_ended" as const,
      targetProjectCount: null,
      maxCandidateProjects: null,
      runHorizon: "candidates_reviewed" as const,
      timeBudget: "multi_round" as const,
      onSafetyCeiling: "auto_continue_within_safety" as const,
    };

    const payload = toDiscoveryJobPayload(spec);

    expect(payload.labeling_strategy).toBe("tmt");
    expect(payload.labeling_hard).toBe(false);
    expect(payload.hard_constraint_fields).not.toContain("labeling_strategy");
    expect(payload.constraint_provenance.labeling_strategy).toBe("user_preference");
    expect(payload.mixed_acquisition_policy).toBe("allow");
    expect(payload.quota_flexibility).toBe("open_ended");
    expect(payload.quantity_scope).toBe("portfolio");
    expect(payload.portfolio_size_preference).toBe("maximize_qualified_projects");
    expect(payload.run_horizon).toBe("candidates_reviewed");
    expect(payload.time_budget_preference).toBe("multi_round");
    expect(payload.on_safety_ceiling).toBe("auto_continue_within_safety");
    const card = buildStrategyCard(spec);
    expect(card.summaryLines.join(" ")).toContain("尽可能多");
    expect(card.targetQuota).toContain("不设固定数量目标");
    expect(card.summaryLines.join(" ")).not.toMatch(/目标规模：约 \d+/);
  });

  it.each([
    ["reject_mixed", "混合采集项目整项排除", "hardConstraints"],
    ["review_mixed", "混合采集项目进入文件级审查", "softPreferences"],
    ["allow", "混合采集项目可保留", "softPreferences"],
  ] as const)(
    "shows mixed-acquisition policy %s even when acquisition mode is open",
    (mixedAcquisitionPolicy, expected, bucket) => {
      const card = buildStrategyCard({
        ...createEmptyIntent("open acquisition strategy"),
        acquisitionMode: "unknown",
        mixedAcquisitionPolicy,
        resolvedFields: ["acquisition_mode", "mixed_acquisition_policy"],
      });

      expect(card[bucket]).toContain(expected);
    },
  );

  it("serializes a fixed project quota as an unmet hard execution constraint", () => {
    const spec = {
      ...applyRecommendedDefaults(createEmptyIntent("reviewed human cohort")),
      targetProjectCount: 17,
      quotaFlexibility: "fixed" as const,
      confirmed: true,
    };

    const payload = toDiscoveryJobPayload(spec);
    const card = buildStrategyCard(spec);

    expect(payload.quota_flexibility).toBe("fixed");
    expect(payload.max_projects).toBe(17);
    expect(payload.hard_constraint_fields).toEqual(expect.arrayContaining([
      "max_projects",
      "quota_flexibility",
    ]));
    expect(payload.constraint_provenance).toMatchObject({
      max_projects: "user",
      quota_flexibility: "user",
    });
    expect(card.hardConstraints).toContain("固定数量目标：17 个项目（待搜索核验）");
    expect(card.summaryLines.join(" ")).toContain("固定数量目标：17 个项目（待搜索核验）");
    expect(card.targetQuota).toContain("搜索后才核验实际可用项目数");
    expect(card.targetQuota).not.toMatch(/已满足|已入选/);
  });

  it("detects meta questions and keeps conversational question phrasing", () => {
    expect(isMetaOrConfusedPrompt("DDA 是什么意思")).toBe(true);
    expect(isMetaOrConfusedPrompt("1")).toBe(false);
    expect(isMetaOrConfusedPrompt("做 RT 预测")).toBe(false);
    const q = nextQuestion(applyLocalParse("hi"));
    expect(q?.id).toBe("Q1");
    const msg = formatQuestionMessage(q!, applyLocalParse("hi"));
    expect(msg).toMatch(/自然语言|直接说你的判断|按推荐默认/);
    expect(msg).not.toMatch(/^\*\*/m);
    const after = applyAnswer(applyLocalParse("hi"), "Q1", "1");
    const ack = formatAnswerAcknowledgement("Q1", after);
    expect(ack).toMatch(/RT|预测|好的|理解/);
    expect(ack).not.toContain("已记录 Q1");
  });

  it("browse_only maps to empty task_type for discovery API", () => {
    let draft = applyLocalParse("human immunopeptide DDA");
    draft = applyAnswer(draft, "Q1", "7");
    expect(draft.taskType).toBe("browse_only");
    draft = applyRecommendedDefaults(draft);
    const payload = toDiscoveryJobPayload({ ...draft, confirmed: true });
    expect(payload.task_type).toBe("");
    expect(payload.grill_confirmed).toBe(true);
    expect(String(payload.prompt || "")).toMatch(/browse_only|immunopeptide|human/i);
  });

  it("personalizes species question for immunopeptide context", () => {
    const draft = applyLocalParse("免疫肽数据");
    draft.answered = { ...draft.answered, Q1: true, Q2: true };
    const q = nextQuestion(draft);
    expect(q?.id).toBe("Q3");
    expect(q?.options.find((o) => o.id === "human_hard")?.recommended).toBe(true);
    const msg = formatQuestionMessage(q!, draft);
    expect(msg).toMatch(/免疫肽|HLA|人源/);
    expect(msg).not.toContain("之所以要确认");
  });

  it("matchOptionAnswer keeps bare numbers stable", () => {
    const q = nextQuestion(applyLocalParse("hi"))!;
    expect(matchOptionAnswer(q, "7")).toBe("7");
    expect(matchOptionAnswer(q, "2.")).toBe("2");
  });
});




describe("explicit project count / strategy revise", () => {
  it("extracts explicit counts from free text", () => {
    expect(extractTargetProjectCount("20个可用项目就行")).toBe(20);
    expect(extractTargetProjectCount("我要20个你为啥写80")).toBe(20);
    expect(extractTargetProjectCount("目标项目数约 15")).toBe(15);
    expect(extractTargetProjectCount("随便找点数据")).toBeNull();
    expect(extractTargetProjectCount("target 2000 usable projects")).toBe(2000);
  });

  it("preserves a confirmed fixed target above the old 300-project cap", () => {
    const draft = applyTargetProjectCount(
      createEmptyIntent("target 2000 usable projects"),
      2000,
      { flexibility: "fixed" },
    );
    draft.specialThemes = ["proteogenomics"];

    const payload = toDiscoveryJobPayload(draft);

    expect(draft.targetProjectCount).toBe(2000);
    expect(payload.max_projects).toBe(2000);
    expect(payload.max_candidate_projects).toBeGreaterThanOrEqual(8000);
    expect(payload.continuous_discovery).toBe(false);
  });

  it("applyLocalParse writes target 20 from natural language", () => {
    const draft = applyLocalParse("免疫肽数据，20个可用项目就行");
    expect(draft.targetProjectCount).toBe(20);
    expect(draft.coverageMode).toBe("curated");
    expect(draft.maxCandidateProjects).toBeGreaterThanOrEqual(80);
    const card = buildStrategyCard(draft);
    expect(card.summaryLines.join("\n")).toContain("固定数量目标：20 个项目（待搜索核验）");
    expect(card.summaryLines.join("\n")).not.toMatch(/约 80 个可用项目/);
  });

  it("revises balanced 80 down to explicit 20 without wiping other fields", () => {
    // Generic balanced default, then lock immunopeptide + force count 20.
    let draft = applyLocalParse("随便找点蛋白质组数据");
    draft = applyRecommendedDefaults(draft);
    expect(draft.targetProjectCount).toBe(80);
    expect(draft.coverageMode).toBe("balanced");
    draft.ptmTypes = ["immunopeptide"];
    draft.specialThemes = ["immunopeptide"];

    const delta = applyLocalParse("我要20个你为啥写80");
    let merged = overlayFilledIntent(draft, delta);
    const n = extractTargetProjectCount("我要20个你为啥写80");
    expect(n).toBe(20);
    merged = applyTargetProjectCount(merged, n!);

    expect(merged.targetProjectCount).toBe(20);
    expect(merged.coverageMode).toBe("curated");
    expect(merged.ptmTypes).toContain("immunopeptide");
    expect(merged.maxCandidateProjects).toBeLessThan(250);

    const card = buildStrategyCard(merged);
    const joined = card.summaryLines.join("\n") + "\n" + card.targetQuota;
    expect(joined).toContain("固定数量目标：20 个项目（待搜索核验）");
    expect(joined).not.toMatch(/约 80 个可用项目/);
    expect(formatConfirmMessage(merged)).toContain("固定数量目标：20 个项目（待搜索核验）");
  });

  it("immunopeptide recommended defaults prefer curated ~20 not balanced 80", () => {
    const draft = applyRecommendedDefaults(applyLocalParse("免疫肽数据"));
    expect(draft.ptmTypes).toContain("immunopeptide");
    expect(draft.coverageMode).toBe("curated");
    expect(draft.targetProjectCount).toBe(20);
    expect(buildStrategyCard(draft).summaryLines.join(" ")).toMatch(/目标规模：约 20 个可用项目/);
  });

  it("mergeLlmFields overrides existing balanced with max_projects 20", () => {
    let draft = applyRecommendedDefaults(applyLocalParse("随便找点蛋白质组数据"));
    expect(draft.targetProjectCount).toBe(80);
    draft.ptmTypes = ["immunopeptide"];
    draft = mergeLlmFields(draft, {
      max_projects: 20,
      target_project_count: 20,
      scale_mode: "curated",
    });
    expect(draft.targetProjectCount).toBe(20);
    expect(buildStrategyCard(draft).summaryLines.join(" ")).toContain("固定数量目标：20 个项目（待搜索核验）");
  });

  it("applyAnswer Q7 accepts free-text count", () => {
    let draft = applyLocalParse("hi");
    draft = applyAnswer(draft, "Q7", "只要 25 个可用项目");
    expect(draft.targetProjectCount).toBe(25);
    expect(draft.answered.Q7).toBe(true);
  });
});

describe("humanizeJobProgress", () => {
  it("maps agent logs into tool-style progress events", () => {
    const result = humanizeJobProgress({
      status: "running",
      logs: [
        {
          sequence: 1,
          type: "candidate_search_started",
          actor: "Repository Search",
          level: "info",
          message: "正在检索 PRIDE 项目：immunopeptide human",
        },
        {
          sequence: 2,
          type: "candidate_search_completed",
          actor: "Repository Search",
          level: "info",
          message: "项目检索目前返回 42 条原始记录。",
          reasoning_summary: "查询覆盖免疫肽与 human 关键词",
        },
        {
          sequence: 3,
          type: "candidate_inspection_started",
          actor: "Candidate Inspector",
          level: "info",
          message: "正在检查项目 PXD001234。",
        },
      ],
      record: { project_count: 3, file_count: 10 },
    });
    expect(result.rawLogCount).toBe(3);
    expect(result.progressEvents.some((e) => e.kind === "tool" && e.name === "仓库检索")).toBe(true);
    expect(result.progressEvents.some((e) => e.kind === "tool" && /候选审查|候选项目审查/.test(e.name || ""))).toBe(
      true,
    );
    expect(result.summary).toMatch(/进行中|候选|检索/);
    expect(result.humanSteps.length).toBeGreaterThan(0);
    // Noise should not dominate
    expect(result.progressEvents.every((e) => e.name !== "Agent 编排")).toBe(true);
  });

  it("does not only show static three-step template when logs exist", () => {
    const result = humanizeJobProgress({
      status: "running",
      logs: [
        { sequence: 1, type: "job_message", message: "正在执行多样性选择。" },
      ],
    });
    const joined = result.humanSteps.join(" | ");
    expect(joined).toMatch(/多样性|数据发现|策略|筛选/);
  });

  it("filters SDK infrastructure noise and translates operational logs", () => {
    const result = humanizeJobProgress({
      status: "running",
      logs: [
        { sequence: 1, type: "sdk_llm_started", actor: "OpenAI Agents SDK", message: "sdk llm started" },
        { sequence: 2, type: "sdk_tool_completed", actor: "OpenAI Agents SDK", message: "sdk tool completed" },
        { sequence: 3, type: "sdk_run_item", actor: "OpenAI Agents SDK", message: "sdk run item" },
        {
          sequence: 4,
          type: "job_message",
          actor: "Discovery Agent",
          message: "Searching PRIDE projects: immunopeptide (up to 4 page(s), max 455 hits).",
        },
        {
          sequence: 5,
          type: "job_message",
          actor: "Discovery Agent",
          message: "Inspecting project PXD001234.",
        },
        {
          sequence: 6,
          type: "job_message",
          actor: "Discovery Agent",
          message: "PXD001234: kept 18 file candidate(s).",
        },
        {
          sequence: 7,
          type: "job_message",
          actor: "Discovery Agent",
          message: "Selected 20 project(s), 100 file(s).",
        },
      ],
      record: { project_count: 20, file_count: 100 },
    });
    const names = result.progressEvents.map((e) => e.name || e.text || "").join(" | ");
    expect(names).not.toMatch(/Agent 编排|sdk llm|sdk tool|sdk run/i);
    expect(result.progressEvents.some((e) => e.name === "仓库检索")).toBe(true);
    expect(result.progressEvents.some((e) => e.name === "候选审查" || e.name === "入选汇总")).toBe(true);
    expect(result.headline).not.toMatch(/sdk /i);
    expect(result.humanSteps.join(" ")).toMatch(/PXD001234|入选|免疫|检索|审查/);
  });

  it("shows legacy repair completion as attempt finished pending audit", () => {
    const result = humanizeJobProgress({
      status: "running",
      logs: [
        {
          sequence: 1,
          type: "discovery_quality_audited",
          actor: "Quality Auditor",
          message: "Quality audit repair_required: 0 delivery-eligible project(s), 2 visible issue(s).",
        },
        {
          sequence: 2,
          type: "discovery_quality_repair_started",
          actor: "Quality Auditor",
          message: "Quality audit found repairable gaps; the Agent is continuing.",
        },
        {
          sequence: 3,
          type: "project_judgments_recorded",
          actor: "Project Judge",
          message: "Project scoring recorded for 2 project(s); 1 currently delivery-qualified.",
        },
        {
          sequence: 4,
          type: "discovery_quality_repair_completed",
          actor: "Quality Auditor",
          message: "Autonomous quality repair completed; 1 project(s) now pass delivery gates.",
        },
      ],
      record: { project_count: 1, file_count: 4 },
    });

    const visible = JSON.stringify(result.progressEvents);
    expect(visible).toMatch(/质量审计|修复尝试结束|项目评分/);
    expect(result.humanSteps.join(" ")).toMatch(/结果待审计|质量/);
    expect(visible).not.toMatch(/自主修复完成/);
    expect(visible).not.toMatch(/repair completed|pass delivery gates/i);
    expect(visible).not.toMatch(/sdk tool|sdk llm/i);
  });

  it("maps authoritative repair results without using events as the success gate", () => {
    const incomplete = humanizeJobProgress({
      status: "completed",
      logs: [
        { sequence: 1, type: "repair_attempt_finished", message: "attempt returned" },
        { sequence: 2, type: "repair_progressed", message: "one blocker removed" },
      ],
      record: {
        project_count: 8,
        business_completion: {
          succeeded: false,
          status: "blocked_with_progress",
          package_kind: "progress",
          success_ui_allowed: false,
          progress: { build_ready_projects: 0, build_ready_files: 0 },
        },
      },
    });

    expect(JSON.stringify(incomplete.progressEvents)).toMatch(/可验证进展|尚待 build-ready/);
    expect(incomplete.summary).toMatch(/搜索已结束|可用文件|批量参数规划/);
    expect(incomplete.summary).not.toMatch(/搜完了/);
  });

  it("fails soft for an unknown future event and never promotes it to completion", () => {
    const result = humanizeJobProgress({
      status: "completed",
      logs: [
        {
          sequence: 1,
          type: "repair_future_success_signal",
          message: "A future worker reported an unfamiliar successful result payload.",
        },
      ],
      record: {
        project_count: 3,
        business_completion: {
          succeeded: false,
          status: "blocked_with_progress",
          package_kind: "progress",
          success_ui_allowed: false,
          progress: { build_ready_projects: 0, build_ready_files: 0 },
        },
      },
    });

    expect(result.summary).toMatch(/搜索已结束|可用文件|批量参数规划/);
    expect(JSON.stringify(result.progressEvents)).not.toMatch(/本轮完成/);
    expect(JSON.stringify(result.progressEvents)).toMatch(/未识别的修复事件|忽略其状态声明/);
    expect(result.progressEvents.some((event) => event.kind === "tool" && event.status === "ok"))
      .toBe(false);
  });

  it("aggregates repeated project inspections into a live step", () => {
    const result = humanizeJobProgress({
      status: "running",
      logs: [
        { sequence: 1, type: "job_message", message: "Inspecting project PXD000001." },
        { sequence: 2, type: "job_message", message: "Inspecting project PXD000002." },
        { sequence: 3, type: "job_message", message: "Inspecting project PXD000003." },
      ],
    });
    const inspectTools = result.progressEvents.filter((e) => e.name === "候选审查");
    // Merged live inspection should not list 3 separate identical-phase cards
    expect(inspectTools.length).toBeLessThanOrEqual(2);
    expect(result.headline).toMatch(/PXD000003|审查/);
    expect(JSON.stringify(result.progressEvents)).toMatch(/本段已审查约 3|PXD000003/);
  });

  it("translates inspection produced and filters tool completed boilerplate", () => {
    const result = humanizeJobProgress({
      status: "running",
      logs: [
        { sequence: 1, type: "tool_completed", actor: "Repository tool", message: "tool completed" },
        { sequence: 2, type: "run_started", message: "run started" },
        {
          sequence: 3,
          type: "candidate_inspection_completed",
          actor: "Candidate Inspector",
          message:
            "Inspection produced 20 selected project(s) and 100 selected file(s); next action: stop",
        },
      ],
      record: { project_count: 20, file_count: 100 },
    });
    const blob = JSON.stringify(result);
    expect(blob).not.toMatch(/tool completed|run started|sdk /i);
    expect(blob).toMatch(/审查完成|入选约 20/);
    expect(result.progressEvents.every((e) => e.name !== "Agent 编排")).toBe(true);
  });
});

describe("composeNextQuestionBody", () => {
  it("uses LLM lead with compact options instead of full template when no numbered list", () => {
    const spec = createEmptyIntent();
    spec.specialThemes = ["immunopeptide"];
    spec.species = ["human"];
    const question = nextQuestion(spec) || {
      id: "Q1" as const,
      prompt: "这些数据主要用来做什么？",
      why: "不同任务标准不同",
      options: [
        { id: "rt", label: "RT 预测", recommended: false },
        { id: "browse", label: "先只找数据", recommended: true, reason: "任务未定更稳妥" },
      ],
    };
    const body = composeNextQuestionBody(
      "免疫肽场景下，如果你还没定下游模型，我建议先只找数据。",
      question,
      spec,
      "FALLBACK",
    );
    expect(body).toContain("免疫肽场景");
    expect(body).toMatch(/1[.)]/);
    expect(body).not.toContain("FALLBACK");
  });

  it("trusts LLM body when it already lists options", () => {
    const spec = createEmptyIntent();
    const question = {
      id: "Q1" as const,
      prompt: "x",
      why: "y",
      options: [{ id: "a", label: "A" }],
    };
    const lead = ["分析如下。", "1) 只要 human", "2) 开放搜索"].join("\n");
    const body = composeNextQuestionBody(lead, question as never, spec, "FALLBACK");
    expect(body).toContain("1) 只要 human");
    expect(body).toContain("2) 开放搜索");
    expect(body).not.toContain("FALLBACK");
    expect(body.includes("1) 只要 human")).toBe(true);
  });
});

describe("objective pollution & free-text absorb", () => {
  it("isPollutedObjective catches bare option tokens", () => {
    expect(isPollutedObjective("7")).toBe(true);
    expect(isPollutedObjective("7)")).toBe(true);
    expect(isPollutedObjective("browse_only")).toBe(true);
    expect(isPollutedObjective("免疫肽数据")).toBe(false);
  });

  it("applyAnswer(Q1, \"7\") does not write objective \"7\"", () => {
    const draft = createEmptyIntent("找免疫肽");
    const next = applyAnswer(draft, "Q1", "7");
    expect(next.taskType).toBe("browse_only");
    expect(next.objective).not.toBe("7");
    expect(isPollutedObjective(next.objective)).toBe(false);
    expect(deriveObjective(next)).not.toMatch(/^7$/);
  });

  it("applyAnswer(Q1, free-text immunopeptide) sets theme and human goal", () => {
    let draft = createEmptyIntent();
    draft = applyAnswer(draft, "Q1", "我想要免疫肽数据");
    expect(draft.taskType).toBe("browse_only");
    expect(draft.ptmTypes).toContain("immunopeptide");
    expect(isPollutedObjective(draft.objective)).toBe(false);
    const goal = deriveObjective(draft);
    expect(goal).toMatch(/免疫肽|HLA|数据/);
    expect(goal).not.toBe("7");
  });

  it("applyAnswer(Q3, 人) prefers human", () => {
    let draft = createEmptyIntent("免疫肽");
    draft = applyAnswer(draft, "Q3", "人");
    expect(draft.species).toContain("human");
    expect(draft.speciesPolicy === "prefer" || draft.speciesPolicy === "include_only").toBe(true);
  });

  it("isStrategyComplaintPrompt detects 为什么目标是7", () => {
    expect(isStrategyComplaintPrompt("为什么目标是7")).toBe(true);
    expect(isStrategyComplaintPrompt("你写的目标是7")).toBe(true);
    expect(isStrategyComplaintPrompt("我不是这个意思")).toBe(true);
    expect(isStrategyComplaintPrompt("只要 human")).toBe(false);
  });

  it("isMetaOrConfusedPrompt catches 为什么 / 我不是这个意思", () => {
    expect(isMetaOrConfusedPrompt("为什么目标是7")).toBe(true);
    expect(isMetaOrConfusedPrompt("我不是这个意思")).toBe(true);
    expect(isMetaOrConfusedPrompt("2")).toBe(false);
  });

  it("buildStrategyCard never shows 目标：7 after polluted objective", () => {
    let draft = createEmptyIntent("免疫肽数据");
    draft.objective = "7";
    draft.taskType = "browse_only";
    draft.ptmTypes = ["immunopeptide"];
    draft.species = ["human"];
    draft.speciesPolicy = "prefer";
    draft.acquisitionMode = "dda";
    draft.runHorizon = "candidates_only";
    draft.coverageMode = "curated";
    draft.targetProjectCount = 20;
    draft.maxCandidateProjects = 80;
    draft = sanitizeIntentObjective(draft);
    const card = buildStrategyCard(draft);
    const blob = card.summaryLines.join("\n");
    expect(blob).not.toMatch(/目标：\s*7\b/);
    expect(blob).toMatch(/目标：/);
    expect(deriveObjective(draft)).toMatch(/免疫肽|人源|DDA|数据/);
  });

  it("end-to-end dialogue fields: immuno + 人 + dda + 20", () => {
    let draft = applyLocalParse("我想要免疫肽数据");
    draft = applyAnswer(draft, "Q1", "我想要免疫肽数据");
    draft = applyAnswer(draft, "Q3", "人");
    draft = applyAnswer(draft, "Q4", "dda");
    draft = applyTargetProjectCount(draft, 20);
    draft = sanitizeIntentObjective(draft);
    expect(draft.ptmTypes).toContain("immunopeptide");
    expect(draft.species).toContain("human");
    expect(draft.acquisitionMode).toBe("dda");
    expect(draft.targetProjectCount).toBe(20);
    expect(deriveObjective(draft)).not.toBe("7");
    const card = buildStrategyCard(draft);
    expect(card.summaryLines.join(" | ")).not.toMatch(/目标：\s*7\b/);
    expect(card.summaryLines).toContain("固定数量目标：20 个项目（待搜索核验）；候选池约 80");
  });
});

describe("orientation & right-panel goal", () => {
  it("orientation prompt is not a data goal", () => {
    expect(isOrientationPrompt("你觉得目前什么任务好，你能干什么")).toBe(true);
    expect(isOrientationPrompt("免疫肽吧")).toBe(false);
    const draft = applyLocalParse("你觉得目前什么任务好，你能干什么");
    expect(draft.objective).toBe("");
    expect(isPollutedObjective("你觉得目前什么任务好，你能干什么")).toBe(true);
  });

  it("after immuno+human, deriveObjective is human-readable not orientation text", () => {
    let draft = applyLocalParse("你觉得目前什么任务好，你能干什么");
    draft = applyAnswer(draft, "Q1", "免疫肽吧");
    draft = applyAnswer(draft, "Q3", "人类");
    draft = sanitizeIntentObjective(draft);
    const goal = deriveObjective(draft);
    expect(goal).not.toMatch(/你觉得|能干什么/);
    expect(goal).toMatch(/免疫肽|HLA|人源|数据/);
    expect(draft.ptmTypes).toContain("immunopeptide");
    expect(draft.species).toContain("human");
  });

    it("mergeLlmFields does not lock invented project counts as fixed user counts", () => {
    // first-pass fill_gaps: LLM scale band fills gaps without inventing a fixed lock
    let draft = applyLocalParse("免疫肽");
    draft = mergeLlmFields(draft, { scale_mode: "curated", target_project_count: 50 }, [], "llm guess", "fill_gaps");
    expect(draft.quotaFlexibility).not.toBe("fixed");
    expect(draft.targetProjectCount).toBe(20);
    // user explicit still wins even under patch
    draft = applyTargetProjectCount(draft, 20);
    expect(draft.quotaFlexibility).toBe("fixed");
    draft = mergeLlmFields(draft, { target_project_count: 50, scale_mode: "balanced" }, [], "", "patch");
    expect(draft.targetProjectCount).toBe(20);
    expect(draft.quotaFlexibility).toBe("fixed");
  });

  it("detectSpeciesSignals prefers latest mention and recognizes fish", () => {
    expect(detectSpeciesSignals("我要鱼类")?.species).toEqual(["fish"]);
    expect(detectSpeciesSignals("human 然后改成鱼类")?.species).toEqual(["fish"]);
    expect(detectSpeciesSignals("斑马鱼")?.species).toEqual(["zebrafish"]);
  });

  it("mergeLlmFields patch overwrites human with fish like update_strategy", () => {
    let draft = applyLocalParse("人源免疫肽");
    draft = applyRecommendedDefaults(draft);
    draft.species = ["human"];
    draft.speciesPolicy = "include_only";
    draft = mergeLlmFields(
      draft,
      { species: ["fish"], species_policy: "include_only", max_projects: 20, target_project_count: 20 },
      [],
      "user switched to fish",
      "patch",
    );
    expect(draft.species).toEqual(["fish"]);
    expect(draft.speciesPolicy).toBe("include_only");
    expect(draft.targetProjectCount).toBe(20);
    const goal = deriveObjective(draft);
    expect(goal).toMatch(/鱼|fish|免疫肽|HLA/i);
    expect(goal).not.toMatch(/人源/);
    const card = buildStrategyCard(draft);
    expect(card.summaryLines.join(" ")).toMatch(/fish|鱼类/);
    expect(card.summaryLines.join(" ")).not.toMatch(/必须为：human/);
  });

  it("absorbFreeTextSignals applies fish switch on free text", () => {
    let draft = applyLocalParse("人源免疫肽");
    draft = applyRecommendedDefaults(draft);
    draft.species = ["human"];
    draft.speciesPolicy = "include_only";
    draft = absorbFreeTextSignals(draft, "我要鱼类");
    expect(draft.species).toEqual(["fish"]);
    expect(draft.speciesPolicy).toBe("include_only");
  });
});


describe("agentic soft-fill (true agent, not form walk)", () => {
  it("hasDomainSubstance detects immunopeptide + species", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "人源免疫肽");
    expect(hasDomainSubstance(s)).toBe(true);
  });

  it("agenticSoftFill marks required gates and becomes confirm-ready", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "人源免疫肽，先 20 个候选");
    s = agenticSoftFill(s);
    expect(isReadyForConfirm(s)).toBe(true);
    expect(s.species).toContain("human");
    expect(s.ptmTypes).toContain("immunopeptide");
    expect(s.targetProjectCount).toBe(20);
  });

  it("fish revise overwrites human via absorb + soft-fill", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "人源免疫肽 20 个");
    s = agenticSoftFill(s);
    expect(s.species).toEqual(["human"]);
    s = absorbFreeTextSignals(s, "我要鱼类");
    s = agenticSoftFill(s);
    expect(s.species).toEqual(["fish"]);
    expect(isReadyForConfirm(s)).toBe(true);
    const card = buildStrategyCard(s);
    expect(card.summaryLines.join(" ")).toMatch(/鱼/);
    expect(card.summaryLines.join(" ")).not.toMatch(/人源/);
  });

  it("orientation chat alone is not domain substance", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "你能干什么");
    expect(hasDomainSubstance(s)).toBe(false);
    s = agenticSoftFill(s);
    expect(isReadyForConfirm(s)).toBe(false);
  });
});

describe("semantic strategy gap validator", () => {
  it("keys gaps by semantic slots instead of Q1-Q10 progress", () => {
    const empty = assessStrategyGaps(createEmptyIntent());
    expect(empty.required_missing).toEqual(["task", "coverage", "objective"]);
    expect(empty.ready_for_confirm).toBe(false);

    const semantic = {
      ...createEmptyIntent(),
      objective: "Human immunopeptide candidate discovery",
      taskType: "browse_only" as const,
      runHorizon: "candidates_only" as const,
      ptmTypes: ["immunopeptide"],
      coverageMode: "curated" as const,
      targetProjectCount: 20,
      maxCandidateProjects: 80,
      answered: {},
    };
    const withoutQuestionFlags = assessStrategyGaps(semantic);
    const withQuestionFlags = assessStrategyGaps({
      ...semantic,
      answered: {
        Q1: true,
        Q2: true,
        Q3: true,
        Q4: true,
        Q5: true,
        Q6: true,
        Q7: true,
        Q8: true,
        Q9: true,
        Q10: true,
      },
    });

    expect(withoutQuestionFlags).toEqual(withQuestionFlags);
    expect(withoutQuestionFlags.required_missing).toEqual([]);
    expect(withoutQuestionFlags.ready_for_confirm).toBe(true);
  });

  it("treats explicitly open acquisition, labeling, and species as resolved", () => {
    const spec = {
      ...createEmptyIntent(),
      objective: "Open immunopeptide exploration",
      taskType: "browse_only" as const,
      runHorizon: "candidates_only" as const,
      ptmTypes: ["immunopeptide"],
      coverageMode: "curated" as const,
      targetProjectCount: 20,
      acquisitionMode: "unknown" as const,
      labelingStrategy: "any" as const,
      speciesPolicy: "open" as const,
      resolvedFields: ["acquisition_mode", "labeling_strategy", "species_policy"],
    };

    expect(assessStrategyGaps(spec).optional_missing).toEqual([]);
  });

  it("serializes the complete strategy and field-resolution memory", () => {
    const spec = {
      ...createEmptyIntent("complete memory"),
      quotaFlexibility: "open_ended" as const,
      timeBudget: "multi_round" as const,
      onSafetyCeiling: "stop" as const,
      legacyFloorRatio: 0.2,
      openRisks: ["verify labeling metadata"],
      resolvedFields: ["quota_flexibility", "labeling_strategy"],
    };

    expect(intentSnapshotForLlm(spec)).toMatchObject({
      quota_flexibility: "open_ended",
      time_budget: "multi_round",
      on_safety_ceiling: "stop",
      legacy_floor_ratio: 0.2,
      open_risks: ["verify labeling metadata"],
      resolved_fields: ["quota_flexibility", "labeling_strategy"],
      repository: "pride",
    });
  });
});


  it("mergeLlmFields ignores DIA token as species and empty species shell", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "人源免疫肽 20 个");
    s = agenticSoftFill(s);
    expect(s.species).toEqual(["human"]);
    s = mergeLlmFields(
      s,
      { species: ["DIA"], acquisition_mode: "dia" },
      [],
      "",
      "patch",
    );
    expect(s.species).toEqual(["human"]);
    expect(s.acquisitionMode).toBe("dia");
    // empty species array without open policy must not wipe card
    s = mergeLlmFields(s, { species: [] }, [], "", "patch");
    expect(s.species).toEqual(["human"]);
  });

describe("autonomous multi-field patch mid-conversation", () => {
  it("patches count over prior quota", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "人源免疫肽 50 个");
    s = agenticSoftFill(s);
    expect(s.targetProjectCount).toBe(50);
    s = absorbFreeTextSignals(s, "目标改成 20 个");
    s = agenticSoftFill(s);
    expect(s.targetProjectCount).toBe(20);
  });

  it("patches acquisition dda -> dia", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "免疫肽 DDA");
    expect(s.acquisitionMode).toBe("dda");
    s = absorbFreeTextSignals(s, "改成 DIA");
    expect(s.acquisitionMode).toBe("dia");
  });

  it("patches task type to rt_prediction", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "人源 DDA 做 RT 预测，20 个");
    s = agenticSoftFill(s);
    expect(s.taskType).toBe("rt_prediction");
    expect(s.species).toContain("human");
    expect(s.targetProjectCount).toBe(20);
    expect(isReadyForConfirm(s)).toBe(true);
    expect(assessStrategyGaps(s).required_missing).not.toContain("horizon");
  });

  it("mergeLlmFields patch overwrites arbitrary extra_fields", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "人源免疫肽 20 个");
    s = agenticSoftFill(s);
    s = mergeLlmFields(
      s,
      {
        species: ["mouse"],
        species_policy: "include_only",
        acquisition_mode: "dia",
        max_projects: 15,
        target_project_count: 15,
        scale_mode: "curated",
        task_type: "denovo",
      },
      [],
      "",
      "patch",
      { allowCountOverwrite: true }, // user said "改成 15" this turn
    );
    expect(s.species).toEqual(["mouse"]);
    expect(s.acquisitionMode).toBe("dia");
    expect(s.targetProjectCount).toBe(15);
    expect(s.taskType).toBe("denovo");
  });
});


describe("recommendation / explore chat", () => {
  it("shouldDeferConfirmCard defers on recommendation asks", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "想做点免疫肽，有推荐吗");
    s = agenticSoftFill(s);
    expect(s.ptmTypes).toContain("immunopeptide");
    expect(shouldDeferConfirmCard(s, "想做点免疫肽，有推荐吗")).toBe(true);
  });

  it("immunopeptide Q1 does not recommend PTM de novo", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "人源免疫肽");
    // Force Q1 menu even if absorb already soft-answered task
    s = { ...s, answered: { ...s.answered, Q1: false }, taskType: "" as never };
    const q = nextQuestion(s);
    expect(q?.id).toBe("Q1");
    const ptm = q?.options.find((o) => o.id === "ptm_denovo");
    const browse = q?.options.find((o) => o.id === "browse_only");
    expect(browse?.recommended).toBe(true);
    expect(ptm?.recommended).toBeFalsy();
  });

  it("composeNextQuestionBody always attaches options for plain LLM questions", () => {
    let s = createEmptyIntent();
    s = absorbFreeTextSignals(s, "免疫肽");
    const q = nextQuestion(s);
    const body = composeNextQuestionBody(
      "你更想先摸清单还是直接定下游任务？",
      q as never,
      s,
      "FALLBACK",
    );
    expect(body).toMatch(/1[.)]/);
    expect(body).toMatch(/按推荐默认/);
  });
});
