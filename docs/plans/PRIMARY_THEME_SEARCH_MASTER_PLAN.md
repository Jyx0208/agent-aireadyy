# Primary-Theme Deep Search — Master Plan

| Field | Value |
|-------|-------|
| Status | **R1 MASTER v2 (PTS-S chair)** — design only; no `src/` impl until user go-ahead |
| Room | `primary-theme-search` |
| Board | `docs/plans/SWARM_PRIMARY_THEME_SEARCH.md` |
| Inputs | User thesis; live code; full R1: PTS-A/B/C/D/E (`docs/plans/_pts_*_r1_*.md`) |
| Prior draft | Boss-assisted DRAFT v1 absorbed and superseded by this file |
| Deliverable owner | PTS-S Plan Chair |

---

## 0. Binding user thesis

Example intent: **免疫肽/HLA 配体 · 小鼠 · 下游偏 PSM 打分 · DDA**

1. **Most important = scientific theme** (immunopeptidomics / HLA ligands), **not** species / DDA / PSM as equal search seeds.
2. Search **deeply** on the primary-theme synonym family (many pages until exhausted / no new projects).
3. Then **project-level** read: title, metadata, filenames, SDRF → filter mouse, DDA, PSM suitability.
4. **Reject** default: many keywords × shallow pages × union → fake coverage.

**Product KEEP (non-negotiable):**

- Confirm-before-search (dialogue fingerprint / grant binding).
- No fake green (qualified / build-ready only with real evidence).
- OpenAI Agents SDK dialogue path remains strategy writer.
- Prefer **thin safety caps + agent tools** over Budget-Agent bureaucracy.
- Soft preferences must not silently become hard constraints.
- No deploy/auth redesign.

---

## 1. Live diagnosis (code evidence)

### 1.1 Query plane mixes filters into equal seeds

`src/agent/discovery/query_builder.py` → `build_pride_queries()`:

- Starts with theme terms (`immunopeptide_query_terms()` / PTM / general).
- **Cross-products** every species alias × primary term.
- Appends **`term + DDA` / `term + data dependent`** as first-class queries.
- Flat `list[str]`; no `budget_role` / primary vs filter.

**Reproduced (worktree, immunopeptidomics + mouse + DDA):**

| Stage | Count | Notes |
|-------|------:|-------|
| `build_pride_queries` | **78** | Theme + species×theme + DDA×theme |
| `prepare_pride_search_queries` atomic seeds | **22** | Includes `mus musculus`, `mouse`, `DDA`, `data dependent` **alongside** theme tokens |

`prepare_pride_search_queries` / WP-A multi-seed expansion **amplifies** this: compounds re-atomize into species and acquisition peers. PSM suitability is already correctly **not** a PRIDE seed — keep it post-pool only.

Ontology already has the right immunopeptide synonym family; the bug is **promoting filters into the same search plane**.

### 1.2 Portfolio + fair-share force shallow multi-seed

| Component | Path | Behavior |
|-----------|------|----------|
| Portfolio | `query_portfolio.py` | Up to 8 atomic seeds/unit; no budget role |
| Fair-share | `search_environment.py` `_search` | `fair_share = remaining // pending_seed_count`; pre-debits `max_pages` |
| Pool split | same | `target_per_query = max(depth, ceil(target_pool / total_seed_slots))` |
| Cap | same | `max_pages` floor 2, hard cap **20** |
| Intent | yields | `intent_dimension` **never used** for page allocation |
| Control plane | `discovery.py` | `minimum_search ∝ query_count` incentivizes seed sprawl |

Worked example: **7 seeds, budget 20** → ~2–3 pages each → coverage theater, not deep HLA recall.

### 1.3 Metrics still reward fake breadth

- `semantic_coverage` / `corpus_term_coverage` = bag-of-token OR across previews (A “covers” mouse, B DDA → 100% with 0 qualified).
- UI still surfaces coverage-style language as progress.
- `quality_target_reached` is a correct **numerator** but does not prove theme depth.

