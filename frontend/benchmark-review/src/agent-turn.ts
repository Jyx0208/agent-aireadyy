import {
  assessStrategyGaps,
  deriveObjective,
  type StrategyGapReport,
  type StrategySlot,
} from "./grill-tree";
import {
  createEmptyIntent,
  type GrillPhase,
  type IntentSpec,
  type ScientificConstraint,
} from "./intent-spec";

export const AGENT_TURN_ACTIONS = [
  "chat",
  "advise",
  "clarify",
  "update_strategy",
  "ready_to_confirm",
  "confirm_strategy",
  "refuse_search",
] as const;

export type AgentTurnAction = (typeof AGENT_TURN_ACTIONS)[number];

export type StrategyPatchKey =
  | "objective"
  | "taskType"
  | "runHorizon"
  | "species"
  | "speciesPolicy"
  | "speciesCoverage"
  | "acquisitionMode"
  | "mixedAcquisitionPolicy"
  | "ptmTypes"
  | "specialThemes"
  | "labelingStrategy"
  | "labelingHard"
  | "coverageMode"
  | "targetProjectCount"
  | "maxCandidateProjects"
  | "quotaFlexibility"
  | "timeBudget"
  | "onSafetyCeiling"
  | "instrumentPreference"
  | "legacyFloorRatio"
  | "excludeRules"
  | "successCriteria"
  | "scientificConstraints"
  | "notes"
  | "openRisks"
  | "repository";

/** Canonical, validated strategy delta produced only by the response decoder. */
export type AgentStrategyPatch = {
  [Key in StrategyPatchKey]?: IntentSpec[Key] | null;
};

export type AgentToolCall = {
  name: string;
  /** Already decoded and normalized. UI consumers must not parse this again. */
  arguments: Record<string, unknown>;
};

export type AgentDecisionOption = {
  id: string;
  label: string;
  reason?: string;
  /** Canonical delta declared when the option is created, never re-inferred after selection. */
  strategy_patch?: AgentStrategyPatch;
};

export type AgentDecisionRecommendation = AgentDecisionOption & { reason: string };

export type AgentNextDecision = {
  focus: string;
  target_fields: string[];
  option_mode: "focused" | "expanded";
  question: string;
  recommendation: AgentDecisionRecommendation;
  options: AgentDecisionOption[];
  revisit_existing: boolean;
  allow_free_text: true;
};

export type AgentResolvedDecision = {
  focus: string;
  target_fields: string[];
  option_ids: string[];
  selected_option_id: string;
  selected_option_label?: string;
};

export type AgentTurnGapReport = {
  required_missing: StrategySlot[];
  optional_missing: StrategySlot[];
  ready_for_confirm: boolean;
};

export const AGENT_SEMANTIC_VERIFICATION_VERDICTS = [
  "passed",
  "repaired",
  "rejected",
  "unavailable",
  "budget_exhausted",
] as const;

export type AgentSemanticVerificationVerdict =
  (typeof AGENT_SEMANTIC_VERIFICATION_VERDICTS)[number];

export type AgentSemanticVerification = {
  verified: boolean;
  verdict: AgentSemanticVerificationVerdict;
  patch: AgentStrategyPatch | null;
  evidence: Array<{ field: string; source: string; rationale?: string }>;
  rationale: string;
  error?: string;
  errors: string[];
  soft_reject_kept_fields?: string[];
  soft_reject_dropped_fields?: string[];
};

export type AgentDialogueMessage = {
  role: "user" | "assistant";
  content: string;
};

export function appendAgentDialogue(
  history: AgentDialogueMessage[],
  userMessage: string,
  assistantMessage: string,
  limit = 12,
): AgentDialogueMessage[] {
  const next = [...history];
  const user = userMessage.trim().slice(0, 2000);
  const assistant = assistantMessage.trim().slice(0, 2000);
  if (user) next.push({ role: "user", content: user });
  if (assistant) next.push({ role: "assistant", content: assistant });
  return next.slice(-Math.max(2, Math.round(limit)));
}

/** Decoded D1 response plus temporary legacy projections. */
export type AgentTurn = {
  status?: string;
  parser?: string;
  llm_used?: boolean;
  action: AgentTurnAction;
  assistant_message: string;
  tool_calls: AgentToolCall[];
  /** The sole mutation projection consumed by the reducer and renderer. */
  strategy_patch: AgentStrategyPatch | null;
  confirmation_requested: boolean;
  /** Valid only when the confirm tool and response envelope agree on one SHA-256. */
  confirmation_fingerprint: string | null;
  strategy_fingerprint?: string;
  semantic_verification: AgentSemanticVerification | null;
  next_decision: AgentNextDecision | null;
  resolved_decision: AgentResolvedDecision | null;
  decision_memory: AgentResolvedDecision[];
  gap_report: AgentTurnGapReport | null;
  intent?: string;
  advance?: boolean;
  answer_text?: string;
  extra_fields: AgentStrategyPatch;
  understanding?: string;
  pending_question_id?: string;
  next_focus?: string | null;
  ready_for_confirm?: boolean;
};

