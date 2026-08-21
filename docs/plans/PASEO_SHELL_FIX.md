# Fix: Paseo/Pi agent bash on this machine

Date: 2026-07-22

## Root cause (not “need WSL”)

Pi coding agents on Windows **require a bash.exe**. Resolution order
(from pi `docs/windows.md`):

1. `~/.pi/agent/settings.json` → `shellPath`
2. `C:\Program Files\Git\bin\bash.exe`
3. `bash.exe` on PATH (Cygwin / MSYS2 / **WSL**)

On this PC:

- No Git Bash at the default `Program Files` path
- No usable WSL bash (`wsl ... /bin/bash` fails)
- Git **does** exist at `E:\Git\` (Git for Windows style tree)

So the agent fell through to WSL and every `bash` tool call died.

## Applied fix (config)

`C:\Users\28425\.pi\agent\settings.json` now includes:

```json
{
  "shellPath": "E:\\Git\\usr\\bin\\bash.exe"
}
```

Use **`E:\\Git\\bin\\bash.exe`** (Git's wrapper). Direct `usr\\bin\\bash.exe` can start without a proper MSYS PATH, so `uname`/`ls`/`which` go missing even though `echo` works.

Also present:

- `C:\Users\28425\.paseo\orchestration-preferences.json` → Codex `gpt-5.6-sol` only

## What you must do (Paseo desktop)

1. **Archive or close this agent chat** (settings are read at session start).
2. **Open a new agent** in Paseo desktop (same project/worktree is fine).
3. In the new session, ask it to run: `echo SHELL_OK && uname -a`
   - Expect something like `MINGW64_NT` / MSYS, **not** a WSL error.
4. Then: `C:/Users/28425/.local/bin/paseo.cmd ls` (or `paseo ls` if on PATH).
5. To discuss the architecture plan with Codex, **New Agent → Codex / gpt-5.6-sol high**, cwd =
   `E:\ai-agent-already\github-publish\agent-aireadyy\.claude\worktrees\benchmark-review-planning`,
   prompt = contents of `docs/plans/CODEX_PLANNING_BRIEF.md`.

## If bash still goes to WSL after restart

Something is forcing `wsl.exe` outside pi’s `shellPath` (rare). Then:

1. Confirm in PowerShell: `& 'E:\Git\usr\bin\bash.exe' -lc "echo ok"`
2. Install Git for Windows to the default path **or** add `E:\Git\usr\bin` to user PATH.
3. Do **not** restart the Paseo daemon unless necessary (kills all agents).

## create_agent tools

Even with bash fixed, **spawning sibling agents** needs either:

- Paseo UI “New Agent”, or
- MCP/`create_agent` tools injected into that agent session

`daemon.mcp.injectIntoAgents: true` is already set in `~/.paseo/config.json`.
If the model still has no `create_agent` tool after a fresh session, use the UI
to launch Codex; bash fix still unblocks `paseo` CLI from inside the agent.

## Upstream reference

- Pi Windows docs: shellPath + Git Bash
- Paseo: https://github.com/getpaseo/paseo (desktop + daemon client architecture)