### 1.4 What already fits (reuse)

| Seam | Keep |
|------|------|
| Inspection reserve | `_candidate_search_request_budget` |
| Search grant / confirm | `SearchGrant` + `_bind_candidate_search_to_grant` |
| Qualified definition | `is_qualified_project_judgment` (inspection + evidence_backed + hard_gate pass + grade ≥ 2 + include) |
| CEM + validity | species / acquisition / immuno at project-file level |
| Bounded SDRF | `inspect_project_sdrf` |
| WP-A “never silent first-seed-only” | Keep **inside theme family** only |

---

## 2. Target architecture

```text
Dialogue (confirm-before-search + fingerprint)
        │
        ▼
┌───────────────────────────────────────┐
│ IR partition (once per accepted card) │
│  ThemeSearchPlan / RecallSpec         │
│  FilterSpec = hard + soft + provenance│
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│ RECALL PLANE (PTS-A / PTS-B)          │
│  Deep-page primary-theme family only  │
│  Role-weighted page budget            │
│  Yield-driven stop / flex top-up      │
│  → P_theme (accessions + previews)    │
└───────────────┬───────────────────────┘
                │ never inject species/DDA/PSM as equal seeds
                ▼
┌───────────────────────────────────────┐
│ FILTER PLANE (PTS-C)                  │
│  F0 partition → F1 preview triage     │
│  F2 inspect rank → F3 evidence ladder │
│  F4 judgment + CEM inspection_backed  │
│  F5 feedback without seed re-encode   │
└───────────────┬───────────────────────┘
                │
                ▼
┌───────────────────────────────────────┐
│ STOP / METRICS (PTS-D)                │
│  L1 theme exhaustion + novelty streak │
│  L3 qualified_after_filter + inspect  │
│  Kill corpus-100 as success           │
└───────────────────────────────────────┘
```

**Honesty bound:** theme-deep keyword recall is **not** perfect theme∩species∩DDA recall. Recovery valves exist (see §3.5 / §7); never claim 100% conjunction recall.

---

## 3. Query model (PTS-A accepted)

Source: `docs/plans/_pts_a_r1_query.md`

### 3.1 Dimension taxonomy

| Class | Examples | Search seeds? | Applied where |
|-------|----------|---------------|---------------|
| **Primary theme (recall)** | immunopeptidomics, HLA ligandome, MHC eluted ligand | **Yes — exclusive deep family** | Query + pagination |
| **Secondary theme (same axis)** | HLA class I/II, neoantigen HLA, IP phrases | Yes, after core plateaus | Same primary pool, lower rank |
| **Hard filter** | species=mouse, acquisition=DDA | **No** (default) | Post-pool CEM hard + validity |
| **Soft suitability** | PSM scoring friendly, peaklist/raw preferred | **No** | Grade / ranking / limitations |
| **Exact accession** | PXD… | Yes, pin path | 1 cheap hit, not fair-share peer |
| **filter_rescue** (optional) | narrow mouse×theme after theme deep + 0 filter-pass | Only agent + confirm + hard cap | Never default equal peer |

### 3.2 Theme family construction

1. Detect primary theme from strategy card / goal / special_themes / ontology (`is_immunopeptidomics_goal`, PTM types, user `query_terms`). Support Chinese/synonym paths in ontology/query_builder — not prompt-only.
2. Expand **synonym family only** (reuse `immunopeptide_query_terms()`, PTM terms). Prefer high-precision phrases first; keep multi-word theme seeds when PRIDE benefits.
3. **Do not** append `species × term` or `term × DDA` into the default portfolio.
4. Soft-cap primary seed count **3–5** (safety, not science policy). Synonym expand is agent-led under budget + confirm-bound strategy.

### 3.3 Structured plan (API shape)