const record = (value: unknown): Record<string, unknown> | null =>
  value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
const cleanString = (value: unknown) => (typeof value === "string" ? value.trim() : "");
const hasOwn = (value: Record<string, unknown>, key: string) =>
  Object.prototype.hasOwnProperty.call(value, key);

export function isStrategyFingerprint(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

function decodeStrategyFingerprint(value: unknown): string | null {
  const normalized = cleanString(value).toLowerCase();
  return isStrategyFingerprint(normalized) ? normalized : null;
}

function toolArguments(value: unknown): Record<string, unknown> | null {
  const direct = record(value);
  if (direct) return { ...direct };
  if (typeof value !== "string") return null;
  try {
    return record(JSON.parse(value));
  } catch {
    return null;
  }
}

const INVALID = Symbol("invalid-strategy-field");
type Invalid = typeof INVALID;
type FieldDecoder = (value: unknown) => unknown | Invalid;
type StrategyFieldDefinition = {
  key: StrategyPatchKey;
  aliases: readonly string[];
  decode: FieldDecoder;
};

const enumDecoder = <Value extends string>(allowed: readonly Value[]): FieldDecoder =>
  (value) => {
    if (typeof value !== "string") return INVALID;
    const normalized = value.trim().toLowerCase();
    return allowed.includes(normalized as Value) ? normalized : INVALID;
  };

const textDecoder = (maxLength: number): FieldDecoder => (value) => {
  if (typeof value !== "string") return INVALID;
  const normalized = value.trim();
  return normalized ? normalized.slice(0, maxLength) : INVALID;
};

const stringListDecoder: FieldDecoder = (value) => {
  if (!Array.isArray(value) || value.length > 100) return INVALID;
  const normalized: string[] = [];
  for (const item of value) {
    if (typeof item !== "string") return INVALID;
    const text = item.trim();
    if (!text || text.length > 240) return INVALID;
    if (!normalized.includes(text)) normalized.push(text);
  }
  return normalized;
};

const booleanDecoder: FieldDecoder = (value) =>
  typeof value === "boolean" ? value : INVALID;

const positiveInteger = (maximum: number): FieldDecoder => (value) =>
  typeof value === "number"
  && Number.isInteger(value)
  && value > 0
  && value <= maximum
    ? value
    : INVALID;

const ratioDecoder: FieldDecoder = (value) =>
  typeof value === "number"
  && Number.isFinite(value)
  && value >= 0
  && value <= 1
    ? value
    : INVALID;

const scientificConstraintsDecoder: FieldDecoder = (value) => {
  if (!Array.isArray(value) || value.length > 100) return INVALID;
  const result: ScientificConstraint[] = [];
  const positions = new Map<string, number>();
  for (const item of value) {
    const raw = record(item);
    if (!raw) return INVALID;
    const id = cleanString(raw.id);
    const label = cleanString(raw.label);
    const dimension = cleanString(raw.dimension);
    const operator = cleanString(raw.operator) || "matches";
    const strength = cleanString(raw.strength).toLowerCase();
    const scope = cleanString(raw.scope).toLowerCase() || "project";
    const source = cleanString(raw.source).toLowerCase() || "user";
    if (
      !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$/.test(id)
      || !label || label.length > 240
      || !dimension || dimension.length > 120
      || operator.length > 64
      || !["hard", "soft"].includes(strength)
      || !["project", "file", "sample", "portfolio"].includes(scope)
      || !["user", "accepted_recommendation", "inferred"].includes(source)
    ) return INVALID;
    try {
      if ((JSON.stringify(raw.value ?? null) || "").length > 4000) return INVALID;
    } catch {
      return INVALID;
    }
    const constraint: ScientificConstraint = {
      id,
      label,
      dimension,
      operator,
      value: (raw.value ?? null) as ScientificConstraint["value"],
      strength: strength as ScientificConstraint["strength"],
      scope: scope as ScientificConstraint["scope"],
      evidence_required: raw.evidence_required !== false,
      ...(cleanString(raw.rationale)
        ? { rationale: cleanString(raw.rationale).slice(0, 500) }
        : {}),
      source: source as ScientificConstraint["source"],
    };
    const key = id.toLowerCase();
    const previous = positions.get(key);
    if (previous == null) {
      positions.set(key, result.length);
      result.push(constraint);
    } else {
      result[previous] = constraint;
    }
  }
  return result;
};

const STRATEGY_FIELDS: readonly StrategyFieldDefinition[] = [
  { key: "objective", aliases: ["objective", "goal_summary", "goalSummary"], decode: textDecoder(120) },
  { key: "taskType", aliases: ["task_type", "taskType"], decode: enumDecoder([
    "rt_prediction", "fragment_intensity_prediction", "psm_scoring", "denovo", "ptm_denovo",
    "chimeric_interpretation", "browse_only", "other",
  ] as const) },
  { key: "runHorizon", aliases: ["run_horizon", "runHorizon"], decode: enumDecoder([
    "plan_only", "candidates_only", "candidates_reviewed", "ai_ready_table", "pre_release", "full_release",
  ] as const) },
  { key: "species", aliases: ["species"], decode: stringListDecoder },
  { key: "speciesPolicy", aliases: ["species_policy", "speciesPolicy"], decode: enumDecoder([
    "open", "include_only", "prefer", "exclude",
  ] as const) },
  { key: "speciesCoverage", aliases: ["species_coverage", "speciesCoverage"], decode: enumDecoder([
    "none", "prefer_listed", "broaden",
  ] as const) },
  { key: "acquisitionMode", aliases: ["acquisition_mode", "acquisitionMode"], decode: enumDecoder([
    "dda", "dia", "unknown",
  ] as const) },
  { key: "mixedAcquisitionPolicy", aliases: ["mixed_acquisition_policy", "mixedAcquisitionPolicy"], decode: enumDecoder([
    "reject_mixed", "review_mixed", "allow",
  ] as const) },
  { key: "ptmTypes", aliases: ["ptm_types", "ptmTypes"], decode: stringListDecoder },
  { key: "specialThemes", aliases: ["special_themes", "specialThemes", "themes", "theme"], decode: stringListDecoder },
  { key: "labelingStrategy", aliases: ["labeling_strategy", "labelingStrategy"], decode: enumDecoder([
    "label_free", "tmt", "itraq", "silac", "dimethyl", "unknown", "any",
  ] as const) },
  { key: "labelingHard", aliases: ["labeling_hard", "labelingHard"], decode: booleanDecoder },
  { key: "coverageMode", aliases: ["coverage_mode", "coverageMode", "scale_mode", "scaleMode"], decode: enumDecoder([
    "curated", "balanced", "exhaustive",
  ] as const) },
  { key: "targetProjectCount", aliases: ["target_project_count", "targetProjectCount", "max_projects", "maxProjects"], decode: positiveInteger(300) },
  { key: "maxCandidateProjects", aliases: ["max_candidate_projects", "maxCandidateProjects"], decode: positiveInteger(1000) },
  { key: "quotaFlexibility", aliases: ["quota_flexibility", "quotaFlexibility"], decode: enumDecoder([
    "fixed", "recommended", "open_ended",
  ] as const) },
  { key: "timeBudget", aliases: ["time_budget", "timeBudget", "time_budget_preference", "timeBudgetPreference"], decode: enumDecoder([
    "fast", "multi_round",
  ] as const) },
  { key: "onSafetyCeiling", aliases: ["on_safety_ceiling", "onSafetyCeiling"], decode: enumDecoder([
    "ask", "auto_continue_within_safety", "stop",
  ] as const) },
  { key: "instrumentPreference", aliases: ["instrument_preference", "instrumentPreference"], decode: enumDecoder([
    "none", "newer", "classic", "newer_with_legacy_floor",
  ] as const) },
  { key: "legacyFloorRatio", aliases: ["legacy_floor_ratio", "legacyFloorRatio"], decode: ratioDecoder },
  { key: "excludeRules", aliases: ["exclude_rules", "excludeRules"], decode: stringListDecoder },
  { key: "successCriteria", aliases: ["success_criteria", "successCriteria"], decode: stringListDecoder },
  { key: "scientificConstraints", aliases: ["scientific_constraints", "scientificConstraints"], decode: scientificConstraintsDecoder },
  { key: "notes", aliases: ["notes"], decode: textDecoder(4000) },
  { key: "openRisks", aliases: ["open_risks", "openRisks"], decode: stringListDecoder },
  { key: "repository", aliases: ["repository"], decode: enumDecoder(["pride", "massive", "iprox", "auto"] as const) },
] as const;

function valuesEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

/** Validate and canonicalize a raw tool patch. Unknown or malformed fields are ignored. */
function decodeStrategyPatch(value: unknown): AgentStrategyPatch | null {
  const raw = record(value);
  if (!raw) return null;
  const knownAliases = new Set(STRATEGY_FIELDS.flatMap((field) => [...field.aliases]));
  if (Object.keys(raw).some((key) => !knownAliases.has(key))) return null;
  const patch: AgentStrategyPatch = {};
  const writable = patch as Record<string, unknown>;

  for (const field of STRATEGY_FIELDS) {
    const presentAliases = field.aliases.filter((alias) => hasOwn(raw, alias));
    if (presentAliases.length === 0) continue;
    const decoded = presentAliases.map((alias) => {
      const candidate = raw[alias];
      return candidate === null ? null : field.decode(candidate);
    });
    if (decoded.some((candidate) => candidate === INVALID)) return null;
    if (decoded.slice(1).some((candidate) => !valuesEqual(candidate, decoded[0]))) return null;
    writable[field.key] = decoded[0];
  }

  if (hasOwn(writable, "runHorizon")) {
    writable.runHorizon = "candidates_reviewed";
  }
  return Object.keys(patch).length > 0 ? patch : null;
}

function decodeToolCalls(value: unknown): AgentToolCall[] {
  if (!Array.isArray(value)) return [];
  const calls: AgentToolCall[] = [];
  for (const item of value) {
    const raw = record(item);
    if (!raw) continue;
    const name = cleanString(raw.name ?? raw.tool);
    if (!name) continue;

    if (name === "update_strategy") {
      const args = toolArguments(raw.arguments ?? raw.args ?? raw.input);
      if (!args) continue;
      const nested = hasOwn(args, "patch") ? record(args.patch) : null;
      const patch = decodeStrategyPatch(nested ?? args);
      if (patch) calls.push({ name, arguments: patch as Record<string, unknown> });
      continue;
    }

    if (name === "confirm_strategy") {
      const args = toolArguments(raw.arguments ?? raw.args ?? raw.input);
      const fingerprint = decodeStrategyFingerprint(args?.strategy_fingerprint);
      calls.push({
        name,
        arguments: fingerprint ? { strategy_fingerprint: fingerprint } : {},
      });
      continue;
    }

    const args = toolArguments(raw.arguments ?? raw.args ?? raw.input);
    if (args) calls.push({ name, arguments: args });
  }
  return calls;
}

function decodeOption(value: unknown): AgentDecisionOption | null {
  const raw = record(value);
  if (!raw) return null;
  const label = cleanString(raw.label);
  if (!label) return null;
  const hasStrategyPatch = hasOwn(raw, "strategy_patch");
  const strategyPatch = hasStrategyPatch ? decodeStrategyPatch(raw.strategy_patch) : null;
  // A declared option patch is executable meaning, not decorative metadata.
  // If it is malformed, reject the option instead of silently turning a
  // committed numeric choice into an ungrounded natural-language re-parse.
  if (hasStrategyPatch && !strategyPatch) return null;
  return {
    id: cleanString(raw.id) || label,
    label,
    ...(cleanString(raw.reason) ? { reason: cleanString(raw.reason) } : {}),
    ...(strategyPatch ? { strategy_patch: strategyPatch } : {}),
  };
}

function decodeNextDecision(value: unknown): AgentNextDecision | null {
  const raw = record(value);
  if (!raw) return null;
  const question = cleanString(raw.question);
  const recommendation = decodeOption(raw.recommendation);
  const options = Array.isArray(raw.options)
    ? raw.options.map(decodeOption).filter((item): item is AgentDecisionOption => item != null).slice(0, 8)
    : [];
  if (!question || !recommendation?.reason || options.length < 2) return null;
  const focus = cleanString(raw.focus);
  const targetFields = Array.isArray(raw.target_fields)
    ? Array.from(new Set(raw.target_fields.map(cleanString).filter(Boolean))).slice(0, 8)
    : [];
  if (
    ["horizon", "run_horizon", "delivery_horizon"].includes(focus.toLowerCase())
    || targetFields.includes("run_horizon")
  ) {
    return null;
  }
  return {
    focus,
    target_fields: targetFields,
    option_mode: cleanString(raw.option_mode) === "expanded" ? "expanded" : "focused",
    question,
    recommendation: { ...recommendation, reason: recommendation.reason },
    options,
    revisit_existing: raw.revisit_existing === true,
    // Free text is always accepted, regardless of model output.
    allow_free_text: true,
  };
}

function decodeResolvedDecision(value: unknown): AgentResolvedDecision | null {
  const raw = record(value);
  if (!raw || !Array.isArray(raw.option_ids)) return null;
  const optionIds = Array.from(new Set(raw.option_ids.map(cleanString).filter(Boolean))).slice(0, 8);
  if (optionIds.length < 2) return null;
  const focus = cleanString(raw.focus);
  const targetFields = Array.isArray(raw.target_fields)
    ? Array.from(new Set(raw.target_fields.map(cleanString).filter(Boolean))).slice(0, 8)
    : [];
  if (
    ["horizon", "run_horizon", "delivery_horizon"].includes(focus.toLowerCase())
    || targetFields.includes("run_horizon")
  ) {
    return null;
  }
  return {
    focus,
    target_fields: targetFields,
    option_ids: optionIds,
    selected_option_id: cleanString(raw.selected_option_id),
    ...(cleanString(raw.selected_option_label)
      ? { selected_option_label: cleanString(raw.selected_option_label) }
      : {}),
  };
}

function decodeDecisionMemory(value: unknown): AgentResolvedDecision[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(decodeResolvedDecision)
    .filter((item): item is AgentResolvedDecision => item != null)
    .slice(-50);
}

const STRATEGY_SLOTS = new Set<StrategySlot>([
  "task", "horizon", "species", "acquisition", "coverage", "theme", "labeling", "objective", "instrument",
]);

function slotList(value: unknown): StrategySlot[] {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(
    value
      .map(cleanString)
      .filter((slot): slot is StrategySlot => STRATEGY_SLOTS.has(slot as StrategySlot)),
  ));
}

