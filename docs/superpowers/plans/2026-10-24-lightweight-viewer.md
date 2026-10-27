# Lightweight Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing Markdown/meeting Viewer with bundled assets through one command or one Docker container.

**Architecture:** Keep the existing Viewer app and HTML exporter. A small standalone CLI mounts Viewer at `/view`, generates minimal configuration when a Git URL is supplied, and owns the existing Viewer lifespan plus periodic Git refresh. Both launchers execute this same Python entrypoint.

**Tech Stack:** Existing Python >=3.13, FastAPI/Starlette, uv, Git, Bash, Docker and pytest/node tests.

## Global Constraints

- Same repository, branch `codex/lightweight-viewer`; preserve existing Helm, Compose and local Marp defaults.
- `asset_source` defaults to `cdn`; lightweight startup explicitly uses `bundled` and `SYNCTX_ENABLED_SERVICES=viewer`.
- Use existing `services/viewer/src/static/vendor/` files for browser assets and existing HTML export; add no copied library files.
- No new service, database, video SDK, iframe integration, meeting schema change or external runtime CDN dependency.
- Meeting platforms remain ordinary HTTPS Markdown links; existing meeting metadata and localStorage behavior remain unchanged.
- Host startup defaults to `127.0.0.1:18000`; Docker publishes to host loopback and serves inside the container on `0.0.0.0:8000`.
- Accept repeated `--repo` or `--config`, not both. Keep explicitly supplied authz settings; never persist token values in generated YAML.
- Resolve local repository paths before deriving cache IDs. Public URL precedence: `--public-base-url`, explicit config URL, Docker `SYNCTX_VIEWER_PUBLIC_BASE_URL`, then bind-derived local URL.
- Isolate generated runtime configuration by effective content so concurrent viewers sharing a cache cannot overwrite each other's startup settings.
- Preserve Python base image `python:3.13.16-slim-trixie`; image tags use `0.6.0-<git-sha>`, never `latest`.
- All image builds go through `scripts/local/steps/build.sh`; no direct Docker build or kubectl commands.
- Use existing async Git methods; do not run blocking subprocesses on the event loop.
- Test-first for runtime and launcher behavior; keep tests focused on observable behavior.

---

### Task 1: Standalone Viewer and local one-command launch

**Files:**
- Modify: `shared/synctx-common/src/synctx_common/config.py`, `services/viewer/src/app.py`
- Create: `services/viewer/src/standalone.py`, `scripts/local/run-lite.sh`, `run.sh`
- Test: `services/viewer/tests/test_standalone.py`, `services/viewer/tests/test_service_options.py`, `scripts/tests/test_run_lite.py`

**Interfaces:**
- `ViewerConfig.asset_source: Literal['cdn', 'bundled'] = 'cdn'` selects browser assets.
- CLI: `--repo` (repeatable), `--config`, `--ref` (main), `--host` (127.0.0.1), `--port` (18000), `--cache-dir`, `--refresh-interval` (60; 0 disables).
- Optional `--public-base-url` preserves export/source links behind proxies; the Docker publication URL is an environment fallback rather than an override of explicit config.
- Root `run.sh` passes local arguments to `scripts/local/run-lite.sh`; first argument `--docker` delegates to `scripts/docker/run-viewer.sh` (Task 2).
- Local launcher uses `uv run --frozen --project <root> --package synctx-viewer --no-dev python <standalone.py>` and preserves the caller's working directory.
- Standalone app mounts existing Viewer at `/view`; root redirects to `/view/`; health and static URLs remain under `/view`.

- [x] Step 1: Add failing API/config tests: bundled mode works without CDN, normal CDN checks remain, HTML references bundled Mermaid/highlight/styles and the assets return 200.
- [x] Step 2: Run `uv run --frozen --all-packages pytest services/viewer/tests/test_standalone.py services/viewer/tests/test_service_options.py -q` and record the missing-feature failures.
- [x] Step 3: Add asset_source and select the existing files in Viewer; CDN mode keeps its current URLs. Implement standalone CLI using generated config and a wrapper lifespan; avoid loading configuration before CLI environment setup.
- [x] Step 4: Test repeated repo naming, token environment references without token contents, config preservation, ambiguous arguments, valid ports, nonnegative refresh interval and shutdown. Use a local Git fixture for a real clone/render/export check.
- [x] Step 5: Add root/local shell launcher and tests that verify pass-through arguments and selected package without running unrelated services.
- [x] Step 6: Run the focused suites and existing Viewer/renderer tests, self-review, and commit with `feat(viewer): add standalone document and meeting launcher`.

