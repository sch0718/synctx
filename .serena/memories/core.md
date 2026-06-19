# synctx — Project Core

## Status
Design documentation phase. No implementation code yet. All deliverables are Markdown docs.

## What it is
Microservice platform for synchronizing AI-generated Markdown content into air-gapped (폐쇄망) financial environment tools (XWiki, OpenProject). Runs on K8s with Helm Charts.

## Key directories
- `README.md` — project overview, architecture diagram (Mermaid), service table, project structure
- `docs/architecture.md` — detailed architecture: 13+ sections, per-service design, module structure, Helm chart, phases
- `docs/production-service-design.md` — operational design: security, config schema (.synctx.yaml), API specs, deployment, observability, runbook, phase checklists
- `docs/reusable-assets.md` — catalog of reusable code/assets from existing repos (markdown_converter, example-infra/fe)
- `docs/xwiki-api-reference.md` — XWiki REST API reference for Sync Engine implementation

## 6+1+1 Services
Viewer (FastAPI), CDN (FastAPI+StaticFiles), Marp (FastAPI+Node.js), Draw.io (jgraph/drawio), Sync Engine (Python CLI CronJob), Slides Server (Nginx), Sync Webhook Receiver (Deployment), Outbound Feedback Exporter (optional, Phase 7)

## Cross-document invariants
- All 3 main docs (README, architecture, production-service-design) must stay consistent on: service list, Mermaid diagrams, config schema, phase numbering, role/secret tables
- Phase order: 1-4 core → 5 운영 준비 → 6 하네스/스크럼 → 7 Outbound Feedback (선택)
- Unidirectional sync: Git is source of truth, XWiki/OpenProject are derived data
- Image tags: SemVer `"0.1.0-<git-sha>"`, never `latest`

## Related memories
- Planned technology choices and version pins: `mem:tech_stack`
- Document writing conventions and review workflow: `mem:conventions`
- Commands for common operations: `mem:suggested_commands`
- Steps to verify task completion: `mem:task_completion`