```text
ThemeSearchPlan
  primary_theme_id: str
  primary_family: list[ThemeQueryUnit]   # ordered
  secondary_family: list[ThemeQueryUnit]
  filters: FilterSpec
  rescue_policy: RescuePolicy
  budget_policy: ThemeBudgetHints
  multi_theme_policy: single | sequential | multi_primary | clarify

ThemeQueryUnit
  text: str
  role: primary_theme | theme_synonym | secondary_theme
        | secondary_axis | filter_only | rescue_filter_fused | exact_accession
  family_rank: int
  intent_dimension: scientific_theme | rescue | ...
  suggested_min_pages: int | null        # soft; PTS-B owns hard budget
  must_exhaust: bool                     # default true for core primary family

FilterSpec
  hard_filters: [{id, dimension, target, provenance}]
  soft_filters: [{id, dimension, target, provenance}]
  match_surfaces: title | metadata | filename | sdrf
```

**Functions:**

| Function | Responsibility |
|----------|----------------|
| `build_theme_search_plan(request)` | **Primary API** — family + filters + rescue + multi-theme policy |
| `theme_family_queries(plan)` | Flatten theme units only for deep search |
| `build_pride_queries` (compat) | Theme-only by default under new mode; **stop** default Cartesian peers |
| `prepare_pride_search_queries` | Add mode: `theme_atomic` vs `legacy_compound`; do not reintroduce filter atoms from compounds |

### 3.4 Portfolio tagging

Extend `QueryUnit` with `budget_role` (or map from `intent_dimension`):

```text
budget_role ∈ {
  primary_theme, theme_synonym, secondary_theme,
  secondary_axis, filter_only, rescue_filter_fused, exact_accession
}
```

**Fail closed:** missing role → **not** primary (secondary/unknown); never default missing → primary depth.

### 3.5 Search phases (contract)

1. **Phase 1 — Primary-theme deep recall** (A+B): ordered family, deep pages, union into `P_theme`.
2. **Phase 2 — Project-level filter** (C): title/metadata/filename/SDRF.
3. **Phase 3 — Bounded rescue** (optional): only if theme pool large but filter yield ~0 **or** theme pool empty after secondary family; tiny rescue set; labeled; confirm; separate budget envelope. Prefer secondary theme expand **before** species fusion.

### 3.6 Multi-theme policy (binding)

| Case | Policy |
|------|--------|
| One clear theme + filters | primary-theme deep (default) |
| Two+ competing themes, no rank | **clarify or confirm primary** (one thin question) |
| User ranks T1 then T2 | sequential exhaust/tranche under budget; meters per family |
| Explicit union exploration | `multi_primary`; report per-theme pages + qualified; no single green bar |

**Hard reject:** silently drop a user scientific theme from the card.

### 3.7 Example partition (user intent)

| Token | Class | budget_role |
|-------|--------|-------------|
| 免疫肽 / HLA 配体 family | primary theme | `primary_theme` / `theme_synonym` |
| 小鼠 | hard filter species | `filter_only` |
| DDA | hard filter acquisition | `filter_only` |
| 下游偏 PSM 打分 | soft suitability | `filter_only` |

---

## 4. Depth & budget (PTS-B accepted)

Source: `docs/plans/_pts_b_r1_depth.md`

### 4.1 Kill

- `fair_share = remaining // pending_seed_count`
- `(target_pool + total_seed_slots - 1) // total_seed_slots` when roles exist
- Equal pre-debit of `max_pages` for every seed
- Default path that keeps fair-share as silent “fallback when roles missing” (feature-flag migration OK; production default must be role-aware)

### 4.2 Replace with role-weighted pools

Given search page budget `B` (already net of inspection reserve):

```text
primary_pool   ≈ 0.75–0.90 × B     # default
secondary_pool ≈ 0–0.15 × B        # v1 product default: 0
flex_pool      = remainder         # top-up primary only
```

