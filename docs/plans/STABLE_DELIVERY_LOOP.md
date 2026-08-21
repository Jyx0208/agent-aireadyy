# 稳定交付自动循环（Stable Delivery Loop）

**用户意图：** 自动改、自动测，直到可稳定交付；独立 agent 负责测试。  
**Provider：** worker / verifier 均为 `pi/relay/grok-4.5`（Codex 额度不足）。  
**工作树：** `benchmark-review-planning`

## 退出条件（done）

1. `scripts/run_stable_delivery_gate.ps1` 退出码 0  
2. Verifier 输出 `DONE:true`  
3. 仍 fail-closed：weak_keep 不能进 BuildReadyPackage  

**注意：** 这不等于生产 L3 GO，只表示「发现→strict-valid→materialize 合约」在测试/门禁下稳定。

## 门禁命令

```powershell
powershell -NoProfile -File scripts/run_stable_delivery_gate.ps1
```

## 循环启动

见 `docs/plans/_start_stable_delivery_loop.ps1`

## 迭代日志

（Worker / Verifier 在此追加）

### 2026-07-23 — Worker (pi/relay/grok-4.5) 迭代

**命令：** `powershell -NoProfile -File scripts/run_stable_delivery_gate.ps1`  
**结果：** `STABLE_DELIVERY_GATE=PASS`（96 passed；materialize_fail_closed 8；validity_policy weak_keep/valid OK）→ **GATE_OK**

**本迭代关注点 / 修复：**

1. **validity（mixed + general）** — `src/agent/discovery/validity.py`
   - 项目级 `mixed_acquisition_project` 在 `review_mixed` 下固定 **weak_keep**（不再因 general_domain 误升 valid）。
   - 文件级：`general_discovery_target` + 下载/方法齐全且无 soft blocks → **valid**（`allow` / file-level DDA 确认路径）。
   - 项目级 immuno / soft blocks 仍 **weak_keep**；weak_keep **永不**进 materialize。

2. **scoring 仪器广播** — `src/agent/discovery/scoring.py`
   - 停止把项目级 instrument names/families 再广播到每个文件；仅保留条件性 fragmentation 继承。
   - 对齐 `test_discovery_extracts_instrument_and_fragmentation_from_metadata`（file.instrument_names == []）。

3. **真实 run 路径播种 publication 输入** — `src/agent/control_plane/openai_agents.py`
   - `_seed_publication_inputs_from_selected_manifest`：audit ready + **strict-valid** 子集 → 确定性生成 `builder_file_entry` observations、membership inventory、`dataset-builder/v1` entrypoint + preflight ref。
   - **仅 valid** 文件参与播种；weak_keep 不播种 evidence store / package。
   - 已存在的 pending preflight **不**被自动升为 ready（fail-closed）。
   - `_persist_discovery_audit_snapshot` 在 materialize 前调用该播种。

4. **相关/既有改动面（同工作树）：** `discovery.py`（zero_strict search_more 等）、`features.py`、证据策略测试。

**改动文件（本轮交付相关）：**
- `src/agent/discovery/validity.py`
- `src/agent/discovery/scoring.py`
- `src/agent/control_plane/openai_agents.py`
- `src/agent/control_plane/discovery.py`（既有 Wave A 搜索侧）
- `docs/plans/STABLE_DELIVERY_LOOP.md`（本日志）

**红线自检：** weak_keep → package=None、不写 evidence store；materializer 仍 `validity_not_valid`。


## 已启动

- **Loop id:**  name=
  - worker + verify-provider: 
  - verify-check: 
  - max-iterations: 12, max-time: 6h, archive on
- **独立测试 agent:** 见 paseo ls 标题 

## 基线门禁（编排手跑 2026-07-23）

- STABLE_DELIVERY_GATE=**FAIL**
- pytest: 7 failed, 89 passed（子集）
- 失败要点：
  - materialization 真实 publication inputs 仍 None
  - mixed_acquisition 期望与新 validity 不一致
  - discovery 仪器提取测试期望空列表 vs 现有值
- validity_policy smoke: weak_keep + valid **OK**
- materialize fail-closed **OK**

Loop 目标：Worker 修到 gate 绿，Verifier 报 DONE:true。

## VERIFY baseline (Independent Tester, 2026-07-23)

