# Suggested Commands

## Git (this project)
- `git diff --stat` — check scope of uncommitted changes
- `git diff <file>` — review specific file changes
- `GITHUB_TOKEN= git push -u origin main` — push (clear invalid GITHUB_TOKEN env var to use keyring)
- `gh auth status` — verify GitHub CLI auth (keyring-based, account: sch0718)

## Notes
- `GITHUB_TOKEN` env var may contain an invalid token; prefix git/gh commands with `GITHUB_TOKEN=` to fall back to keyring credentials
- No build/test/lint commands yet (design phase, no code)