1. Partition by `budget_role`; `filter_only` never enters page loop.
2. **Primary-first**; synonyms share primary pool with depth dedupe (`_seed_depths`).
3. Initial tranche `T0` (e.g. 3–5 pages); **yield-driven extensions** to highest `new / pages`.
4. Secondary smoke: ≤1 page only if explicitly requested; default **0**.
5. **Post-debit actual pages used**; release unused tranche to `flex_pool`.
6. Theme target is **not** diluted by filter seed count; multi-round re-entry preferred over unbounded single-call pages.
7. Prefer true **page loop** / per-page yield callback so diminishing-yield stop does not fetch useless tails.

### 4.3 Inspection reserve (keep + retune)

Keep `_candidate_search_request_budget` spirit:

- Reserve ~`min(20, max_projects) * 3` (or equivalent) for inspection when possible.
- **Invariant:** `inspection_reserve >= min(desired, max(3, remaining // 4))` when candidates or `max_projects > 0`.
- Retune `minimum_search`: scale by **primary seed count × T0**, **not** raw query/seed sprawl.
- Round-2+ deepen still confirm-bound when strategy fingerprint changes; no silent reserve theft.

### 4.4 Operational stop (frees budget)

Per seed: empty/short page; `Z` consecutive pages with 0 new accessions; diminishing yield; role/cap; candidate ceiling; global budget empty.

---

## 5. Post-pool filter pipeline (PTS-C accepted)

Source: `docs/plans/_pts_c_r1_filter.md`

### 5.1 Stages

| Stage | Purpose |
|-------|---------|
| **F0** | Partition request → `RecallSpec` + `FilterSpec` (hard/soft + provenance) |
| **F1** | Search-stage triage on previews — provisional CEM only; never `evidence_backed` |
| **F2** | Rank inspect queue (theme relevance + unknown hard filters; deprioritize clear preview FAIL) |
| **F3** | Evidence ladder: title/meta → filenames → SDRF → file/project validity |
| **F4** | Inspection-backed judgments + CEM commit |
| **F5** | Feedback meters to search (**no** auto re-encode filters as equal seeds) |

### 5.2 Strength rules

- Hard filters only from user / accepted recommendation (`may_be_hard` provenance).
- Missing hard evidence → `UNKNOWN` / investigate, **not** silent FAIL / hard drop when inspect budget remains.
- Soft (PSM suitability) → grade / limitations only.
- Preview contradiction may deprioritize; structured multi-source contradiction may early exclude.
- Prefer structured species/acquisition refs; filename tokens are soft corroboration only.

### 5.3 Forbidden feedback

“Species fail rate high → add mouse query at equal depth” as default.

**Allowed:** labeled `filter_rescue` with confirm-before-search + hard cap.

### 5.4 Reuse

`candidate_evidence_matrix.py`, `validity.py`, `project_judgment.py`, `inspect_project_sdrf` — extend, don’t fork matchers. One qualified definition only.

---

## 6. Metrics & stop (PTS-D accepted)

Source: `docs/plans/_pts_d_r1_metrics.md`

### 6.1 Success definition

> **Success = inspection-backed qualified projects after theme-deep recall + project-level filters — never corpus term OR-coverage hitting 100%.**

### 6.2 Kill list (anti-metrics)

Cannot alone drive scientific stop / ready / “检索完成”:

- `corpus_term_coverage == 1.0` / `semantic_coverage` (any rename of the same OR formula)
- “all seeds ran once”
- raw candidate_count / theme_pages alone / theme_pool_exhausted alone
- high_relevance without inspection
- multi-seed union page count
- provisional CEM conjunction as qualified
- hard ceiling relabeled as `quality_target_reached`

### 6.3 Layers

```text
L0 Safety ceilings     → hard stop / limitations (never success)
L1 Theme-pool recall   → theme_pool_exhausted, no_new_project_streak
L2 Post-pool filter    → filter_pass / fail / unknown
L3 Inspection+qualify  → qualified_after_filter vs target
L4 Delivery            → strict_valid / build-ready (existing no-fake-green)
```

### 6.4 Core theme meters (implement later)