**Command:**
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_stable_delivery_gate.ps1
```

**Exit code:** `1` → `STABLE_DELIVERY_GATE=FAIL`

**Results:**
| Check | Result |
| --- | --- |
| pytest gate subset | **FAIL** — 7 failed, 89 passed (~6s) |
| materialize fail-closed smoke | **PASS** — `GATE_OK materialize_fail_closed 8` (no package, blockers present) |
| validity policy smoke | **PASS** — `GATE_OK validity_policy weak_keep valid` |
| overall gate | **FAIL** |

### Failed tests (exact)
1. `tests/test_discovery_build_ready_materialization.py::test_selected_manifest_prepares_real_publication_inputs_without_fixture_injection`  
   - `assert persisted.publication_evidence_store is not None` (got `None`)  
   - Selected-manifest path via `_persist_discovery_audit_snapshot` still does **not** wire real publication inputs (evidence store / membership / preflight / package material).
2. `tests/test_discovery_mixed_acquisition_policy.py` × 5:
   - `test_project_mixed_acquisition_policy_controls_validity[review_mixed-weak_keep-False-True]` — got `valid`, expected `weak_keep`
   - `test_file_mixed_acquisition_policy_controls_delivery_review[review_mixed-True-valid-False-False]` — got `weak_keep`, expected `valid`
   - `test_file_mixed_acquisition_policy_controls_delivery_review[allow-False-valid-False-False]` — got `weak_keep`, expected `valid`
   - `test_file_mixed_acquisition_policy_controls_delivery_review[allow-True-valid-False-False]` — got `weak_keep`, expected `valid`
   - `test_review_mixed_file_level_evidence_resolves_project_uncertainty_at_the_file` — project got `valid` (expected `weak_keep`); file path also inconsistent with policy table
3. `tests/test_discovery.py::test_discovery_extracts_instrument_and_fragmentation_from_metadata`  
   - `assert file.instrument_names == []` but got `['Q Exactive HF', 'Q Exactive']` (project→file method inheritance from scoring Wave A leaks into file when test expects empty extraction).

### Policy / anti-regression checks (diff review)
| Rule | Status |
| --- | --- |
| weak_keep must not enter materialize as build-ready | **HOLD** — `publication.py` requires `validity_status == "valid"`; gate smoke fail-closed OK; materialize with incomplete state returns package=None + blockers |
| no fake-green (0 build-ready counted as success) | **HOLD** — control_plane adds `zero_strict_valid_files_with_qualified_projects` + `search_more` when qualified>0 and strict_valid==0 |
| immuno special-case hardcoding | **No new hardcode of project accessions / PXD ids observed.** Goal-aware immuno reasons remain ontology-driven (`is_immunopeptidomics_goal`); filename immuno is soft (`filename_immunopeptide_hint`) and cannot alone authorize `valid`. |
| positive path: strict-valid can materialize | **Partial** — existing materialization tests: 13 pass / 1 fail; fail is the *real selected-manifest wiring* test, not the fixture-injected happy path. Gate does **not** print a positive materialize-success smoke beyond fail-closed + validity.

### done criteria evaluation
- gate exit 0: **NO**
- materialize fail-closed without package / weak files: **YES** (smoke + code path)
- at least one proof that legal strict-valid materializes OR gate prints GATE_OK end-to-end: **NO** for full gate (pytest red); positive materialize fixture tests exist but real-path test still red

**Verdict: DONE:false**

### Suggested next Worker cut (do not implement here)
1. **mixed_acquisition policy (highest leverage for 5 reds):** In `validity.py` project/file branches, honor `request.mixed_acquisition_policy`:
   - project `review_mixed` + `mixed_acquisition_project` → `weak_keep` (not `valid` via domain/method short-circuit)
   - file `review_mixed` without file-level acquisition resolution → `needs_review` + `needs_file_level_acquisition_confirmation`
   - file `review_mixed`/`allow` with DDA file-level evidence and full method+delivery → `valid` (today soft-blocks to `weak_keep` even under `allow`)
2. **Real publication wiring:** Make `_persist_discovery_audit_snapshot` (or selected-manifest ready path) populate `publication_evidence_store`, membership refs, builder entrypoint/preflight, clear materialization blockers when audit ready + strict-valid files exist — so `test_selected_manifest_prepares_real_publication_inputs_without_fixture_injection` passes without test injection.
3. **Instrument inheritance vs extraction test:** Either gate inheritance so pure metadata extraction fixture without project instruments stays empty, or update `test_discovery_extracts_instrument_and_fragmentation_from_metadata` expectations *only if* inheritance is intentional and test fixture truly has project-level instruments (prefer product fix: inheritance only when project has methods *and* file empty *and* not a unit-test-only empty feature path that still pulls project names incorrectly).
4. Do **not** loosen materialize to accept `weak_keep`; do **not** treat 0 strict-valid as GO.

## SWARM GATE (P4 independent runner, 2026-07-23 11:40:31 +08:00)

**Role:** Swarm P4 — run gate only; no product code changes.

**Command:**
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_stable_delivery_gate.ps1
```

