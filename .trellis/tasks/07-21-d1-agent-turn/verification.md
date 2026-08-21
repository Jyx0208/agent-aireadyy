# D1 autonomous dialogue and quality-first Discovery verification (2026-07-22)

## Final automated gates

- Backend Discovery / Control Plane / PRIDE scope: **579 passed**.
- Frontend: **9 test files, 179 passed**.
- Frontend TypeScript and Vite production build: passed.
- Python `compileall` for Discovery, Control Plane, metadata, and web modules: passed.
- `git diff --check`: passed; only existing Windows LF/CRLF notices were emitted.
- The Carbon bundle still emits the known large-chunk performance warning; it is not a correctness failure.
- Targeted regressions cover exact-accession retention, one-step judgment repair feedback,
  weak-keep disclosure, SDRF file-level evidence, blocked-result counts, dynamic strategy fields,
  numeric option memory, confirmation fingerprints, and Codex-style progress rendering.

## Real-model browser matrix

Validated against the local Carbon application with the configured DeepSeek model:

1. Greeting: conversational response, no strategy mutation, no forced menu.
2. Explicit consultation-only request ("只聊、不改策略、不搜索"): the Agent returned five
   task-specific immunopeptidomics research directions, did not search PRIDE, and left the strategy
   card at "还没有形成可执行搜索目标" with confirmation disabled.
3. One compound commitment (fish immunopeptidomics, DIA-only, 15 projects,
   >=30 participants/project, exclude immortalized cell lines, newer instruments, review candidates):
   the Agent path preserved every requirement and stored the participant rule as a structured hard
   constraint.
4. Numeric replies resolve only the active model-generated options, update once, and do not repeat
   the same question.
5. Technical traces are collapsed by default. The right side contains only the compact strategy / run
   entry; full strategy, downloads, judgments, and audit reasons live in dialogs.

## Real PRIDE / model probe

Target: exact project `PXD055544`, human immunopeptidomics, one reviewed project, file-level SDRF
assay evidence required.

### Fault exposed and repaired

- A mixed exact-accession + broad-query run reproduced a real defect: PRIDE returned
  `PXD055544`, but global Top-N retention dropped it from the persisted candidate pool.
- The Agent could see the accession in query yield but could not inspect it, expanded searches,
  and eventually exhausted the SDK run.
- A deterministic regression reproduced the defect in seconds.
- Exact `PXD\d+` hits are now pinned within the bounded pool and survive state reload; broad ranking
  cannot silently evict an explicitly requested accession.

### Final successful probe

- Job / record status: `completed` / `completed`.
- Candidate projects: 1; selected projects: 1; selected files: 66.
- Final audit: `ready`; final manifest exists; no audit errors.
- All 66 selected acquisition files:
  - belong to `PXD055544`;
  - have a concrete download URL, positive size, and raw-acquisition role;
  - have `sdrf_match_status=matched`;
  - carry matched-row evidence `sdrf:assay name = Immunopeptidomics_class_I`;
  - have no unresolved review flag.
- SDRF source is public and hashed; the 66 count means 66 raw files matched to SDRF rows, not 66
  separate SDRF documents.
- The repair-feedback changes reduced the representative run from 15 SDK turns / 207,783 cumulative
  input tokens to 10 turns / 116,842 cumulative input tokens while preserving the same selected result.

## Quality disclosure and audit boundary

- `validity_status=weak_keep` remains delivery eligible only when concrete file evidence is present and
  `needs_review=false`; it is not relabeled as strict valid.
- Final audits now report `strict_valid_files` and `weak_keep_files` separately and add a visible warning
  whenever delivery relies on weak-keep files.
- Completed-result dialogs show quality warnings as well as downloads and project scoring reasons.
- Blocked runs remain blocked, show candidate / delivered / review counts separately, and cannot enter
  downstream workbenches.
- Search-stage provisional judgments omit inspection-only constraint assessments. Rejected inspection
  judgments receive bounded persisted evidence and exact repair instructions instead of a generic retry.

## Remaining non-blocking boundary

- The production Carbon bundle is large and should later be code-split for load performance.
- Scientific semantic coverage is still a lightweight retrieval diagnostic, not a substitute for hard
  constraints or project judgments. Generic workflow words and exact accessions are now removed from
  the metric so it is no longer polluted by terms such as `and`, `the`, or `PXD055544`.
