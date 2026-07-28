import { describe, expect, it } from "vitest";

import {
  appendAgentDialogue,
  decodeAgentTurnResponse,
  formatAgentNextDecision,
  reduceAgentTurn,
  reduceAgentUnavailable,
} from "./agent-turn";
import { applyTargetProjectCount, buildStrategyCard } from "./grill-tree";
import { createEmptyIntent } from "./intent-spec";

describe("agent-turn response boundary", () => {
  it("keeps recent dialogue context for follow-up scientific reasoning", () => {
    const history = appendAgentDialogue([], "有鱼类的吗？", "有可能，先不改策略。", 4);
    const continued = appendAgentDialogue(history, "那就改成斑马鱼", "已改成斑马鱼。", 4);

    expect(continued).toEqual([
      { role: "user", content: "有鱼类的吗？" },
      { role: "assistant", content: "有可能，先不改策略。" },
      { role: "user", content: "那就改成斑马鱼" },
      { role: "assistant", content: "已改成斑马鱼。" },
    ]);
    expect(appendAgentDialogue(continued, "再聊聊", "可以", 4)).toEqual([
      { role: "user", content: "那就改成斑马鱼" },
      { role: "assistant", content: "已改成斑马鱼。" },
      { role: "user", content: "再聊聊" },
      { role: "assistant", content: "可以" },
    ]);
  });

  it("decodes the D1 envelope at one typed boundary", () => {
    const turn = decodeAgentTurnResponse({
      status: "completed",
      // A turn may update the card and offer the next decision, but its
      // mutation action remains update_strategy rather than clarify.
      action: "update_strategy",
      assistant_message: "Let us choose the most useful cohort.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: { species: ["human"], species_policy: "prefer" },
        },
      ],
      next_decision: {
        focus: "species",
        question: "Should human studies be preferred or required?",
        recommendation: {
          id: "human_prefer",
          label: "Prefer human",
          reason: "It keeps useful comparison cohorts available.",
        },
        options: [
          { id: "human_prefer", label: "Prefer human", reason: "Broader recall." },
          { id: "human_only", label: "Human only", reason: "Strict cohort." },
        ],
        allow_free_text: false,
      },
      gap_report: {
        required_missing: ["horizon"],
        optional_missing: ["acquisition"],
        ready_for_confirm: false,
      },
    });

    expect(turn.action).toBe("update_strategy");
    expect(turn.tool_calls).toEqual([
      {
        name: "update_strategy",
        arguments: { species: ["human"], speciesPolicy: "prefer" },
      },
    ]);
    expect(turn.next_decision?.focus).toBe("species");
    // Free text is a UI invariant, even if a model returns false.
    expect(turn.next_decision?.allow_free_text).toBe(true);
    expect(turn.gap_report?.required_missing).toEqual([]);
  });

  it("canonicalizes predeclared option strategy patches at the shared response boundary", () => {
    const turn = decodeAgentTurnResponse({
      action: "clarify",
      assistant_message: "Choose the delivery horizon.",
      tool_calls: [],
      next_decision: {
        focus: "run_horizon",
        target_fields: ["run_horizon"],
        question: "Should I only prepare a plan or collect reviewed candidates?",
        recommendation: {
          id: "reviewed_candidates",
          label: "Collect reviewed candidates",
          reason: "It produces evidence-backed projects without claiming a downstream release.",
          strategy_patch: { run_horizon: "CANDIDATES_REVIEWED" },
        },
        options: [
          {
            id: "plan_only",
            label: "Plan only",
            reason: "Do not access PRIDE yet.",
            strategy_patch: { run_horizon: "plan_only" },
          },
          {
            id: "reviewed_candidates",
            label: "Collect reviewed candidates",
            reason: "Search and review the candidate projects.",
            strategy_patch: {
              run_horizon: "candidates_reviewed",
              target_project_count: 20,
            },
          },
        ],
      },
    });

    expect(turn.next_decision).toBeNull();
  });

  it.each([
    ["accept", true, "passed"],
    ["repair", true, "repaired"],
    ["reject", false, "rejected"],
    ["unavailable", false, "unavailable"],
    ["budget_exhausted", false, "budget_exhausted"],
  ] as const)("normalizes semantic verification verdict %s", (verdict, verified, expected) => {
    const turn = decodeAgentTurnResponse({
      action: "update_strategy",
      assistant_message: "Reviewed the proposed strategy delta.",
      tool_calls: [{ name: "update_strategy", arguments: { species: ["human"] } }],
      semantic_verification: {
        verified,
        verdict,
        patch: verified ? { species: ["human"] } : {},
        rationale: `verifier reported ${verdict}`,
      },
    });

    expect(turn.semantic_verification?.verdict).toBe(expected);
    expect(turn.semantic_verification?.verified).toBe(verified);
    expect(turn.semantic_verification?.rationale).toBe(`verifier reported ${verdict}`);
  });

  it.each([
    ["an explicit rejection", { verdict: "rejected", verified: false }],
    ["an inconsistent successful verdict that cannot authorize", { verdict: "passed", verified: false }],
  ])("fails closed for %s", (_label, semanticVerification) => {
    const original = createEmptyIntent("unchanged strategy");
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "The proposed update was not authorized.",
      tool_calls: [{ name: "update_strategy", arguments: { species: ["mouse"] } }],
      semantic_verification: {
        ...semanticVerification,
        patch: { species: ["mouse"] },
        rationale: "The patch is not supported by the latest user message.",
      },
    });

    expect(turn.semantic_verification?.verdict).toBe("rejected");
    // Write attempt stays labeled update_strategy for FE chrome; patch is stripped.
    expect(turn.action).toBe("update_strategy");
    expect(turn.tool_calls).toEqual([]);
    expect(turn.strategy_patch).toBeNull();
    expect(turn.extra_fields).toEqual({});
    expect(reduceAgentTurn(original, turn).spec).toBe(original);
    expect(reduceAgentTurn(original, turn).strategyUpdated).toBe(false);
  });

  it.each(["unavailable", "budget_exhausted"] as const)(
    "keeps the typed update_strategy tool authoritative when verification is %s",
    (verdict) => {
      const turn = decodeAgentTurnResponse({
        status: "completed",
        action: "update_strategy",
        assistant_message: "Applied the typed strategy update.",
        tool_calls: [{ name: "update_strategy", arguments: { species: ["mouse"] } }],
        semantic_verification: {
          verdict,
          verified: false,
          patch: {},
          rationale: "The bounded verifier did not run.",
        },
      });

      expect(turn.action).toBe("update_strategy");
      expect(turn.strategy_patch).toEqual({ species: ["mouse"] });
      expect(reduceAgentTurn(createEmptyIntent(), turn).spec.species).toEqual(["mouse"]);
    },
  );

  it("defensively refuses a rejected patch at the reducer boundary", () => {
    const original = createEmptyIntent("unchanged strategy");
    const accepted = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Applied.",
      tool_calls: [{ name: "update_strategy", arguments: { species: ["mouse"] } }],
    });
    const rejection = decodeAgentTurnResponse({
      action: "advise",
      assistant_message: "Rejected.",
      tool_calls: [],
      semantic_verification: {
        verdict: "rejected",
        verified: false,
        patch: { species: ["mouse"] },
      },
    }).semantic_verification;

    const result = reduceAgentTurn(original, {
      ...accepted,
      semantic_verification: rejection,
    });

    expect(result.spec).toBe(original);
    expect(result.strategyUpdated).toBe(false);
  });

  it("keeps legacy projections usable without letting them override a primary envelope", () => {
    const legacy = decodeAgentTurnResponse({
      status: "completed",
      intent: "revise",
      assistant_message: "Updated.",
      extra_fields: { acquisition_mode: "dia" },
    });
    expect(legacy.action).toBe("update_strategy");
    expect(legacy.tool_calls).toEqual([
      { name: "update_strategy", arguments: { acquisitionMode: "dia" } },
    ]);

    const primaryChat = decodeAgentTurnResponse({
      status: "completed",
      action: "chat",
      assistant_message: "We can think this through first.",
      tool_calls: [],
      // A stale compatibility projection must not become an implicit tool call.
      extra_fields: { species: ["mouse"] },
    });
    expect(primaryChat.tool_calls).toEqual([]);
  });

  it("renders exactly the personalized decision supplied by the agent", () => {
    const turn = decodeAgentTurnResponse({
      action: "clarify",
      assistant_message: "For your HLA exploration, cohort policy matters most.",
      tool_calls: [],
      next_decision: {
        focus: "species",
        question: "How strict should the human cohort be?",
        recommendation: {
          id: "prefer",
          label: "Prefer human",
          reason: "It preserves recall while prioritizing the intended cohort.",
        },
        options: [
          { id: "prefer", label: "Prefer human", reason: "Balanced recall." },
          { id: "only", label: "Human only", reason: "Hard constraint." },
        ],
        allow_free_text: true,
      },
    });

    const body = formatAgentNextDecision(turn.next_decision);
    expect(body).toContain("How strict should the human cohort be?");
    expect(body).toContain("Prefer human");
    expect(body).toContain("preserves recall");
    expect(body).toContain("1. Prefer human");
    expect(body).toContain("2. Human only");
    expect(body).toContain("直接回复选项，或用自然语言说你的判断");
    expect(body).not.toMatch(/Q1|Q2|Q10/);
  });
});

