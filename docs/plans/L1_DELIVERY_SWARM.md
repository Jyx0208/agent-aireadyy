# L1 交付蜂群（可用文件列表 → 批量参数规划）

**目标（用户定案）：**
- 成功 = **L1 可用文件列表**（valid + weak_keep，可下载、可送入批量）
- **不要**用「质量闸门未通过」吓人；说明不足 + 交付清单
- 一键 **送入批量参数规划**，做参数推断 / 标准化格式
- L2 build-ready 仅作参考，不挡 L1

**工作树：**  
`E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning`

**聊天室：** `l1-delivery-swarm`  
协议：`CLAIM 文件` → 改 → `DONE 角色 摘要` → 阻塞 `BLOCKED`

## 文件所有权（禁止抢改）

| 角色 | 只改 | 任务 |
|------|------|------|
| **S 监督** | 只读 + chat post + 跑测 | 验收 DONE、冲突仲裁、不写产品大改 |
| **A UI 文案/结果** | `DiscoveryContextRail.tsx` `DiscoveryProgressMessage.tsx` `grill-tree.ts` 相关文案 | 修测试；blocked→L1 文案；下载+送入批量 |
| **B Batch 接线** | `BatchPanel.tsx` `App.tsx` | initialInputs 预填；送入批量流畅 |
| **C 后端下载** | `src/agent/web/app.py` download 路径；`manifest.py` batch 行格式 | candidate_pool 回落；URL 行 |
| **D 测试修绿** | `*.test.tsx` `*.test.ts` 前端发现相关 | npm test 相关套件绿 |
| **E 构建重启** | scripts + static build | `npm run build`；重启 8000；验证 download 200 |

## 验收（监督勾）

1. `frontend` 相关 vitest 绿（或仅剩无关失败写明）
2. `curl` `/api/discovery/agents_20260723_132129_2f2c0e/download?file=batch_inputs_usable` → 200
3. 静态 `index-*.js` 含「可用文件」或「送入批量」
4. chat 各 DONE 齐