- No API key was read from source, copied into artifacts, or written to the repository.

## Explicit open/default decision regression (2026-07-22)

- Reproduced the production loop where an explicit answer such as `都可以` or a numeric
  selection of `都看看，不限制` submitted `species=[]`, `species_policy=open`, or
  `scientific_constraints=[]`, but unchanged-value filtering erased the event before
  `resolved_fields` and dialogue memory could advance.
- The backend now distinguishes ordinary value echoes from **resolution deltas**. A resolution
  delta requires an SDK `update_strategy` event plus either a server-resolved active option or a
  separately source-grounded free-text commitment. No species/MHC vocabulary branch was added.
- Pure resolution deltas no longer invoke the semantic verifier, because they change decision
  state but no search value. Mixed changed/unchanged target fields still pass through semantic
  verification, with the active option context available to the verifier.
- Regression matrix covers open species, empty scientific constraints, mixed changed/default
  fields, free-text open answers, same-turn repeated-question suppression, and next-turn memory.
- Verification after the repair: 147 related backend tests passed; 82 frontend Agent-turn tests
  passed; Python compileall and `git diff --check` passed.
- Real DeepSeek probes passed for both numeric options and natural-language `都可以`: the response
  emitted the explicit open-value patch, recorded the decision, and advanced to `run_horizon`
  instead of alternating between species and MHC questions.

## Active-option verifier authority regression (2026-07-22)

- Reproduced the browser failure where choosing option `1` correctly produced a scoped
  `update_strategy` call, but the separate semantic-verifier Agent re-read the bare string `1`
  without the active decision semantics and rejected the task/species patch.
- Fixed the authority boundary generically: when the server resolves an explicit option and the
  SDK tool patch is non-empty and wholly contained in that decision's canonical `target_fields`,
  schema/action/scope validation is final and the stateless semantic verifier is not invoked.
- Any valid but out-of-scope field still takes the verifier/rejection path; a regression proves a
  task option cannot silently change project quota.
- Real DeepSeek replays of the captured task option and species option now produce scoped patches,
  `resolved_decision` memory, no semantic-verifier error, and no repeated question.
- Verification after this repair: 149 related backend tests and 82 frontend Agent-turn tests passed;
  compileall, diff check, and the live 8013 health probe passed.
- Official Agents SDK orchestration guidance confirms that nested/tool agents do not inherit parent
  state automatically. The planned architecture therefore keeps one user-facing manager, uses
  specialists as bounded tools with structured inputs, and reserves deterministic code gates for
  mutation/confirmation authority.

## Compound goal plus consultation regression (2026-07-22)

- Reproduced a real DeepSeek failure for `我想做免疫肽 de novo 训练集，你先帮我分析下一步最关键的决定`:
  the Manager understood the task but emitted no update, while the read-only verifier incorrectly
  classified the complete turn by its final advice request and confirmed that there was no commitment.
- Root cause was a contradictory verifier contract. Its generic tool description said it could only
  return fields already present in a proposed patch, even in omission-audit mode where the proposal is
  empty and its job is to report missed commitments as non-authoritative findings.
- The verifier now has mode-specific read-only tool descriptions and a clause-first omission protocol:
  a declarative goal/data-use/downstream-task clause is audited independently from a separate advice,
  explanation, or recommendation clause. Findings still cannot write the card; they trigger one bounded
  retry by the same Dialogue Manager, which remains the sole writer.
- Real DeepSeek replay passed without a task/species vocabulary branch. The Manager wrote only
  `objective`, `task_type=denovo`, and `special_themes=[immunopeptidomics]`, then offered an auditable
  HLA/MHC decision whose every option had a predeclared `strategy_patch`.
- Continuous real-model replay also passed: the numeric HLA option applied exactly its stored patch,
  the next turn asked for delivery horizon, and accepting reviewed candidates advanced to the required
  search-scale decision with curated 15, balanced 30, and explicit open-ended choices. Confirmation
  remained disabled while scale/acquisition/generalization blockers were unresolved.
- Verification after the repair: **151** targeted dialogue tests, **592** Discovery/Control Plane/PRIDE
  tests, **183** frontend tests, TypeScript/Vite production build, Python compileall, and
  `git diff --check` passed. The existing large-bundle warning remains non-blocking.
