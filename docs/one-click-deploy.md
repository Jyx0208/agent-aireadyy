# One-click remote deploy

Use this from Windows PowerShell after local changes are ready:

```powershell
.\scripts\deploy.ps1 -CommitMessage "fix: describe the change"
```

Default target:

- Git remote: `origin`
- Branch: `main`
- Server: `admin@47.253.243.164`
- Server path: `/opt/pride-agent`

The script does:

1. `git add -A`
2. `git commit -m ...`
3. `git push origin HEAD:main`
4. SSH to the server
5. `git pull --ff-only origin main`
6. `sudo docker compose build web`
7. `sudo docker compose up -d`

For a clean rebuild:

```powershell
.\scripts\deploy.ps1 -CommitMessage "fix: describe the change" -NoCache
```

Override the server if needed:

```powershell
.\scripts\deploy.ps1 `
  -ServerHost 47.253.243.164 `
  -ServerUser admin `
  -ServerPath /opt/pride-agent `
  -Branch main `
  -CommitMessage "fix: describe the change"
```

The script does not store API keys, SSH passwords, or GitHub tokens. SSH and GitHub authentication must already work in your terminal.
