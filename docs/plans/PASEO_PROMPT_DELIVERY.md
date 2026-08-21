# Paseo Windows：多行 prompt 必须可靠送达

Date: 2026-07-22  
Scope: Orchestrator / Phase 1+（不改产品 `src/`）

## 事故

`paseo run ... "<multiline prompt>"` 在 Windows（`paseo.cmd` + 命令行参数）上会把 prompt **截成第一行**。  
症状：agent 立刻 idle，日志只有首句，回复类似 `Ready. Send the planning objective…`。

## 铁律（违反即 FAIL）

1. **禁止**把多行 mission 当作 `paseo run` 的位置参数 `<prompt>` 直接塞完整正文。
2. **必须**用下面之一：
   - **推荐**：`docs/plans/_paseo_run_with_prompt_file.ps1`  
     bootstrap `run`（单行）→ `send --prompt-file`（完整 UTF-8）→ **日志指纹校验**。
   - 已有 agent：`paseo send --no-wait --prompt-file <path> <agentId>`
3. 创建后 **必须** 用 `paseo logs <id>` 确认指纹（如 `PLAN_STATUS` / `Mission:`）出现在日志里，再汇报「已启动」。
4. PowerShell 里展开 `$prompt` 作位置参数也会踩坑；优先 `--prompt-file`。

## 一键创建（示例）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  docs\plans\_paseo_run_with_prompt_file.ps1 `
  -PromptFile docs\plans\_codex_phase1_prompt.txt `
  -Provider codex/gpt-5.6-sol `
  -Thinking high `
  -Mode auto `
  -Cwd E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning `
  -Title "Phase1 plan Codex gpt-5.6-sol high" `
  -Label role=planning,phase=1 `
  -Fingerprint PLAN_STATUS
```

## 跟进已有 agent

```bat
E:\paseo\resources\bin\paseo.cmd send --json --no-wait --prompt-file path\to\mission.txt <agentId>
E:\paseo\resources\bin\paseo.cmd logs <agentId>
```

## 相关脚本

| 文件 | 用途 |
| --- | --- |
| `_paseo_run_with_prompt_file.ps1` | 可靠创建 + 校验 |
| `_codex_phase1_prompt.txt` | Phase 1 Codex mission（全文） |
| `_run_codex_phase1.ps1` | 已改为调用可靠包装器 |
| `_send_codex_phase1.ps1` | 已改为 `--prompt-file` only |
