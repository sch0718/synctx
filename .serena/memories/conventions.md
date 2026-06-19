# Conventions

## Document workflow
- User edits docs externally → asks "검토해줘" → review feedback → user fixes → "커밋해줘" → occasionally "푸시해줘"
- Reviews check: cross-document consistency, completeness, security, naming accuracy
- Review severity: Critical / Warning / Suggestion

## Mermaid diagrams
- No `%%{init:...}%%` or `classDef` in Mermaid blocks
- README uses subgraphs (External/Internal), architecture.md uses flat nodes
- Actor order in sequence diagrams: left→right = user → FE → BE → Engine → DB

## Naming
- Korean for doc prose and comments
- English for technical identifiers, config keys, CLI commands
- Commit messages: Conventional Commits in Korean (`docs: 설명`)
- Config file: `.synctx.yaml` (dot-prefixed)
- External key format: `{repo_id}:{normalized_path}`

## Front Matter
- YAML block with `synctx:` namespace for routing metadata
- `type`: task | doc | report | meeting | decision | version
- `route`: openproject | xwiki | both

## Cross-document consistency rules
- Service additions must update: README (diagram + table + project structure), architecture.md (diagram + table + module), production-service-design.md (config + API + security + metrics + alerts + tests + phase checklist)
- New secrets → 3 places: secrets table, .synctx.yaml schema, runbook
- New roles → 2 places: roles table, authz config example
