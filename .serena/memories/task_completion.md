# Task Completion

## Document review tasks
1. Read all changed files via `git diff`
2. Check cross-document consistency (service list, diagrams, config, phases, roles, secrets, metrics, alerts)
3. Report findings as Critical / Warning / Suggestion
4. After user fixes, re-read diff to verify

## Commit tasks
1. `git status` + `git diff --stat` + `git log --oneline -5`
2. Stage specific files (not `git add -A`)
3. Conventional Commits format: `docs: <Korean description>`
4. Include `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`

## No code tasks yet
- No linter, formatter, test runner, or type checker to run (design phase)
