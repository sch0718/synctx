# Tech Stack (Planned)

## Backend
- Python 3.12+ — Viewer, CDN, Marp (FastAPI wrapper), Sync Engine, Feedback Exporter
- FastAPI — web services
- httpx — async HTTP client (XWiki/OpenProject REST API)
- python-markdown — server-side Markdown rendering
- Node.js — Marp CLI (@marp-team/marp-cli) subprocess

## Frontend (Phase 1-4)
- Vanilla JS/CSS only. No framework.
- CDN-served: mermaid 10.9.1, highlight.js, Chart.js 4.4.4
- Phase 6+ scrum board: React/Vite (only for high-interaction features)

## Infrastructure
- K8s (Minikube for local dev)
- Helm Charts
- Nginx — Slides Server static serving
- jgraph/drawio — Draw.io (Tomcat-based Docker image)

## Key constraints
- Air-gapped: all dependencies vendored, no external CDN/registry at runtime
- Images mirrored to internal registry with SemVer tags
- NFS StorageClass recommended for RWX PVC
