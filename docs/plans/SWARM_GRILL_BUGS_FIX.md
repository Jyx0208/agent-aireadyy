# 蜂群：Grill 该问不问 + 下一问丢失 + 澄清空洞

## 用户要求
修完下列全部 bug 再停：

### P0-C 物种 agenda 消失
- 根因：generalization_scope 触发 AND species_policy missing；默认 open 使物种不问
- 修：只靠 species 空触发（task_profiles 已改一版，验证+测）

### P0-A 下一问结构不完整被整段丢弃
- 写卡成功后 next_decision 校验失败 → 中文「下一问结构不完整已忽略」+ 不再问
- 修：update_strategy 成功且仍有 critical agenda 时，服务端合成完整 next_decision（最高 priority critical 项），或重试规范化；禁止留下“只写卡不问”

### P1-B 「什么意思」空回复
- 系统插入 contract 文案时，用户问澄清应解释 + 补下一关键问
- 修：detect 用户澄清 + 最近 assistant 含 contract 句时，注入 manager 提示；assistant 文案改友好

### P1 Grill 策略
- 禁止用「仓库会告诉我们」跳过 species/scale/acquisition（training）
- 开放物种必须用户明确选 open

### P1 文案
- 不要把内部 contract 英文/半截中文甩给用户

### P2（尽量做）
- 数字选项失败时允许自然语言续

## 聊天室
`grill-bugs-fix`

## 所有权
| 角色 | 文件 | 任务 |
|------|------|------|
| S | 验收 | pytest + live 路径说明 |
| A | task_profiles/agenda | 物种 trigger + 单测 agenda |
| B | app.py next_decision fallback | 合成/修复 next_decision |
| C | app.py 文案+澄清 | 友好错误 + 什么意思 |
| D | 测试+部署 | tests + local/lab restart |

## 成功
1. denovo+dda 后 agenda 含 generalization_scope
2. update_strategy + 坏 next_decision → 仍有完整下一问（或服务端合成）
3. 用户不看到「下一问结构不完整已明确忽略」原文
4. pytest 绿；部署本机+lab