**Exit code:** `1` → `STABLE_DELIVERY_GATE=FAIL`

**cwd:** `E:/ai-agent-already/github-publish/agent-aireadyy/.claude/worktrees/benchmark-review-planning`  
**python:** `.venv/Scripts/python.exe`

| Check | Result |
| --- | --- |
| pytest gate subset | **FAIL** — 7 failed, 89 passed (~6.55s) |
| materialize fail-closed smoke | **PASS** — `GATE_OK materialize_fail_closed 8` |
| validity policy smoke | **PASS** — `GATE_OK validity_policy weak_keep valid` |
| overall gate | **FAIL** |

### Failed tests (exact, unchanged vs baseline)
1. `tests/test_discovery_build_ready_materialization.py::test_selected_manifest_prepares_real_publication_inputs_without_fixture_injection`
   - `assert None is not None` (publication inputs still unwired on real selected-manifest path)
2. `tests/test_discovery_mixed_acquisition_policy.py` × 5:
   - `test_project_mixed_acquisition_policy_controls_validity[review_mixed-weak_keep-False-True]` — got `valid`, expected `weak_keep`
   - `test_file_mixed_acquisition_policy_controls_delivery_review[review_mixed-True-valid-False-False]` — got `weak_keep`, expected `valid`
   - `test_file_mixed_acquisition_policy_controls_delivery_review[allow-False-valid-False-False]` — got `weak_keep`, expected `valid`
   - `test_file_mixed_acquisition_policy_controls_delivery_review[allow-True-valid-False-False]` — got `weak_keep`, expected `valid`
   - `test_review_mixed_file_level_evidence_resolves_project_uncertainty_at_the_file` — got `valid`, expected `weak_keep`
3. `tests/test_discovery.py::test_discovery_extracts_instrument_and_fragmentation_from_metadata`
   - instrument_names got `['Q Exactive HF', 'Q Exactive']`, expected `[]`

### Smoke contracts
- weak_keep / incomplete state must not materialize package: **HOLD** (fail-closed smoke OK)
- validity weak_keep + valid path smoke: **HOLD**

### Delta vs VERIFY baseline
- **No green progress.** Same 7 failures, same 89 passes. Smokes still green.
- Gate not exit-0; swarm workers (P1/P2/P3/P5) have not yet cleared their ownership reds as observed by this independent run.

**Verdict: DONE:false** (P4 observation only)

### Ownership remapping for remaining reds
| Fail cluster | Owner role | Files (per GROK_PARALLEL_SWARM.md) |
| --- | --- | --- |
| mixed_acquisition ×5 | P1 | `validity.py` + `tests/test_discovery_mixed_acquisition_policy.py` |
| selected-manifest publication inputs | P2 | `openai_agents.py` / publication seed path + materialization tests |
| instrument_names non-empty | P3 | `scoring.py`/`features.py` + `tests/test_discovery.py` |
| wiring if any new gate-only reds | P5 | `tests/test_discovery_wiring_*.py` (not red this run) |


## VERIFY (Independent Tester, 2026-07-23)

**Role:** Independent Verifier — validate only; no product feature work.

**Command:**
```powershell
powershell -NoProfile -File scripts/run_stable_delivery_gate.ps1
```

**Exit code:** `0` → `STABLE_DELIVERY_GATE=PASS`

**cwd:** `E:/ai-agent-already/github-publish/agent-aireadyy/.claude/worktrees/benchmark-review-planning`  
**python:** `.venv/Scripts/python.exe`

| Check | Result |
| --- | --- |
| pytest gate subset | **PASS** — 96 passed in 6.76s |
| materialize fail-closed smoke | **PASS** — `GATE_OK materialize_fail_closed 8` |
| validity policy smoke | **PASS** — `GATE_OK validity_policy weak_keep valid` |
| overall gate | **PASS** — `GATE_OK pytest` + smokes; ends with `STABLE_DELIVERY_GATE=PASS` |

