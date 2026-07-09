# Known Limitations and Next Stage

Generated on: 2026-06-26

## Current Boundaries

1. **Not a one-click large-scale reproduction system**
   - The current validation is a protected five-candidate v3 smoke benchmark.
   - It is suitable for reporting, review, and small-to-medium demonstrations, but it does not mean arbitrary users can run large-scale workflows without additional setup.

2. **The model loop is still a dry run**
   - Adapter schema, metric schema, failure modes, and gap planning are implemented.
   - Real XuanjiNovo/MassNet training is not enabled by default.

3. **MassIVE and iProX are less mature than PRIDE**
   - PRIDE is the online-first main path.
   - MassIVE currently has adapter, discovery v1, and smoke coverage.
   - iProX is index-first: refresh the public JSONL index before discovery. If the index is missing, the system returns an `iprox_index_missing` blocker.

4. **RAW / WIFF-like compatibility is not the main line for this stage**
   - RAW conversion still needs more stable validation across Windows Docker and Linux server setups.
   - WIFF-like mzML nativeID / scan mismatch remains a separate follow-up issue.

5. **Readiness/value must be interpreted with `score_source`**
   - Discovery-originated rows can have readiness/value scores.
   - Existing-run/protected evidence rows may be marked `not_scored_existing_run`; empty scores do not mean zero value.

6. **Manual browser confirmation is still recommended before presentation**
   - HTTP smoke and template tests passed.
   - Before a PPT or live demo, run one browser click-through and capture screenshots to confirm the visual state.

## Next-stage Priorities

### P0: Delivery Presentation

- Run a manual browser smoke path: Discovery -> Batch -> AI-ready Build -> Recipe.
- Capture screenshots of the three-stage AI-ready Build UI, candidate groups, and recipe/model-loop outputs.
- Use `final_agent_capability_report.md` as the source for PPT text.

### P1: Public Reproduction

- Freeze protected v3 expected outputs.
- Turn Docker/Web/CLI smoke paths into copyable steps.
- Add repository-specific notes for MassIVE and iProX.

### P2: Real Model Loop

- Add a command-template adapter for XuanjiNovo/MassNet.
- Run training only when the user explicitly provides a config or command.
- Feed real model failure modes back into the gap plan.

### P3: Data Compatibility and Scaling

- Add more real parquet outputs for recipe/leakage calibration.
- Systematically handle RAW/WIFF-like conversion and nativeID mismatch.
- Validate more real MassIVE/iProX projects.