`primary_theme_seed_ids`, `theme_pages_fetched`, `theme_raw_hits`, `theme_unique_accessions`, `theme_new_accessions_last_k_pages`, `theme_page_short_final`, `theme_pool_exhausted`, `no_new_project_streak`, `marginal_theme_yield`, plus filter/inspect budgets and `qualified_after_filter`.

**Family exhaust policy (chair resolve of PTS-D open #1):**  
`theme_pool_exhausted` when **all `must_exhaust` primary units** hit E1 (API short/empty) or E2 (no-new streak ≥ `K_page`) **or** soft theme page cap. Optional synonyms do not alone mark family exhausted. Empty family (`theme_raw_hits==0`) uses different user copy than exhausted-after-yield.

### 6.5 Scientific stop (binding sketch)

**`scientific_stop_ready` (fixed/curated target)** requires **all**:

- `qualified_after_filter >= target` (same as `is_qualified_project_judgment` count)
- **and** inspection minimum met
- **and** unknown hard rate under epsilon
- **and** theme depth evidence:  
  - prefer `theme_pool_exhausted` **and** low marginal **filter-eligible** yield, **or**  
  - curated early-stop: target met **and** last theme pages show no new filter-eligible accessions **and** not maximize mode  
- **not** corpus-coverage-only

**Maximize / open-ended / harvest_all:** require `theme_pool_exhausted` **and** `qualified_no_gain_streak >= K_qual` (default 2), or L0 → limitations / portfolio incomplete.

**Defaults (thin caps):** `K_page=3` (min 2 if agent sets); `K_qual=2`; min page_size (or result-count basis) for streak anti-gaming e.g. ≥20; inspect floor α≈2× target when pool large.

### 6.6 Operator-facing summary

Lead with: `qualified_after_filter / target`, `theme_pool_exhausted`, `no_new_project_streak`, `theme_pages_fetched`, `inspection_budget_*`, `stop_reason`, `limitations[]`.  
Coverage only as diagnostic (`corpus_term_coverage_diagnostic`).

---

## 7. Critic reject conditions (PTS-E accepted — binding)

Source: `docs/plans/_pts_e_r1_critic.md`

Master plan / implementation **REJECT** if any:

| ID | Code | Reject if |
|----|------|-----------|
| 1 | **R-CONFIRM** | PRIDE page fetch without confirm-before-search / fingerprint (incl. auto deep / silent round-2 on card change) |
| 2 | **R-FAKEGREEN** | success/ready from corpus coverage, pages, raw pool, theme_exhausted alone, L0 ceiling, or ai_ready/build-ready without validity path |
| 3 | **R-SOFTHARD** | missing metadata or soft prefs hard-exclude without inspect-unknown path |
| 4 | **R-SPECIESMETA** | mouse/DDA filter only on search preview; no SDRF/file inspect path |
| 5 | **R-MULTITHEME** | drop or starve a user theme without clarify/sequential policy; silent multi-primary fair-share |
| 6 | **R-APIABUSE** | unbounded primary×pages; no governor/429→limitations; streak gameable with page_size=1 |
| 7 | **R-FAIRSHARE** | default equal fair_share remains; missing role→primary; tests enforce equal pages |
| 8 | **R-BUILDER** | only allocator changed; `build_pride_queries` still injects species/DDA as equal deep peers with no mode switch |
| 9 | **R-PROMPTONLY** | no file-level hooks/tests |
| 10 | **R-QUALDEF** | second qualified definition; provisional CEM counted as inspection-backed qualified |
| 11 | **R-SYNONYMLOOP** | unbounded synonym expand as success avoidance |
| 12 | **R-HORIZON** | plan_only/ai_ready/full_release honesty broken by theme-search completion |

**Thin caps that must appear in impl:**

```text
repo_request_ceiling          : existing governor wins
primary_seed_soft_cap         : 3–5
theme_page_hard_cap_per_run   : align max_repository_requests
min_page_size_for_streak      : e.g. >= 20 (or count results not pages)
inspection_reserve            : non-zero when candidates or max_projects > 0
429/5xx                       : stop_reason=repository_rate_or_outage; limitations; never quality_target_reached
```

**Approve direction (PTS-E):** role-weighted deep primary + post-pool UNKNOWN→inspect + PTS-D layered stops + confirm retained + multi-theme clarify/sequential + control-plane minimum_search retune + named regression tests.

---

## 8. Implementation work packages

| WP | Content | Primary files | Depends |
|----|---------|---------------|---------|
| **PTS-1 IR partition** | `ThemeSearchPlan` / `FilterSpec` / seed `budget_role`; stop Cartesian default; Chinese/theme detection hooks | `src/agent/discovery/query_builder.py`, `ontology.py`, control_plane request prep | — |
| **PTS-2 Portfolio roles** | Carry roles through `QueryUnit`; mode-aware seed expand; agent query tools emit roles | `src/agent/discovery/query_portfolio.py`, Agents SDK query construction | PTS-1 |
| **PTS-3 Role-weighted search** | Replace fair-share; primary-first; post-debit; yield/page-loop stop | `src/agent/discovery/search_environment.py`, pride client page accounting | PTS-2 |
| **PTS-4 Inspection reserve** | Retune min search to primary×T0; explicit theme/inspect budget fields | `src/agent/control_plane/discovery.py`, `budget_governor.py` | PTS-3 |
| **PTS-5 Filter pipeline** | F0–F5 wire CEM/judgment/inspect ladder; ban filter seed feedback | `candidate_evidence_matrix.py`, `validity.py`, `project_judgment.py`, control_plane tools | PTS-1 |
| **PTS-6 Metrics/stop** | Theme meters; kill coverage stop; UI copy; optional pure `stop_policy` helpers | `search_environment.py`, control_plane discovery, `web/app.py`, FE progress | PTS-3, PTS-5 |
| **PTS-7 Tests** | See §10; invert fair-share tests; confirm; soft≠hard; multi-theme; 429 limitations | `tests/test_discovery*.py`, search_environment / control_plane tests | all |
| **PTS-8 Agent prompts** | Deepen theme, inspect filters, no coverage theater; offline plan pre-confirm | `src/agent/control_plane/openai_agents.py`, `agentic.py` | PTS-1–6 |

**Recommended order:** PTS-1 → PTS-2 → PTS-3 → PTS-4 → PTS-5 → PTS-6 → PTS-7 → PTS-8.

**Suggested first vertical slice (after user go-ahead):**

1. Red test: immunopeptidomics + mouse + DDA → **no** mouse/DDA equal page peers; primary theme gets majority of fixed page budget.
2. Green: IR partition + role-weighted search only (filters still via existing validity/judgment).
3. Then metrics demotion of corpus coverage as stop + UI copy.

Optional mode flag for migration: `search_allocation_mode=primary_theme_deep` (name bikeshed OK).

---

## 9. Explicit non-goals

- Deploy auth redesign.
- Full CEM science rewrite beyond filter/stop wiring.
- Notebook dialogue re-litigation.
- Case-only patch for one immunopeptidomics run without generic roles/metrics.
- Prompt-only “plan” with no file paths.
- Removing confirm-before-search or inspection-backed qualified definition.
- Claiming perfect theme∩species∩DDA keyword recall.
- Budget-Agent bureaucracy theater.

---

## 10. Acceptance criteria & regression tests

### 10.1 Design → later impl criteria

| # | Criterion |
|---|-----------|
| 1 | Default portfolio for immuno+mouse+DDA does **not** fair-share pages across mouse/DDA atomic seeds |
| 2 | Primary theme family can consume majority of search page budget until exhaustion / no-new streak |
| 3 | Species/DDA/PSM applied via project-level inspect/CEM/validity path |
| 4 | `scientific_stop` / ready cannot fire on corpus coverage alone |
| 5 | Inspection reserve still allows metadata/file/SDRF reads |
| 6 | Confirm-before-search and no-fake-green gates unchanged |
| 7 | Missing species in preview → UNKNOWN + inspect path, not hard drop |
| 8 | Multi-theme has clarify/sequential policy; no silent drop |
| 9 | 429/outage → limitations, never quality_target_reached |
| 10 | Focused tests prove role-weighted allocation + anti-metrics |

### 10.2 Named tests (minimum)

| Test | Guards |
|------|--------|
| `test_primary_theme_deep_search_requires_confirm` | R-CONFIRM |
| `test_theme_round2_requires_confirm_on_fingerprint_change` | R-CONFIRM |
| `test_budgeted_search_majority_pages_on_primary_theme` | R-FAIRSHARE (invert old equal-share test) |
| `test_build_pride_queries_theme_only_default_no_species_dda_peers` | R-BUILDER |
| `test_corpus_coverage_not_scientific_stop` | R-FAKEGREEN |
| `test_theme_exhausted_zero_qualified_is_limitations` | R-FAKEGREEN |
| `test_missing_species_preview_unknown_then_sdrf_recover` | R-SPECIESMETA / R-SOFTHARD |
| `test_soft_psm_preference_not_hard_exclude` | R-SOFTHARD |
| `test_multi_theme_requires_clarify_or_sequential` | R-MULTITHEME |
| `test_repository_429_is_limitations_not_ready` | R-APIABUSE |
| `test_qualified_definition_single_inspection_backed` | R-QUALDEF |
| `test_primary_seed_soft_cap` | R-SYNONYMLOOP / R-APIABUSE |

---

## 11. Swarm index

| Role | Artifact | Status |
|------|----------|--------|
| Board | `docs/plans/SWARM_PRIMARY_THEME_SEARCH.md` | done |
| PTS-A | `docs/plans/_pts_a_r1_query.md` | **done** → §3 |
| PTS-B | `docs/plans/_pts_b_r1_depth.md` | **done** → §4 |
| PTS-C | `docs/plans/_pts_c_r1_filter.md` | **done** → §5 |
| PTS-D | `docs/plans/_pts_d_r1_metrics.md` | **done** → §6 |
| PTS-E | `docs/plans/_pts_e_r1_critic.md` | **done** → §7 |
| Chair | **this file** | **R1 MASTER v2** |

R2 optional: peer mutual critique. R3: boss review / user go-ahead before coding.

---

## 12. Human summary (中文)

你要的检索方式：

> **先把科学主题（如免疫肽/HLA）搜深、翻够页** → 再在项目级用标题/元数据/文件名/SDRF 筛小鼠、DDA、是否适合 PSM → **不要**把物种/DDA 和主题当成平等关键词浅搜拼并集假覆盖。

计划落地：

- 改 `query_builder` / `query_portfolio`：主题家族 vs 过滤维度 + `budget_role`
- 改 `search_environment`：取消 equal fair-share，主题优先翻页、按实际页扣预算
- 过滤走现有 CEM / validity / inspection；UNKNOWN 先 inspect
- 停搜以「主题池耗尽 + 过滤后合格数 + inspect 下限」为准，**禁止**词袋覆盖率当成功
- 保留 confirm-before-search、inspection reserve、no fake green
- PTS-E 十二条 REJECT 为硬门禁

**未实现代码**；等你确认后按 WP PTS-1…8 开工。

---

## 13. Chair resolution notes (open points closed)

| Open | Chair decision |
|------|----------------|
| Curated early-stop vs maximize exhaust | §6.5: curated may early-stop only with inspect min + low marginal **filter-eligible** yield; maximize needs exhaust + qualified stall |
| Family exhaust any-vs-all | All `must_exhaust` core units (not optional synonyms alone) |
| Secondary pool v1 | Default **0** pages; rescue valve after deep theme + 0 filter-pass |
| Missing role | Fail closed → not primary |
| Acquisition hard-by-default | Only if hard provenance / `hard_constraint_fields` (fewer hard rules) |
| Preview auto-exclude species | Prefer deprioritize; high-confidence structured only; else inspect |

*End PTS-S R1 MASTER v2.*
