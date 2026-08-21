# Provider 时间表（用户指定）

## 今晚（夜间）
- **全部**：`pi/relay/grok-4.5`（thinking high）
- **不用 Codex**
- 配置：`%USERPROFILE%\.paseo\orchestration-preferences.json`
- 备份（白天 Codex）：`orchestration-preferences.json.bak-codex-day`

## 明天白天
恢复 Codex：

```powershell
Copy-Item $env:USERPROFILE\.paseo\orchestration-preferences.json.bak-codex-day `
  $env:USERPROFILE\.paseo\orchestration-preferences.json -Force
```

或手动把各 role 改回：`codex/gpt-5.6-sol`

然后停 Grok loop、按需开 Codex 实现。

## 当前夜间 loop
见 `paseo loop ls`；名称含 `grok-night`。