function decodeGapReport(value: unknown): AgentTurnGapReport | null {
  const raw = record(value);
  if (!raw) return null;
  const fixedSlots = new Set(["horizon", "run_horizon", "delivery_horizon"]);
  return {
    required_missing: slotList(raw.required_missing ?? raw.requiredMissing)
      .filter((slot) => !fixedSlots.has(slot)),
    optional_missing: slotList(raw.optional_missing ?? raw.optionalMissing)
      .filter((slot) => !fixedSlots.has(slot)),
    ready_for_confirm: raw.ready_for_confirm === true || raw.readyForConfirm === true,
  };
}

const SEMANTIC_VERDICT_ALIASES: Record<string, AgentSemanticVerificationVerdict> = {
  accept: "passed",
  pass: "passed",
  passed: "passed",
  repair: "repaired",
  repaired: "repaired",
  reject: "rejected",
  rejected: "rejected",
  unavailable: "unavailable",
  budget_exhausted: "budget_exhausted",
};

function decodeSemanticVerification(value: unknown): AgentSemanticVerification | null {
  const raw = record(value);
  if (!raw) return null;
  let verdict = SEMANTIC_VERDICT_ALIASES[cleanString(raw.verdict).toLowerCase()];
  if (!verdict) return null;
  if (raw.verified === false && (verdict === "passed" || verdict === "repaired")) {
    verdict = "rejected";
  }
  const successful = verdict === "passed" || verdict === "repaired";
  const evidence = Array.isArray(raw.evidence)
    ? raw.evidence.flatMap((item) => {
        const candidate = record(item);
        const field = cleanString(candidate?.field).slice(0, 120);
        const source = cleanString(candidate?.source).slice(0, 500);
        if (!field || !source) return [];
        const rationale = cleanString(candidate?.rationale).slice(0, 500);
        return [{ field, source, ...(rationale ? { rationale } : {}) }];
      }).slice(0, 100)
    : [];
  const errors = Array.isArray(raw.errors)
    ? raw.errors.map(cleanString).filter(Boolean).map((item) => item.slice(0, 500)).slice(0, 50)
    : [];
  const error = cleanString(raw.error).slice(0, 500);
  const softKept = Array.isArray(raw.soft_reject_kept_fields)
    ? raw.soft_reject_kept_fields.map(cleanString).filter(Boolean).slice(0, 50)
    : [];
  const softDropped = Array.isArray(raw.soft_reject_dropped_fields)
    ? raw.soft_reject_dropped_fields.map(cleanString).filter(Boolean).slice(0, 50)
    : [];
  return {
    verified: successful && raw.verified === true,
    verdict,
    patch: decodeStrategyPatch(raw.patch),
    evidence,
    rationale: cleanString(raw.rationale).slice(0, 1200),
    ...(error ? { error } : {}),
    errors,
    ...(softKept.length ? { soft_reject_kept_fields: softKept } : {}),
    ...(softDropped.length ? { soft_reject_dropped_fields: softDropped } : {}),
  };
}