### Key assertions confirmed
1. **Gate exit 0** and prints `GATE_OK` / `STABLE_DELIVERY_GATE=PASS`.
2. **Fail-closed materialize:** incomplete snapshot → `package is None` + blockers (`GATE_OK materialize_fail_closed 8`). Code path in `publication.py` rejects non-`valid` files with `validity_not_valid` (weak_keep cannot enter BuildReadyPackage).
3. **Validity policy smoke:** weak_keep + valid paths both present (`GATE_OK validity_policy weak_keep valid`).
4. **Positive strict-valid materialize path:** gate pytest includes materialization suite (e.g. `test_selected_manifest_prepares_real_publication_inputs_without_fixture_injection`, fixture-based materializer success tests) — all green under the 96-pass run; selected-manifest seeding only uses `validity_status == "valid"` files.
5. **No fake-green:** `discovery.py` still errors + `search_more` when qualified>0 and `strict_valid_file_count==0`.
6. **No immuno accession hardcoding observed** in reviewed diffs; filename immuno remains soft (`filename_immunopeptide_hint`).

### Diff review (validity/scoring/discovery/publication/openai_agents)
| Rule | Status |
| --- | --- |
| weak_keep must not enter materialize | **HOLD** — seed filters `validity_status == "valid"`; materializer requires `valid` |
| no immuno special-case hardcoding | **HOLD** — no PXD/id hardcodes in diff |
| no fake-green (0 build-ready as success) | **HOLD** — zero_strict_valid continue-search audit issue |

### done criteria evaluation
- gate exit 0: **YES**
- materialize fail-closed without package / weak files: **YES**
- proof legal strict-valid materializes or GATE_OK: **YES** (`GATE_OK` + 96 passed including positive materialize tests)

**Verdict: DONE:true**

## SWARM GATE (P4 independent runner, 2026-07-23 11:47:30 +08:00)

**Role:** Swarm P4 — run gate only; no product code changes.  
**Trigger:** re-run after chatroom P1/P2/P3 DONE notices.

**Command:**
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_stable_delivery_gate.ps1
```

**Exit code:** `0` → `STABLE_DELIVERY_GATE=PASS`

**cwd:** `E:/ai-agent-already/github-publish/agent-aireadyy/.claude/worktrees/benchmark-review-planning`  
**python:** `.venv/Scripts/python.exe`

| Check | Result |
| --- | --- |
| pytest gate subset | **PASS** — 96 passed (~6.74s) |
| materialize fail-closed smoke | **PASS** — `GATE_OK materialize_fail_closed 8` |
| validity policy smoke | **PASS** — `GATE_OK validity_policy weak_keep valid` |
| overall gate | **PASS** |

### Failed tests
- none

### Delta vs prior SWARM GATE (11:40 FAIL)
- pytest: 7 failed / 89 passed → **0 failed / 96 passed**
- materialize fail-closed: still HOLD
- validity policy smoke: still HOLD
- prior red clusters (mixed_acquisition ×5, selected-manifest wiring ×1, instrument extract ×1) cleared after P1/P2/P3 work

### done criteria (gate-only)
- gate exit 0: **YES**
- weak_keep cannot enter package (smoke): **YES**
- Verifier DONE:true: **not claimed by P4** (gate observation only)

**Verdict: GATE_GREEN** — `DONE:false` for full loop still requires Verifier; P4 gate objective is green.

## VERIFY post-swarm (Independent Tester, 2026-07-23 chatroom re-run)

**Role:** Independent Tester (not implementer). Chatroom CLAIM → re-gate after P1–P5 DONE.

**Command:**
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_stable_delivery_gate.ps1
```

**Exit code:** `0` → `STABLE_DELIVERY_GATE=PASS`

| Check | Result |
| --- | --- |
| pytest gate subset | **PASS** — 96 passed in 6.60s |
| materialize fail-closed smoke | **PASS** — `GATE_OK materialize_fail_closed 8` |
| validity policy smoke | **PASS** — `GATE_OK validity_policy weak_keep valid` |
| targeted prior-reds recheck | **PASS** — materialize real-path + mixed_acquisition 14 + instrument extract (16 tests) green |
| overall gate | **PASS** |

### Key assertions
1. Gate exit 0 + `STABLE_DELIVERY_GATE=PASS` + `GATE_OK pytest`.
2. Fail-closed: empty snapshot → package None + blockers (smoke + direct call).
3. weak_keep blocked: `openai_agents` seed filters `validity_status == "valid"`; materializer rejects non-valid.
4. Positive path: materialization suite inside 96-pass includes selected-manifest real wiring (no fixture injection).
5. No fake-green: zero_strict_valid continues search (prior audit path hold).
6. mixed_acquisition honored (`review_mixed` paths in validity); no immuno PXD hardcoding observed.

### done criteria
- gate exit 0: **YES**
- materialize fail-closed / weak not packageable: **YES**
- legal strict-valid materialize proven or GATE_OK: **YES**

**Verdict: DONE:true**

