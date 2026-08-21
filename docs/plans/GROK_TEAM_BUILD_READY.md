# Grok 4.5 分工板 — build-ready 证据策略（用户：全部按默认 + 多 agent）

**时间窗：** ALL-GROK（`pi/relay/grok-4.5`）  
**批准：** Q1–Q6 全按委员会默认  
**工作树：** `E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning`  
**政策摘要：** SDRF 最准 → 项目描述可认 → 文件名仅佐证 → 无证据 exclude 并继续搜 → weak_keep 不进 materialize → 防死循环 → 禁止假绿 / 禁止 immuno 特例  

## 角色与文件所有权（禁止越界改别人的文件）

| 角色 | 负责文件 | 不做 |
|------|----------|------|
| **A validity** | `src/agent/discovery/validity.py`；`tests/test_discovery_evidence_priority_policy.py`（新建） | 不改 control_plane、不改 publication |
| **B scoring** | `src/agent/discovery/scoring.py`（项目方法有条件继承到文件）；必要时 `features.py` 只读引用 | 不改 validity 状态机（A 负责） |
| **C search/audit** | `src/agent/control_plane/discovery.py`：quality 在 0 strict-valid 时 search_more；no-progress 看 strict-valid/build-ready 增量；`qualified_no_gain` 不单独当毕业 | 不改 materialize 允许 weak_keep |
| **D auditor** | 只读审查 + 写 `docs/plans/_GROK_WAVE_A_REVIEW.md`；跑相关 pytest | 不直接改实现，除非修测试误伤 |

## 默认拍板（已批准）

1. weak_keep **禁止**进 BuildReadyPackage / materialize（publication 保持 `valid` only）  
2. 项目级 instrument/fragmentation/acquisition：单一 assay、无混合、无文件反证时可继承到文件  
3. 文件名 **仅佐证**，不能单独定关键科学维度  
4. 同策略 2 轮无进展、最多 3 种查询假设（C 在注释+可配置常量落地，若缺配置则先写常量）  
5. DDA 类任务 instrument+fragmentation 为 hard → 缺则 **不能 valid**（可为 weak_keep 若有领域证据）  
6. converted peaklist：不全局一刀切；保留 soft reason，不单独因 peaklist 打 needs_review  

## Wave A 验收

- 项目级免疫肽 + 有 download → 文件 **weak_keep**（非 needs_review）  
- SDRF matched + 无冲突 → **valid**  
- 无领域且无仪器/碎裂 → **exclude**  
- materialize 仍拒绝 weak_keep  
- `pytest tests/test_discovery_evidence_priority_policy.py tests/test_discovery.py -q --tb=line` 相关绿  
- 无 immuno 硬编码 accession  

## 通信

完成后在本文件追加 `## DONE <role>` 与摘要；D 汇总 PASS/FAIL。


## DONE A validity
- validity.py：项目级领域 soft；SDRF matched→valid；无领域+无方法→exclude；文件名仅佐证。
- tests/test_discovery_evidence_priority_policy.py

## DONE B scoring
- scoring.py：无 mixed 时继承项目 instrument/fragmentation；source=project_method_inherited

## DONE C search
- discovery.py：qualified>0 且 strict_valid_files==0 且 can_continue → search_more

## DONE D auditor（编排收口）
- Grok 四人 idle 但文档不全；编排补测试与 C；静态资源误删已恢复