function legacyAction(intent: string, hasPatch: boolean, ready: boolean): AgentTurnAction {
  if (ready) return "ready_to_confirm";
  if (hasPatch || ["revise", "multi_fill", "answer_question"].includes(intent)) return "update_strategy";
  if (intent === "explain") return "advise";
  if (intent === "clarify") return "clarify";
  if (intent === "refuse_search") return "refuse_search";
  // Legacy request_confirm meant “offer the card”, never “the user confirmed”.
  if (intent === "request_confirm") return "ready_to_confirm";
  return "chat";
}

function mergeUpdatePatches(calls: AgentToolCall[]): AgentStrategyPatch | null {
  const merged: AgentStrategyPatch = {};
  const writable = merged as Record<string, unknown>;
  for (const call of calls) {
    if (call.name === "update_strategy") Object.assign(writable, call.arguments);
  }
  return Object.keys(merged).length > 0 ? merged : null;
}

/** The only decoder for unknown `/grill-turn` response data. */
export function decodeAgentTurnResponse(value: unknown): AgentTurn {
  const raw = record(value);
  if (!raw) throw new TypeError("Invalid agent-turn response");

  const primary = ["action", "tool_calls", "next_decision", "gap_report"].some((key) => hasOwn(raw, key));
  let toolCalls = decodeToolCalls(raw.tool_calls);
  const intent = cleanString(raw.intent);
  if (!primary) {
    const legacyPatch = decodeStrategyPatch(raw.extra_fields);
    if (legacyPatch) {
      toolCalls = [{ name: "update_strategy", arguments: legacyPatch as Record<string, unknown> }];
    }
  }

  const updatePatch = mergeUpdatePatches(toolCalls);
  const semanticVerification = decodeSemanticVerification(raw.semantic_verification);
  const rawHasUpdateTool = Array.isArray(raw.tool_calls) && raw.tool_calls.some((item) => {
    const candidate = record(item);
    return cleanString(candidate?.name ?? candidate?.tool) === "update_strategy";
  });
  const hasConfirmTool = toolCalls.some((call) => call.name === "confirm_strategy");
  const strategyFingerprint = decodeStrategyFingerprint(raw.strategy_fingerprint);
  const rawAction = cleanString(raw.action);
  let action = AGENT_TURN_ACTIONS.includes(rawAction as AgentTurnAction)
    ? (rawAction as AgentTurnAction)
    : legacyAction(intent, updatePatch != null, raw.ready_for_confirm === true);

  // A turn may update or confirm, never both. Ambiguity is fail-closed.
  const ambiguousTransition = (updatePatch != null || rawHasUpdateTool)
    && (action === "confirm_strategy" || hasConfirmTool);
  if (ambiguousTransition) {
    action = "advise";
    toolCalls = toolCalls.filter((call) => !["update_strategy", "confirm_strategy"].includes(call.name));
  } else if (
    semanticVerification?.verdict === "rejected"
    && (updatePatch != null || rawHasUpdateTool)
  ) {
    // Soft-reject v2: only an explicit soft_reject_kept_fields list authorizes
    // a partial write. The verifier's `patch` alone is the rejected proposal,
    // not a keep set — bare reject must fail closed.
    const softKeepFields = semanticVerification.soft_reject_kept_fields ?? [];
    const softKeepPatch = semanticVerification.patch;
    const hasSoftKeep = softKeepFields.length > 0;
    if (hasSoftKeep) {
      action = "update_strategy";
      if (softKeepPatch != null && Object.keys(softKeepPatch).length > 0) {
        const kept: Record<string, unknown> = {};
        const source = softKeepPatch as Record<string, unknown>;
        for (const field of softKeepFields) {
          if (Object.prototype.hasOwnProperty.call(source, field)) {
            kept[field] = source[field];
          }
        }
        toolCalls = Object.keys(kept).length
          ? [{ name: "update_strategy", arguments: kept }]
          : toolCalls.filter((call) => call.name !== "update_strategy");
        if (!Object.keys(kept).length) action = "advise";
      }
    } else {
      // Keep action=update_strategy so write-attempt SV chrome can still surface
      // the rejection; strip the unauthorized tool patch (fail closed).
      action = "update_strategy";
      toolCalls = toolCalls.filter((call) => call.name !== "update_strategy");
    }
  } else if (hasConfirmTool) {
    action = "confirm_strategy";
  } else if (action === "update_strategy" && updatePatch == null) {
    action = "advise";
  }

  const strategyPatch = action === "update_strategy" ? mergeUpdatePatches(toolCalls) : null;
  const confirmationRequested = action === "confirm_strategy";
  const confirmCalls = toolCalls.filter((call) => call.name === "confirm_strategy");
  const toolFingerprint = confirmCalls.length === 1
    ? decodeStrategyFingerprint(confirmCalls[0].arguments.strategy_fingerprint)
    : null;
  const confirmationFingerprint = confirmationRequested
    && strategyFingerprint != null
    && toolFingerprint === strategyFingerprint
      ? strategyFingerprint
      : null;

  return {
    status: cleanString(raw.status) || undefined,
    parser: cleanString(raw.parser) || undefined,
    llm_used: typeof raw.llm_used === "boolean" ? raw.llm_used : undefined,
    action,
    assistant_message: cleanString(raw.assistant_message ?? raw.reply),
    tool_calls: toolCalls,
    strategy_patch: strategyPatch,
    confirmation_requested: confirmationRequested,
    confirmation_fingerprint: confirmationFingerprint,
    strategy_fingerprint: strategyFingerprint || undefined,
    semantic_verification: semanticVerification,
    next_decision: decodeNextDecision(raw.next_decision),
    resolved_decision: decodeResolvedDecision(raw.resolved_decision),
    decision_memory: decodeDecisionMemory(raw.decision_memory),
    gap_report: decodeGapReport(raw.gap_report),
    intent: intent || undefined,
    advance: typeof raw.advance === "boolean" ? raw.advance : undefined,
    answer_text: cleanString(raw.answer_text) || undefined,
    extra_fields: strategyPatch ? { ...strategyPatch } : {},
    understanding: cleanString(raw.understanding) || undefined,
    pending_question_id: cleanString(raw.pending_question_id) || undefined,
    next_focus: cleanString(raw.next_focus) || null,
    ready_for_confirm: raw.ready_for_confirm === true,
  };
}

