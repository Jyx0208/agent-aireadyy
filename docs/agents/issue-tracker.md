# Issue Tracker: GitHub

Issues and planning requests for this repository live in GitHub Issues at `Jyx0208/agent-aireadyy`. Use the `gh` CLI from this clone so it infers the repository from `git remote`.

## Conventions

- Create: `gh issue create --title "..." --body "..."`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open --json number,title,body,labels,comments`
- Comment: `gh issue comment <number> --body "..."`
- Label: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

When a skill says to publish to the issue tracker, create a GitHub issue. When it asks for the relevant ticket, read the matching GitHub issue and its comments.

## Pull Requests as a Triage Surface

PRs as a request surface: **no**. Pull requests are not included in the issue triage queue unless this setting is deliberately changed later.

## Wayfinding

A wayfinding map is a GitHub issue labeled `wayfinder:map`; child work is represented by GitHub sub-issues when available. Use native issue dependencies for blocking relationships, and claim work by assigning the selected issue before making tracker changes.