### Task 2: Single-container launcher and installation documentation

**Files:**
- Create: `scripts/docker/run-viewer.sh`, `scripts/tests/test_run_viewer_docker.py`
- Modify: `README.md`, `docs/architecture.md`, `docs/production-service-design.md`, `docs/local-development.md`, `scripts/README.md`
- Modify Task 1 launcher tests only where needed for Docker dispatch.

**Interfaces:**
- `./run.sh --docker --repo <url>` runs one existing Viewer image; `--build` delegates to existing local build script; `--image <tagged-image>` selects a prebuilt image.
- Docker arguments `--port`, `--bind`, `--name` and `--volume` control host publication and cache. Remaining standalone arguments retain their meanings; `--config` is mounted read-only at an internal path.
- Pass Git/service tokens through Docker environment by variable name, not by exposing values in CLI arguments.
- Set `SYNCTX_VIEWER_PUBLIC_BASE_URL` from the host bind/publication port; pass an explicit `--public-base-url` unchanged to the app when supplied.
- `docker run --rm` executes the same standalone.py, internal port 8000 and cache `/data/viewer`; no Compose/Gateway/CDN is involved.
- Default local image tag derives `0.6.0-<git-sha>`; `--build` uses `scripts/local/steps/build.sh --only viewer --no-load --tag <tag>` with the matching registry prefix.

- [x] Step 1: Add failing wrapper tests with a recording fake Docker executable: exactly one run, one port/volume, preserved repo argument, read-only config mount, invalid/missing arguments and image tags rejected, build delegated.
- [x] Step 2: Run `uv run --frozen --all-packages pytest scripts/tests/test_run_viewer_docker.py -q` and record expected failures before adding the wrapper.
- [x] Step 3: Implement the Bash wrapper using arrays and safe path handling; foreground run handles Ctrl-C through exec and Docker `--rm` cleanup.
- [x] Step 4: Update all required documents with actual commands, prerequisites, cache/token behavior, meeting links, unchanged browser-local notes, bundled assets, external access/authentication, and current test/build limitations. Describe lightweight as a deployment option, not a new service or Phase.
- [x] Step 5: Run wrapper tests plus existing Docker/local scripts suites; validate shell syntax. Commit with `feat(viewer): support single-container lightweight deployment`.

### Task 3: Integration verification and whole-branch review

**Files:** Only fixes justified by review/integration, and this plan's progress checkboxes.

- [x] Step 1: Execute `scripts/test-all.sh` once after all implementation changes; inspect Python and JavaScript totals.
- [x] Step 2: Start `./run.sh --repo <temporary-local-Git-repo>` on an available loopback port and confirm document rendering, bundled assets, search, meeting mode and HTML download through real HTTP. Stop and confirm the port closes; repeat startup to confirm cache reuse.
- [x] Step 3: If Docker is available, run `./run.sh --docker --build --repo <reachable-repo>` and verify the same endpoints. If the daemon/base image is unavailable, record the exact limitation and still verify Docker argument construction and build-script delegation.
- [x] Step 4: Review the entire branch against the design with requesting-code-review; fix actionable findings and repeat affected tests.
- [x] Step 5: Update progress, run `git diff --check`, leave the feature branch for the user, and report actual commands and verification results. Do not merge, publish, or create a PR without a subsequent request.


## Final verification (2026-10-24)

- `scripts/test-all.sh`: Python 1,509 passed, 35 skipped; JavaScript 89 passed. One existing Starlette/httpx deprecation warning remains. Exit 0.
- Source launcher: actual local Git clone/render/search, Mermaid/code highlighting, meeting/evidence/presentation, generic HTML download and self-contained meeting HTML export verified through HTTP and browser. Periodic refresh, shutdown and cache reuse passed.
- Single container: final runtime image `synctx/viewer:0.6.0-f47b010` built through `scripts/local/steps/build.sh --only viewer --no-load`. Actual wrapper/HTTP checks passed for read-only local paths and escaped-space file URLs, bundled assets, meeting export, Git polling, loopback publication and public URLs.
- Same-volume main → release restart retained repository ID and rendered the new branch. Real Git regressions also cover both fetch paths, four storage modes and force updates. Explicit destination refspecs fix single-branch cache reuse without duplicating caches.
- Whole-branch and scoped follow-up reviews: requirements and quality passed, no remaining Critical/Warning/Suggestion findings.
- Temporary smoke servers, containers and test cache volumes removed. `git diff --check` clean. Keep `codex/lightweight-viewer` locally as requested.