export function formatAgentNextDecision(decision: AgentNextDecision | null): string {
  if (!decision) return "";
  const lines = [
    decision.question,
    `建议：${decision.recommendation.label}（${decision.recommendation.reason}）`,
    ...decision.options.map(
      (option, index) => `${index + 1}. ${option.label}${option.reason ? `（${option.reason}）` : ""}`,
    ),
    "直接回复选项，或用自然语言说你的判断。",
  ];
  return lines.join("\n");
}

const EMPTY_INTENT = createEmptyIntent();

function cloneIntent(spec: IntentSpec): IntentSpec {
  return {
    ...spec,
    species: [...spec.species],
    ptmTypes: [...spec.ptmTypes],
    specialThemes: [...spec.specialThemes],
    selectedSearchTerms: [...(spec.selectedSearchTerms || [])],
    excludeRules: [...spec.excludeRules],
    successCriteria: [...spec.successCriteria],
    scientificConstraints: spec.scientificConstraints.map((item) => ({ ...item })),
    openRisks: [...spec.openRisks],
    resolvedFields: [...(spec.resolvedFields || [])],
    answered: { ...spec.answered },
    inferred: { ...spec.inferred },
    parseWarnings: [...spec.parseWarnings],
  };
}

function cloneStrategyValue(value: unknown): unknown {
  return Array.isArray(value)
    ? value.map((item) => (item && typeof item === "object" ? { ...item } : item))
    : value;
}