describe("Agent-owned defaults boundary", () => {
  it("applies defaults only when the available Agent emits an update tool", () => {
    const onlineTurn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Applied the recommended defaults.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: { task_type: "browse_only", run_horizon: "candidates_only" },
        },
      ],
    });

    const reduction = reduceAgentTurn(createEmptyIntent(), onlineTurn);
    expect(reduction.strategyUpdated).toBe(true);
    expect(reduction.spec).toMatchObject({
      taskType: "browse_only",
      runHorizon: "candidates_reviewed",
    });
  });

});

describe("agent-turn strategy reducer", () => {
  it("normalizes the canonical backend arguments.patch envelope", () => {
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Updated.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            patch: { species: ["mouse"], labeling_strategy: "tmt" },
          },
        },
      ],
    });

    expect(turn.tool_calls[0].arguments).toEqual({
      species: ["mouse"],
      labelingStrategy: "tmt",
    });
    const result = reduceAgentTurn(createEmptyIntent(), turn, { userMessage: "Apply those choices." });
    expect(result.spec.species).toEqual(["mouse"]);
    expect(result.spec.labelingStrategy).toBe("tmt");
    expect(result.spec.notes).not.toContain("patch:");
  });

  it("derives a display objective from committed scientific fields instead of re-asking it", () => {
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Updated the scientific scope.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            patch: {
              species: ["fish"],
              species_policy: "include_only",
              special_themes: ["鱼类免疫肽"],
              acquisition_mode: "dia",
              run_horizon: "candidates_reviewed",
              target_project_count: 15,
            },
          },
        },
      ],
    });

    const result = reduceAgentTurn(createEmptyIntent(), turn);

    expect(result.spec.objective).toMatch(/免疫肽.*鱼类|鱼类.*免疫肽|fish/i);
    expect(result.gapReport.required_missing).not.toContain("objective");
  });

  it("re-synthesizes a stale objective when committed scientific fields change", () => {
    const spec = {
      ...createEmptyIntent("最初找斑马鱼免疫肽 DIA 数据"),
      objective: "免疫肽/HLA 配体 · Danio rerio · 先摸清有哪些数据 · DIA",
      taskType: "browse_only" as const,
      species: ["Danio rerio"],
      speciesPolicy: "include_only" as const,
      acquisitionMode: "dia" as const,
      specialThemes: ["immunopeptidomics"],
    };
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Updated the scientific scope.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            patch: {
              species: ["non-human primate"],
              species_policy: "include_only",
              acquisition_mode: "unknown",
              target_project_count: 18,
            },
          },
        },
      ],
    });

    const result = reduceAgentTurn(spec, turn);

    expect(result.spec.objective).toContain("non-human primate");
    expect(result.spec.objective).not.toMatch(/Danio rerio|DIA/i);
  });

  it("prefers committed theme fields over stale objective and prompt text", () => {
    const spec = {
      ...createEmptyIntent("最初找斑马鱼免疫肽数据"),
      objective: "免疫肽/HLA 配体 · Danio rerio · 先摸清有哪些数据",
      taskType: "browse_only" as const,
      species: ["Danio rerio"],
      speciesPolicy: "include_only" as const,
      ptmTypes: ["immunopeptide"],
    };
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Switched the scientific theme.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            patch: {
              ptm_types: ["phospho"],
            },
          },
        },
      ],
    });

    const result = reduceAgentTurn(spec, turn);

    expect(result.spec.objective).toContain("磷酸化蛋白组");
    expect(result.spec.objective).not.toContain("免疫肽");
  });

  it("preserves explicit null clears in an update_strategy patch", () => {
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "The fixed project quota is now open.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            patch: {
              quota_flexibility: "open_ended",
              target_project_count: null,
            },
          },
        },
      ],
    });

    expect(turn.tool_calls).toEqual([
      {
        name: "update_strategy",
        arguments: {
          quotaFlexibility: "open_ended",
          targetProjectCount: null,
        },
      },
    ]);
    expect(turn.extra_fields).toEqual({
      quotaFlexibility: "open_ended",
      targetProjectCount: null,
    });
  });

  it("leaves the IntentSpec untouched for pure chat/advice", () => {
    const spec = applyTargetProjectCount(
      {
        ...createEmptyIntent("human immunopeptide exploration"),
        taskType: "browse_only",
        runHorizon: "candidates_only",
        ptmTypes: ["immunopeptide"],
      },
      20,
    );
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "advise",
      assistant_message: "Browse first; de novo can remain a downstream option.",
      tool_calls: [],
    });

    const result = reduceAgentTurn(spec, turn, { userMessage: "Why browse first?" });
    expect(result.spec).toBe(spec);
    expect(result.strategyUpdated).toBe(false);
  });

  it("applies an arbitrary update_strategy patch in the same turn", () => {
    const spec = createEmptyIntent("initial exploration");
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "I updated every constraint you changed.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            species: ["fish"],
            species_policy: "include_only",
            target_project_count: 15,
            max_projects: 15,
            quota_flexibility: "fixed",
            acquisition_mode: "dia",
            task_type: "rt_prediction",
            ptm_types: ["phospho"],
            special_themes: ["drug_treated_cell_line"],
            labeling_strategy: "tmt",
            scale_mode: "curated",
            run_horizon: "ai_ready_table",
            objective: "Fish DIA RT benchmark",
          },
        },
      ],
    });

    const result = reduceAgentTurn(spec, turn, { userMessage: "Use max_projects=15 instead." });
    expect(result.strategyUpdated).toBe(true);
    expect(result.spec).toMatchObject({
      species: ["fish"],
      speciesPolicy: "include_only",
      targetProjectCount: 15,
      quotaFlexibility: "fixed",
      acquisitionMode: "dia",
      taskType: "rt_prediction",
      ptmTypes: ["phospho"],
      specialThemes: ["drug_treated_cell_line"],
      labelingStrategy: "tmt",
      coverageMode: "curated",
      runHorizon: "candidates_reviewed",
      objective: "Fish DIA RT benchmark",
      confirmed: false,
    });
  });

  it("applies a different field combination without phrase-specific parsing", () => {
    const spec = createEmptyIntent("phosphoproteomics planning");
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Those choices are now on the strategy card.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            species: ["mouse"],
            species_policy: "prefer",
            labeling_strategy: "tmt",
            run_horizon: "candidates_reviewed",
            ptm_types: ["phospho"],
          },
        },
      ],
    });

    const result = reduceAgentTurn(spec, turn, { userMessage: "Apply those choices." });
    expect(result.spec).toMatchObject({
      species: ["mouse"],
      speciesPolicy: "prefer",
      labelingStrategy: "tmt",
      runHorizon: "candidates_reviewed",
      ptmTypes: ["phospho"],
    });
  });

  it("covers the full IntentSpec strategy surface", () => {
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "The complete strategy has been updated.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            objective: "Reviewed mouse phosphoproteomics PSM cohort",
            task_type: "psm_scoring",
            run_horizon: "candidates_reviewed",
            species: ["mouse"],
            species_policy: "prefer",
            species_coverage: "prefer_listed",
            acquisition_mode: "dda",
            mixed_acquisition_policy: "reject_mixed",
            ptm_types: ["phospho"],
            special_themes: ["patient_sample"],
            labeling_strategy: "tmt",
            labeling_hard: true,
            scale_mode: "balanced",
            target_project_count: 33,
            max_candidate_projects: 333,
            quota_flexibility: "fixed",
            time_budget: "multi_round",
            on_safety_ceiling: "stop",
            instrument_preference: "newer_with_legacy_floor",
            legacy_floor_ratio: 0.25,
            exclude_rules: ["cell lines"],
            success_criteria: ["manual metadata review complete"],
            scientific_constraints: [
              {
                id: "cohort.min-participants",
                label: "At least 30 participants",
                dimension: "participant_count",
                operator: "gte",
                value: 30,
                strength: "hard",
                scope: "project",
                evidence_required: true,
                source: "user",
              },
            ],
            notes: "Prefer studies with raw files.",
            open_risks: ["Sparse treatment metadata"],
            repository: "pride",
          },
        },
      ],
    });

    const result = reduceAgentTurn(createEmptyIntent(), turn, {
      userMessage: "Use max_projects=33 and apply that strategy.",
    });
    expect(result.spec).toMatchObject({
      objective: "Reviewed mouse phosphoproteomics PSM cohort",
      taskType: "psm_scoring",
      runHorizon: "candidates_reviewed",
      species: ["mouse"],
      speciesPolicy: "prefer",
      speciesCoverage: "prefer_listed",
      acquisitionMode: "dda",
      mixedAcquisitionPolicy: "reject_mixed",
      ptmTypes: ["phospho"],
      specialThemes: ["patient_sample"],
      labelingStrategy: "tmt",
      labelingHard: true,
      coverageMode: "balanced",
      targetProjectCount: 33,
      maxCandidateProjects: 333,
      quotaFlexibility: "fixed",
      timeBudget: "multi_round",
      onSafetyCeiling: "stop",
      instrumentPreference: "newer_with_legacy_floor",
      legacyFloorRatio: 0.25,
      excludeRules: ["cell lines"],
      successCriteria: ["manual metadata review complete"],
      scientificConstraints: [
        expect.objectContaining({
          id: "cohort.min-participants",
          operator: "gte",
          value: 30,
          strength: "hard",
        }),
      ],
      repository: "pride",
    });
    expect(result.spec.notes).toContain("Prefer studies with raw files.");
    expect(result.spec.openRisks).toContain("Sparse treatment metadata");
  });

  it("lets a verified patch explicitly revise a previously fixed count", () => {
    const fixed = applyTargetProjectCount(createEmptyIntent("human cohort"), 20);
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Species and project count changed.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            species: ["mouse"],
            target_project_count: 80,
            max_projects: 80,
            quota_flexibility: "fixed",
            scale_mode: "balanced",
          },
        },
      ],
    });

    const result = reduceAgentTurn(fixed, turn, { userMessage: "Apply the verified revision." });
    expect(result.spec.species).toEqual(["mouse"]);
    expect(result.spec.targetProjectCount).toBe(80);
    expect(result.spec.quotaFlexibility).toBe("fixed");
  });

  it.each([
    ["an open-ended transition", { quota_flexibility: "open_ended" }],
    [
      "an explicit null clear",
      { quota_flexibility: "open_ended", target_project_count: null },
    ],
  ])("reopens a fixed quota with %s without rewriting coverage or time budget", (_case, patch) => {
    const fixed = applyTargetProjectCount(createEmptyIntent("human cohort"), 20);
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "The project quota is now open-ended.",
      tool_calls: [{ name: "update_strategy", arguments: patch }],
    });

    const result = reduceAgentTurn(fixed, turn);
    expect(result.spec.quotaFlexibility).toBe("open_ended");
    expect(result.spec.targetProjectCount).toBeNull();
    expect(result.spec.maxCandidateProjects).toBeNull();
    expect(result.spec.coverageMode).toBe("curated");
    expect(result.spec.timeBudget).toBe("fast");
  });

  it("demotes a fixed quota to a recommended soft target without clearing its count", () => {
    const fixed = applyTargetProjectCount(createEmptyIntent("human cohort"), 20);
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Twenty projects is now a recommendation, not a hard quota.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: { quota_flexibility: "recommended" },
        },
      ],
    });

    const result = reduceAgentTurn(fixed, turn);

    expect(result.spec.quotaFlexibility).toBe("recommended");
    expect(result.spec.targetProjectCount).toBe(20);
    expect(buildStrategyCard(result.spec).targetQuota).toMatch(/约 20(?:；|个|$)/);
  });

  it("does not extract an incidental number omitted from the verified tool patch", () => {
    const original = applyTargetProjectCount(createEmptyIntent("marine cohort"), 20);
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Species changed; project count is unchanged.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: { species: ["sea urchin"] },
        },
      ],
    });

    const result = reduceAgentTurn(original, turn, {
      userMessage: "物种改成海胆；你提到的 64 个为什么合适？数量保持原样",
    });

    expect(result.spec.species).toEqual(["sea urchin"]);
    expect(result.spec.targetProjectCount).toBe(20);
    expect(result.spec.quotaFlexibility).toBe("fixed");
  });

  it("does not overwrite already-filled quota fields when only the target count is patched", () => {
    const original = {
      ...createEmptyIntent("deep marine cohort"),
      coverageMode: "exhaustive" as const,
      targetProjectCount: 64,
      maxCandidateProjects: 777,
      quotaFlexibility: "fixed" as const,
      timeBudget: "multi_round" as const,
    };
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Only the requested count changed.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: { patch: { target_project_count: 20 } },
        },
      ],
    });

    const result = reduceAgentTurn(original, turn);
    expect(result.spec).toMatchObject({
      targetProjectCount: 20,
      coverageMode: "exhaustive",
      maxCandidateProjects: 777,
      quotaFlexibility: "fixed",
      timeBudget: "multi_round",
    });
  });

  it("does not persist display defaults when only targetProjectCount is committed", () => {
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Recorded the requested project target.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: { patch: { target_project_count: 25 } },
        },
      ],
    });

    const result = reduceAgentTurn(createEmptyIntent("human cohort"), turn);

    expect(result.spec.targetProjectCount).toBe(25);
    expect(result.spec.coverageMode).toBe("");
    expect(result.spec.maxCandidateProjects).toBeNull();
    expect(result.spec.timeBudget).toBe("");
    expect(result.spec.resolvedFields).toContain("target_project_count");
    expect(result.spec.resolvedFields).not.toEqual(expect.arrayContaining([
      "coverage_mode",
      "max_candidate_projects",
      "time_budget",
    ]));
  });

  it("ignores an update_strategy tool attached to a non-update action", () => {
    const original = createEmptyIntent("marine cohort");
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "clarify",
      assistant_message: "Should sea urchin be preferred or required?",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: { species: ["sea urchin"] },
        },
      ],
    });

    const result = reduceAgentTurn(original, turn);

    expect(result.spec).toBe(original);
    expect(result.strategyUpdated).toBe(false);
  });

  it("does not mutate for a failed response and validates ready_to_confirm locally", () => {
    const incomplete = createEmptyIntent("human cohort");
    const failed = decodeAgentTurnResponse({
      status: "failed",
      action: "update_strategy",
      assistant_message: "",
      tool_calls: [
        { name: "update_strategy", arguments: { species: ["human"] } },
      ],
    });
    expect(reduceAgentTurn(incomplete, failed).spec).toBe(incomplete);

    const premature = decodeAgentTurnResponse({
      status: "completed",
      action: "ready_to_confirm",
      assistant_message: "Ready.",
      tool_calls: [],
      gap_report: { required_missing: [], optional_missing: [], ready_for_confirm: true },
    });
    expect(reduceAgentTurn(incomplete, premature).showConfirmation).toBe(false);
  });
});

