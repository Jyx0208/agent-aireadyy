# 蜂群：本机 compound 写卡修稳

## 问题
Lab live 复合句可写 10+ 字段；本机常只 soft-keep 3 字段（objective/task/themes），species/DDA/horizon 丢失。

## 根因（监督+编排）
1. Manager 有时只吐 soft 字段 → soft-reject 丢硬字段  
2. `tool_interpretation_difference` / 扩字段后 **超 8 字段或非白名单** 导致 low-risk skip 失败 → 又走 critic  
3. 确定性 compound hints 已加，但 merge 后可能含 `species_coverage` 等，或 len>8 触发 skip=False

## 目标
- 复合句 `人源免疫肽，RT 预测，越多越好，DDA，审查候选` 本机 **稳定 ≥6 字段** 写入  
- 不发明 RT（仅免疫肽不写 task_type）  
- pytest low_risk/compound 绿  
- 本机 + lab 部署  

## 聊天室
`local-compound-fix`

## 所有权
| 角色 | 文件 | 任务 |
|------|------|------|
| S | 只读+测 | 验收 |
| A | app.py skip+merge | skip 在 compound 补全后仍为 True；白名单含 species_coverage 或 merge 只写白名单字段；MAX_FIELDS 提到 12 |
| B | app.py 写卡顺序 | merge 后 patch=filled；强制 skip 路径用 verification_input_patch |
| C | tests | compound skip + hints unit；修失败断言 |
| D | 部署 live | 本机 restart + lab scp；POST 计时 |

## 成功
local POST compound field_count≥6, next_decision 可选 null, verifier 不整卡清空