function hasPatchField(patch: AgentStrategyPatch, key: StrategyPatchKey): boolean {
  return Object.prototype.hasOwnProperty.call(patch, key);
}

const OBJECTIVE_SOURCE_FIELDS: readonly StrategyPatchKey[] = [
  "taskType",
  "runHorizon",
  "species",
  "ptmTypes",
  "specialThemes",
  "acquisitionMode",
];

function assignStrategyField(
  target: IntentSpec,
  key: StrategyPatchKey,
  value: AgentStrategyPatch[StrategyPatchKey],
): void {
  const resolved = value === null ? EMPTY_INTENT[key] : value;
  Object.assign(target, { [key]: cloneStrategyValue(resolved) });
}

function applyNormalizedPatch(spec: IntentSpec, patch: AgentStrategyPatch): IntentSpec {
  const next = cloneIntent(spec);

  for (const field of STRATEGY_FIELDS) {
    if (hasPatchField(patch, field.key)) {
      assignStrategyField(next, field.key, patch[field.key]);
    }
  }
  const resolved = new Set(next.resolvedFields || []);
  for (const field of STRATEGY_FIELDS) {
    if (hasPatchField(patch, field.key)) resolved.add(field.aliases[0]);
  }
  next.resolvedFields = [...resolved];

  // Objective is a human-readable synthesis, not another questionnaire field.
  // When concrete domain fields were committed but the redundant objective
  // string was omitted, derive it from the validated strategy instead of
  // asking the user to restate the same scientific goal.
  const objectiveSourceChanged = OBJECTIVE_SOURCE_FIELDS.some((key) => hasPatchField(patch, key));
  if (!hasPatchField(patch, "objective") && (objectiveSourceChanged || !next.objective)) {
    // Structured strategy fields are the source of truth.  Do not feed the
    // previous prose projection back into its own rebuild: it may still name
    // a replaced species, acquisition mode, task, or theme.
    const objectiveSource = objectiveSourceChanged
      ? { ...next, objective: "", originalPrompt: "" }
      : next;
    const derivedObjective = deriveObjective(objectiveSource);
    if (derivedObjective && derivedObjective !== "蛋白质组数据发现") {
      next.objective = derivedObjective;
    } else if (objectiveSourceChanged) {
      next.objective = "蛋白质组数据发现";
    }
  }

  if (patch.quotaFlexibility === "open_ended") {
    if (!hasPatchField(patch, "targetProjectCount")) next.targetProjectCount = null;
    if (!hasPatchField(patch, "maxCandidateProjects")) next.maxCandidateProjects = null;
  }

  if (valuesEqual(next, spec)) return spec;
  next.confirmed = false;
  return next;
}

