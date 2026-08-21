# Grok 并行蜂群 — 稳定交付

**主循环：** `a9f55430` stable-delivery-build-ready（Worker+Verifier）  
**专职测试：** StableDelivery Independent Tester  

## 并行角色（文件所有权，禁止抢改）

| 角色 | 只改这些 | 任务 |
|------|----------|------|
| **P1 mixed** | `src/agent/discovery/validity.py` + `tests/test_discovery_mixed_acquisition_policy.py` | 修好 mixed_acquisition 与新状态机一致性，pytest 该文件绿 |
| **P2 materialize** | `src/agent/control_plane/openai_agents.py` 和/或 publication 播种路径；`tests/test_discovery_build_ready_materialization.py` | 真实 run 路径播种 evidence/membership/builder，使 materialize 测试过；**禁止** weak_keep 进 package |
| **P3 discovery meta** | `src/agent/discovery/scoring.py` 或 `features.py`（仪器提取）；`tests/test_discovery.py` 中失败用例 | 修 instrument/fragmentation 提取期望冲突 |
| **P4 gate runner** | 只跑测、写 `docs/plans/STABLE_DELIVERY_LOOP.md` | 反复跑 `scripts/run_stable_delivery_gate.ps1`，报告红绿 |

政策：SDRF>项目描述>文件名；weak_keep 不进 build-ready；无 immuno 特例。
