# Swarm: Primary-theme deep search redesign

## User thesis (binding)

Example intent: `免疫肽/HLA 配体 · 小鼠 · 下游偏PSM 打分 · DDA`

1. **Most important = scientific theme** (immunopeptidomics / HLA ligands), NOT species/DDA/PSM as equal search seeds.
2. Search **deeply** on the primary theme synonym family (not shallow multi-seed fair-share).
3. Pull **many pages** of that theme until exhausted / no new projects.
4. Then **project-level** read: title, metadata, filenames, SDRF → filter mouse, DDA, PSM suitability.
5. Reject current default: many keywords × shallow pages × union → fake coverage.

## Constraints from product

- Keep confirm-before-search, no fake green, OpenAI Agents SDK dialogue path.
- User prefers **fewer hard rules**, Agent-led where possible; thin safety caps OK.
- Do not redesign deploy auth.
- Output must be implementable in this repo (`query_builder`, `search_environment`, control_plane discovery, validity/inspection).

## Roles

| ID | Role |
|----|------|
| PTS-S | Chair: synthesize MASTER plan |
| PTS-A | Query generation: primary theme family vs filters |
| PTS-B | Depth/pagination/budget allocation |
| PTS-C | Post-pool inspection & filter pipeline |
| PTS-D | Metrics/stop criteria (no fake 100% coverage) |
| PTS-E | Critic: leak risk, API limits, edge cases |

## Rounds

- R1: diagnosis of current strategy + proposal
- R2: critique each other
- R3: chair MASTER plan → boss review

## Deliverable

`docs/plans/PRIMARY_THEME_SEARCH_MASTER_PLAN.md`