export type AgentTurnReduction = {
  spec: IntentSpec;
  strategyUpdated: boolean;
  gapReport: StrategyGapReport;
  showConfirmation: boolean;
  awaitingConfirmation: boolean;
  confirmationRequested: boolean;
  confirmationAccepted: boolean;
  confirmationFingerprint: string | null;
};

export type AgentUnavailableReduction = {
  spec: IntentSpec;
  phase: GrillPhase;
  assistantMessage: string;
};

/** Transport/model failure policy: preserve state and never invoke the legacy questionnaire. */
export function reduceAgentUnavailable(
  spec: IntentSpec,
  phase: GrillPhase,
): AgentUnavailableReduction {
  return {
    spec,
    phase: phase === "done" || phase === "failed" ? "idle" : phase,
    assistantMessage:
      "Agent 本轮没有返回可验证的决策；策略卡保持不变，也没有启动搜索。请稍后重试，或换一种自然语言继续说明。",
  };
}

/** Apply only decoder-validated tool events; user prose is never parsed here. */
export function reduceAgentTurn(
  spec: IntentSpec,
  turn: AgentTurn,
  options: { phase?: GrillPhase; userMessage?: string } = {},
): AgentTurnReduction {
  const failed = ["failed", "error", "cancelled"].includes(cleanString(turn.status).toLowerCase());
  const strategyPatchRejected = turn.strategy_patch != null
    && turn.semantic_verification?.verdict === "rejected";
  let next = spec;
  if (!failed && !strategyPatchRejected && turn.action === "update_strategy" && turn.strategy_patch) {
    next = applyNormalizedPatch(spec, turn.strategy_patch);
  }
  const strategyUpdated = next !== spec;
  const gapReport = assessStrategyGaps(next);
  const phase = options.phase ?? "grilling";
  const confirmationRequested = !failed
    && turn.action === "confirm_strategy"
    && turn.confirmation_requested;
  const confirmationFingerprint = confirmationRequested ? turn.confirmation_fingerprint : null;
  const confirmationAccepted = confirmationRequested
    && confirmationFingerprint != null
    && !strategyUpdated
    && phase === "awaiting_confirm"
    && gapReport.ready_for_confirm;

  if (confirmationAccepted && !next.confirmed) {
    next = { ...next, confirmed: true };
  }

  const confirmationOffer = !failed
    && !strategyPatchRejected
    && gapReport.ready_for_confirm
    && (
      turn.action === "ready_to_confirm"
      || turn.action === "update_strategy"
      || (confirmationRequested && !confirmationAccepted)
    );
  const awaitingConfirmation = !confirmationAccepted
    && gapReport.ready_for_confirm
    && (phase === "awaiting_confirm" || confirmationOffer);

  return {
    spec: next,
    strategyUpdated,
    gapReport,
    showConfirmation: confirmationOffer && !confirmationAccepted,
    awaitingConfirmation,
    confirmationRequested,
    confirmationAccepted,
    confirmationFingerprint,
  };
}