describe("D1 semantic confirmation and patch safety", () => {
  it("normalizes a validated strategy patch once at the response boundary", () => {
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Applied.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            patch: {
              species: ["Caenorhabditis elegans"],
              acquisition_mode: "DIA",
              target_project_count: 47,
            },
          },
        },
      ],
    });

    expect(turn.strategy_patch).toEqual({
      species: ["Caenorhabditis elegans"],
      acquisitionMode: "dia",
      targetProjectCount: 47,
    });
  });

  it.each([
    ["invalid enum", { acquisition_mode: "all-ion" }],
    ["array supplied as text", { species: "mouse" }],
    ["array with a non-string member", { exclude_rules: ["contaminants", 3] }],
    ["numeric string", { target_project_count: "20" }],
    ["out-of-range ratio", { legacy_floor_ratio: 1.4 }],
    ["boolean supplied as text", { labeling_hard: "true" }],
    ["unsupported repository", { repository: "somewhere" }],
    ["unsupported field", { sample_material: "tumor biopsy" }],
    ["empty patch", {}],
  ])("rejects %s without claiming a strategy update", (_label, patch) => {
    const original = createEmptyIntent("unchanged");
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Updated.",
      tool_calls: [{ name: "update_strategy", arguments: { patch } }],
    });

    const reduction = reduceAgentTurn(original, turn);
    expect(turn.strategy_patch).toBeNull();
    expect(reduction.spec).toBe(original);
    expect(reduction.strategyUpdated).toBe(false);
  });

  it("accepts an Agent confirmation decision only while awaiting confirmation", () => {
    const strategyFingerprint = "a".repeat(64);
    const ready = {
      ...createEmptyIntent("Reviewed human immunopeptide candidates"),
      taskType: "browse_only" as const,
      runHorizon: "candidates_only" as const,
      coverageMode: "curated" as const,
      targetProjectCount: 20,
    };
    const confirmation = decodeAgentTurnResponse({
      status: "completed",
      action: "confirm_strategy",
      assistant_message: "Confirmed; starting discovery.",
      strategy_fingerprint: strategyFingerprint,
      tool_calls: [
        { name: "confirm_strategy", arguments: { strategy_fingerprint: strategyFingerprint } },
      ],
    });

    expect(confirmation.confirmation_fingerprint).toBe(strategyFingerprint);
    expect(reduceAgentTurn(ready, confirmation, { phase: "grilling" }).confirmationAccepted).toBe(false);
    const accepted = reduceAgentTurn(ready, confirmation, { phase: "awaiting_confirm" });
    expect(accepted.confirmationAccepted).toBe(true);
    expect(accepted.confirmationFingerprint).toBe(strategyFingerprint);
    expect(accepted.spec.confirmed).toBe(true);
  });

  it("preserves a matching confirm_strategy fingerprint at the typed boundary", () => {
    const strategyFingerprint = "b".repeat(64);
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "chat",
      assistant_message: "The user approved the displayed strategy.",
      strategy_fingerprint: strategyFingerprint,
      tool_calls: [
        { name: "confirm_strategy", arguments: { strategy_fingerprint: strategyFingerprint } },
      ],
    });

    expect(turn.action).toBe("confirm_strategy");
    expect(turn.confirmation_requested).toBe(true);
    expect(turn.strategy_fingerprint).toBe(strategyFingerprint);
    expect(turn.confirmation_fingerprint).toBe(strategyFingerprint);
    expect(turn.tool_calls).toEqual([
      { name: "confirm_strategy", arguments: { strategy_fingerprint: strategyFingerprint } },
    ]);
    expect(turn.strategy_patch).toBeNull();
  });

  it.each([
    ["missing", undefined, undefined],
    ["malformed", "not-a-sha256", "not-a-sha256"],
    ["mismatched", "c".repeat(64), "d".repeat(64)],
  ])("fails closed for a %s confirmation fingerprint", (_case, responseFingerprint, toolFingerprint) => {
    const ready = {
      ...createEmptyIntent("Reviewed human candidates"),
      taskType: "browse_only" as const,
      runHorizon: "candidates_only" as const,
      targetProjectCount: 20,
    };
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "confirm_strategy",
      assistant_message: "Confirmed.",
      ...(responseFingerprint ? { strategy_fingerprint: responseFingerprint } : {}),
      tool_calls: [{
        name: "confirm_strategy",
        arguments: toolFingerprint ? { strategy_fingerprint: toolFingerprint } : {},
      }],
    });

    const reduction = reduceAgentTurn(ready, turn, { phase: "awaiting_confirm" });
    expect(turn.confirmation_requested).toBe(true);
    expect(turn.confirmation_fingerprint).toBeNull();
    expect(reduction.confirmationAccepted).toBe(false);
    expect(reduction.spec).toBe(ready);
  });

  it("fails closed when one turn tries to update and confirm at the same time", () => {
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "confirm_strategy",
      assistant_message: "Changed and confirmed.",
      tool_calls: [
        { name: "update_strategy", arguments: { patch: { acquisition_mode: "not-valid" } } },
      ],
    });

    expect(turn.action).toBe("advise");
    expect(turn.strategy_patch).toBeNull();
    expect(turn.confirmation_requested).toBe(false);
  });

  it("lets an Agent defaults update immediately expose confirmation without starting", () => {
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Recommended defaults are now on the card.",
      tool_calls: [
        {
          name: "update_strategy",
          arguments: {
            patch: {
              objective: "Explore human immunopeptidomics projects",
              task_type: "browse_only",
              run_horizon: "candidates_only",
              target_project_count: 20,
            },
          },
        },
      ],
      gap_report: {
        required_missing: [],
        optional_missing: ["acquisition"],
        ready_for_confirm: true,
      },
    });

    const reduction = reduceAgentTurn(createEmptyIntent(), turn, { phase: "grilling" });
    expect(reduction.strategyUpdated).toBe(true);
    expect(reduction.showConfirmation).toBe(true);
    expect(reduction.awaitingConfirmation).toBe(true);
    expect(reduction.confirmationAccepted).toBe(false);
    expect(reduction.spec.confirmed).toBe(false);
  });

  it("treats ready_to_confirm as an offer, never as accepted confirmation", () => {
    const ready = {
      ...createEmptyIntent("Review immunopeptidomics projects"),
      taskType: "denovo" as const,
      runHorizon: "candidates_reviewed" as const,
      targetProjectCount: 20,
      coverageMode: "balanced" as const,
    };
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "ready_to_confirm",
      assistant_message: "The current card is ready for your approval.",
      tool_calls: [],
      gap_report: {
        required_missing: [],
        optional_missing: [],
        ready_for_confirm: true,
      },
    });

    const reduction = reduceAgentTurn(ready, turn, { phase: "awaiting_confirm" });

    expect(reduction.showConfirmation).toBe(true);
    expect(reduction.awaitingConfirmation).toBe(true);
    expect(reduction.confirmationRequested).toBe(false);
    expect(reduction.confirmationAccepted).toBe(false);
    expect(reduction.spec.confirmed).toBe(false);
  });

  const clearCases = [
    ["objective", "objective"],
    ["task_type", "taskType"],
    ["run_horizon", "runHorizon"],
    ["species", "species"],
    ["species_policy", "speciesPolicy"],
    ["species_coverage", "speciesCoverage"],
    ["acquisition_mode", "acquisitionMode"],
    ["mixed_acquisition_policy", "mixedAcquisitionPolicy"],
    ["ptm_types", "ptmTypes"],
    ["special_themes", "specialThemes"],
    ["labeling_strategy", "labelingStrategy"],
    ["labeling_hard", "labelingHard"],
    ["coverage_mode", "coverageMode"],
    ["target_project_count", "targetProjectCount"],
    ["max_candidate_projects", "maxCandidateProjects"],
    ["quota_flexibility", "quotaFlexibility"],
    ["time_budget_preference", "timeBudget"],
    ["on_safety_ceiling", "onSafetyCeiling"],
    ["instrument_preference", "instrumentPreference"],
    ["legacy_floor_ratio", "legacyFloorRatio"],
    ["exclude_rules", "excludeRules"],
    ["success_criteria", "successCriteria"],
    ["scientific_constraints", "scientificConstraints"],
    ["notes", "notes"],
    ["open_risks", "openRisks"],
    ["repository", "repository"],
  ] as const;

  it.each(clearCases)("uses null to clear %s through the generic field contract", (wireKey, specKey) => {
    const populated = {
      ...createEmptyIntent("populated strategy"),
      objective: "Reviewed marine phosphoproteomics cohort",
      taskType: "psm_scoring" as const,
      runHorizon: "candidates_reviewed" as const,
      species: ["sea urchin"],
      speciesPolicy: "include_only" as const,
      speciesCoverage: "broaden" as const,
      acquisitionMode: "dia" as const,
      mixedAcquisitionPolicy: "reject_mixed" as const,
      ptmTypes: ["phospho"],
      specialThemes: ["tumor"],
      labelingStrategy: "tmt" as const,
      labelingHard: true,
      coverageMode: "exhaustive" as const,
      targetProjectCount: 90,
      maxCandidateProjects: 700,
      quotaFlexibility: "fixed" as const,
      timeBudget: "multi_round" as const,
      onSafetyCeiling: "stop" as const,
      instrumentPreference: "newer_with_legacy_floor" as const,
      legacyFloorRatio: 0.3,
      excludeRules: ["cell lines"],
      successCriteria: ["reviewed metadata"],
      scientificConstraints: [
        {
          id: "sample.faims",
          label: "FAIMS only",
          dimension: "separation",
          operator: "equals",
          value: "FAIMS",
          strength: "hard" as const,
          scope: "file" as const,
          evidence_required: true,
          rationale: "",
          source: "user" as const,
        },
      ],
      notes: "keep raw files",
      openRisks: ["sparse annotations"],
      repository: "massive",
    };
    const turn = decodeAgentTurnResponse({
      status: "completed",
      action: "update_strategy",
      assistant_message: "Cleared the requested field.",
      tool_calls: [
        { name: "update_strategy", arguments: { patch: { [wireKey]: null } } },
      ],
    });

    const result = reduceAgentTurn(populated, turn);
    expect(result.strategyUpdated).toBe(true);
    expect(result.spec[specKey]).toEqual(createEmptyIntent()[specKey]);
  });

  it.each([
    ["斑马鱼改成 DIA，并排除交联数据", { species: ["zebrafish"], acquisition_mode: "dia" }],
    ["数量换成 31，之前的覆盖设置保持", { target_project_count: 31 }],
    ["采用上一条建议，但先别执行", { labeling_strategy: "silac" }],
  ])("keeps arbitrary input fail-closed when the Agent turn fails: %s", (userMessage, patch) => {
    const original = {
      ...createEmptyIntent("existing strategy"),
      coverageMode: "balanced" as const,
      targetProjectCount: 50,
    };
    const failedTurn = decodeAgentTurnResponse({
      status: "failed",
      action: "update_strategy",
      assistant_message: "",
      tool_calls: [{ name: "update_strategy", arguments: { patch } }],
    });

    const result = reduceAgentTurn(original, failedTurn, { userMessage });
    expect(result.spec).toBe(original);
    expect(result.strategyUpdated).toBe(false);
    expect(result.confirmationAccepted).toBe(false);
  });

  it("keeps an unavailable Agent fail-closed instead of returning to a mechanical Q1", () => {
    const original = {
      ...createEmptyIntent("existing sea-urchin strategy"),
      species: ["sea urchin"],
      acquisitionMode: "dia" as const,
    };

    const outcome = reduceAgentUnavailable(original, "grilling");
    expect(outcome.spec).toBe(original);
    expect(outcome.phase).toBe("grilling");
    expect(outcome.assistantMessage).toContain("策略卡保持不变");
    expect(outcome.assistantMessage).not.toMatch(/Q1|Q2|问卷|选项 1/);
  });

  it("keeps an expanded Agent decision instead of truncating it to a short menu", () => {
    const turn = decodeAgentTurnResponse({
      action: "clarify",
      assistant_message: "Compare labeling strategies.",
      tool_calls: [],
      next_decision: {
        focus: "labeling_strategy",
        target_fields: ["labeling_strategy"],
        option_mode: "expanded",
        question: "Which labeling strategy should we use?",
        recommendation: {
          id: "label_free",
          label: "Label-free",
          reason: "Least restrictive for exploration",
        },
        options: [
          { id: "label_free", label: "Label-free" },
          { id: "tmt", label: "TMT" },
          { id: "itraq", label: "iTRAQ" },
          { id: "silac", label: "SILAC" },
          { id: "dimethyl", label: "Dimethyl" },
          { id: "any", label: "Keep open" },
        ],
        revisit_existing: false,
      },
    });

    expect(turn.next_decision?.options.map((option) => option.id)).toEqual([
      "label_free", "tmt", "itraq", "silac", "dimethyl", "any",
    ]);
    expect(turn.next_decision?.target_fields).toEqual(["labeling_strategy"]);
    expect(turn.next_decision?.option_mode).toBe("expanded");
  });

  it("records explicit open values as resolved field memory", () => {
    const base = {
      ...createEmptyIntent("open exploration"),
      taskType: "browse_only" as const,
      runHorizon: "candidates_only" as const,
      coverageMode: "curated" as const,
      objective: "Open immunopeptide exploration",
      ptmTypes: ["immunopeptide"],
    };
    const turn = decodeAgentTurnResponse({
      action: "update_strategy",
      assistant_message: "Keep these dimensions open.",
      tool_calls: [{
        name: "update_strategy",
        arguments: { patch: {
          species: [],
          species_policy: "open",
          acquisition_mode: "unknown",
          labeling_strategy: "any",
        } },
      }],
    });

    const result = reduceAgentTurn(base, turn);
    expect(result.spec.resolvedFields).toEqual(expect.arrayContaining([
      "species", "species_policy", "acquisition_mode", "labeling_strategy",
    ]));
    expect(result.gapReport.optional_missing).toEqual([]);
    expect(result.showConfirmation).toBe(true);
  });
});
