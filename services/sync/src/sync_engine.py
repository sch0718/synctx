"""Sync Engine 코어 오케스트레이션."""

import asyncio
import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from synctx_common.config import SynctxConfig, RepoProfile, TargetRetryConfig
from synctx_common.git_manager import GitRepoManager
from synctx_common.observability import (
    sync_runs_total,
    sync_items_total,
    sync_failures_total,
    sync_last_success_timestamp,
)
from state import StateManager, RepoState
from lease import repo_lock
from targets.xwiki import XWikiClient
from targets.openproject import OpenProjectClient
from urllib.parse import quote

from synctx_common.html_bundle import repo_reader
from synctx_common.observability import html_bundle_total
from embed_sync import (
    MarkdownAnalysis,
    analyze,
    effective_sha,
    policy_fingerprint,
    publisher_marker,
    render_for_openproject,
    render_for_xwiki,
)
from synctx_common.frontmatter import parse_frontmatter

logger = logging.getLogger(__name__)


class SyncError(Exception):
    """Sync Engine 에러 with 분류."""

    def __init__(self, message: str, error_type: str):
        super().__init__(message)
        self.error_type = error_type


class SyncReport:
    """동기화 결과 리포트."""

    def __init__(self):
        self.success: List[str] = []
        self.failed: List[Dict[str, Any]] = []
        self.skipped: List[str] = []
        self.html_errors: List[Dict[str, Any]] = []

    @property
    def success_count(self) -> int:
        return len(self.success)

    @property
    def fail_count(self) -> int:
        return len(self.failed)

    def record_success(self, path: str) -> None:
        self.success.append(path)

    def record_failure(self, path: str, error: str, target: str, error_type: str = "transient_error") -> None:
        self.failed.append({"path": path, "error": error, "target": target, "error_type": error_type})

    def record_skip(self, path: str) -> None:
        self.skipped.append(path)

    def record_html_error(self, path: str, status: str, message: str) -> None:
        self.html_errors.append({"path": path, "status": status, "message": message})

    def summary(self) -> Dict[str, Any]:
        return {
            "success": self.success_count,
            "failed": self.fail_count,
            "skipped": len(self.skipped),
            "failures": self.failed,
            "html_errors": self.html_errors,
        }


def classify_error(e: Exception) -> str:
    """에러를 설계 문서 5가지 분류로 구분."""
    msg = str(e).lower()

    if isinstance(e, SyncError):
        return e.error_type

    if "401" in msg or "403" in msg or "unauthorized" in msg or "forbidden" in msg:
        return "auth_error"
    if "429" in msg or "5" in msg[:1] and msg[1:4].isdigit() or "timeout" in msg or "connection" in msg:
        return "transient_error"
    if "duplicate" in msg or "conflict" in msg or "409" in msg:
        return "conflict_error"
    if "validation" in msg or "invalid" in msg or "frontmatter" in msg:
        return "validation_error"
    if "mapping" in msg or "unknown type" in msg or "unknown status" in msg:
        return "mapping_error"

    return "transient_error"


def should_retry(error_type: str) -> bool:
    """에러 분류에 따라 재시도 여부 결정."""
    return error_type == "transient_error"


def can_advance_state(error_type: str) -> bool:
    """에러 발생 시에도 state를 진행할 수 있는지 판단."""
    return error_type == "validation_error"


class SyncEngine:
    def __init__(self, config: SynctxConfig):
        self.config = config
        self.state_manager = StateManager(config.sync.state_path)

        self.xwiki_client = None
        if config.targets.xwiki:
            self.xwiki_client = XWikiClient(config.targets.xwiki)

        self.op_client = None
        if config.targets.openproject:
            self.op_client = OpenProjectClient(config.targets.openproject)
        self._analysis_cache: Dict[Tuple[str, str, str], MarkdownAnalysis] = {}
        self._analysis_by_path: Dict[Tuple[str, str], MarkdownAnalysis] = {}
        self._repo_xwiki_clients: Dict[str, XWikiClient] = {}

    async def close(self):
        if self.xwiki_client:
            await self.xwiki_client.close()
        for client in self._repo_xwiki_clients.values():
            await client.close()
        if self.op_client:
            await self.op_client.close()

    async def run_sync(self, repo: RepoProfile, dry_run: bool = False) -> Optional[SyncReport]:
        """단일 저장소에 대한 동기화 실행."""
        identity = os.getenv("HOSTNAME", "sync-engine-local")
        run_id = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())

        with repo_lock(self.config.sync.lease_namespace, repo.id, identity) as acquired:
            if not acquired:
                logger.info(f"Could not acquire lock for {repo.id}, skipping sync.")
                return None

            logger.info(f"Starting sync for {repo.id} (dry_run={dry_run}, run_id={run_id})")
            self._analysis_cache.clear()
            self._analysis_by_path.clear()

            cache_dir = os.getenv("REPO_CACHE_PATH", "/data/repos")
            git_manager = GitRepoManager(repo, cache_dir)
            await git_manager.ensure()
            if git_manager.status != "ready":
                sync_runs_total.labels(repo_id=repo.id, result="error").inc()
                raise RuntimeError(f"Git repository is not ready: {git_manager.last_error or repo.id}")
            head_sha = git_manager.head_sha_value

            state = self.state_manager.get_state(repo.id)
            state.last_run_id = run_id
            last_sha = state.last_successful_sha
            fingerprint = policy_fingerprint(repo.html_embed)
            index_missing = state.html_dependencies is None
            policy_changed = state.html_policy_fingerprint != fingerprint
            publisher = self._xwiki_publisher(repo)
            publisher_changed = state.xwiki_publisher != publisher

            if last_sha == head_sha and not index_missing and not policy_changed and not publisher_changed:
                logger.info(f"Repo {repo.id} is up to date (SHA: {head_sha})")
                return None

            changes = [] if last_sha == head_sha else self._get_changes(git_manager, last_sha, repo)
            if index_missing or publisher_changed:
                changes = self._merge_changes(changes, self._get_all_files(git_manager))
            changes = self._expand_html_changes(changes, state, include_indexed=policy_changed and not index_missing)
            if not changes:
                logger.info(f"No relevant changes found for {repo.id}.")
                if not dry_run:
                    state.last_successful_sha = head_sha
                    self._mark_html_state(state, fingerprint)
                    state.xwiki_publisher = publisher
                    self.state_manager.save_state(repo.id, state)
                return None

            report = SyncReport()
            has_blocking_failure = False

            for status, path in changes:
                if not self._should_process(path, repo):
                    continue

                if dry_run:
                    logger.info(f"[DRY-RUN] Would process {status} {path}")
                    report.record_skip(path)
                    continue

                content_sha = self._compute_content_sha(git_manager, status, path, repo, state)
                if status != "D" and not self.state_manager.is_file_changed(repo.id, path, content_sha):
                    report.record_skip(path)
                    continue

                target, project_id = self._resolve_target(path)
                retry_cfg = self._get_retry_config(target)

                success, error_type = await self._process_with_retry(
                    repo, git_manager, status, path, target, project_id,
                    retry_cfg, report, state, head_sha, content_sha,
                )

                if not success:
                    sync_failures_total.labels(repo_id=repo.id, target=target, error_type=error_type).inc()
                    if not can_advance_state(error_type):
                        has_blocking_failure = True
                else:
                    sync_items_total.labels(repo_id=repo.id, target=target, action=status).inc()
                    self.state_manager.clear_pending_failure(state, path)
                    self._record_html_errors(report, repo, path)

            if not has_blocking_failure and report.fail_count == 0 and not dry_run:
                state.last_successful_sha = head_sha
                self._mark_html_state(state, fingerprint)
                state.xwiki_publisher = publisher
                sync_last_success_timestamp.labels(repo_id=repo.id).set(time.time())
                sync_runs_total.labels(repo_id=repo.id, result="success").inc()
                logger.info(f"Sync completed for {repo.id}. {report.summary()}")
            elif not dry_run:
                if not has_blocking_failure:
                    state.last_successful_sha = head_sha
                    self._mark_html_state(state, fingerprint)
                sync_runs_total.labels(repo_id=repo.id, result="partial" if not has_blocking_failure else "failed").inc()
                logger.warning(f"Sync completed with issues for {repo.id}. {report.summary()}")

            if not dry_run:
                self.state_manager.save_state(repo.id, state)

            return report
    def _get_changes(self, git_manager: GitRepoManager, last_sha: Optional[str], repo: RepoProfile) -> List[Tuple[str, str]]:
        if last_sha:
            try:
                return git_manager.diff_since(last_sha)
            except Exception as e:
                logger.warning(f"Failed to get diff since {last_sha}: {e}. Falling back to full rescan.")
                if self.config.sync.force_push_policy == "full_rescan":
                    return self._get_all_files(git_manager)
                raise
        return self._get_all_files(git_manager)

    def _should_process(self, path: str, repo: RepoProfile) -> bool:
        if not path.endswith(".md"):
            return False
        if repo.watch_paths and not any(path.startswith(wp.rstrip("*").rstrip("/")) for wp in repo.watch_paths):
            return False
        if any(path.startswith(ip) for ip in repo.ignore_paths):
            return False
        return True

    def _compute_content_sha(
        self,
        git_manager: GitRepoManager,
        status: str,
        path: str,
        repo: Optional[RepoProfile] = None,
        state: Optional[RepoState] = None,
    ) -> str:
        if status == "D":
            if state is not None and state.html_dependencies is not None:
                state.html_dependencies.pop(path, None)
            return ""
        try:
            content = git_manager.read_file_text(path)
        except Exception:
            return ""
        if repo is None or not path.endswith(".md"):
            return hashlib.sha256(content.encode()).hexdigest()
        _, body = parse_frontmatter(content)
        analysis = self._html_analysis(repo, git_manager, path, body)
        if state is not None:
            self._update_html_index(state, path, analysis)
        sha = effective_sha(content, analysis, policy_fingerprint(repo.html_embed))
        publisher = self._xwiki_publisher(repo)
        if publisher:
            sha = hashlib.sha256(f"{sha}\0{publisher}".encode()).hexdigest()
        return sha

    def _update_html_index(self, state: RepoState, path: str, analysis: MarkdownAnalysis) -> None:
        """게시 여부와 상관없이 합본을 만든 직후 색인을 갱신한다(참조만 바뀐 경우도 놓치지 않게)."""
        if state.html_dependencies is None:
            state.html_dependencies = {}
        deps = analysis.deps
        if deps:
            state.html_dependencies[path] = deps
        else:
            state.html_dependencies.pop(path, None)

    def _merge_changes(self, primary: List[Tuple[str, str]], extra: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        seen = {path for _, path in primary}
        return primary + [(status, path) for status, path in extra if path not in seen]

    def _expand_html_changes(self, changes: List[Tuple[str, str]], state: RepoState, include_indexed: bool) -> List[Tuple[str, str]]:
        index = state.html_dependencies or {}
        reverse: Dict[str, List[str]] = {}
        for md_path, deps in index.items():
            for dep in deps:
                reverse.setdefault(dep, []).append(md_path)
        extra = [("M", md_path) for _, path in changes for md_path in sorted(reverse.get(path, []))]
        if include_indexed:
            extra += [("M", md_path) for md_path in sorted(index)]
        result = list(changes)
        seen = {path for _, path in result}
        for status, path in extra:
            if path not in seen:
                result.append((status, path))
                seen.add(path)
        return result

    def _mark_html_state(self, state: RepoState, fingerprint: str) -> None:
        state.html_policy_fingerprint = fingerprint
        if state.html_dependencies is None:
            state.html_dependencies = {}

    def _record_html_errors(self, report: SyncReport, repo: RepoProfile, path: str) -> None:
        analysis = self._analysis_by_path.get((repo.id, path))
        if analysis is None:
            return
        for outcome in analysis.outcomes:
            if outcome.status not in ("ok", "disabled"):
                report.record_html_error(path, outcome.status, outcome.message)
    def _resolve_target(self, path: str) -> Tuple[str, str]:
        target = "xwiki"
        project_id = "1"
        for rule in self.config.mappings:
            if path.startswith(rule.match.path_prefix):
                target = rule.target
                if rule.openproject:
                    project_id = rule.openproject.project
                break
        return target, project_id

    def _get_retry_config(self, target: str) -> TargetRetryConfig:
        if target == "xwiki" and self.config.targets.xwiki:
            return self.config.targets.xwiki.retry
        return TargetRetryConfig()

    async def _process_with_retry(
        self,
        repo: RepoProfile,
        git_manager: GitRepoManager,
        status: str,
        path: str,
        target: str,
        project_id: str,
        retry_cfg: TargetRetryConfig,
        report: SyncReport,
        state: RepoState,
        head_sha: str,
        content_sha: str,
    ) -> Tuple[bool, str]:
        last_error = ""
        error_type = "transient_error"

        for attempt in range(retry_cfg.max_attempts):
            try:
                resource_id = await self._process_single_file(repo, git_manager, status, path, target, project_id)
                report.record_success(path)

                synced_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                external_key = f"{repo.id}:{path}"

                if status == "D":
                    self.state_manager.record_file_deleted(state, path)
                else:
                    self.state_manager.record_file_synced(
                        state, path, content_sha, target,
                        external_key, resource_id or "", head_sha, synced_at,
                    )
                return True, ""
            except Exception as e:
                last_error = str(e)
                error_type = classify_error(e)

                if not should_retry(error_type):
                    break

                if attempt < retry_cfg.max_attempts - 1:
                    backoff_idx = min(attempt, len(retry_cfg.backoff_seconds) - 1)
                    wait = retry_cfg.backoff_seconds[backoff_idx]
                    logger.warning(f"Retry {attempt + 1}/{retry_cfg.max_attempts} for {path} after {wait}s: {e}")
                    await asyncio.sleep(wait)

        report.record_failure(path, last_error, target, error_type)
        self.state_manager.add_pending_failure(state, path, error_type, last_error)
        logger.error(f"Failed {path}: [{error_type}] {last_error}")
        return False, error_type

    async def _process_single_file(
        self,
        repo: RepoProfile,
        git_manager: GitRepoManager,
        status: str,
        path: str,
        target: str,
        project_id: str,
    ) -> Optional[str]:
        """단일 파일 처리. 성공 시 resource_id 반환."""
        if status == "D":
            on_delete = self.config.sync.on_delete
            if on_delete == "ignore":
                logger.info(f"on_delete=ignore, skipping deleted file: {path}")
                return None

            if target == "xwiki" and self.xwiki_client:
                if on_delete == "delete":
                    space, page = self._derive_xwiki_target(repo, path)
                    await self._xwiki_client_for(repo).delete_page(space, page)
                else:
                    logger.info(f"on_delete=mark but XWiki has no mark_deleted, skipping: {path}")
            elif target == "openproject" and self.op_client:
                external_key = f"{repo.id}:{path}"
                if on_delete == "delete":
                    logger.info(f"on_delete=delete, but OP uses mark fallback: {path}")
                await self.op_client.mark_deleted(project_id, external_key)
            else:
                raise SyncError(f"No configured client for target={target}", "mapping_error")
            return None

        content = git_manager.read_file_text(path)
        from synctx_common.frontmatter import parse_frontmatter
        fm, body = parse_frontmatter(content)

        if fm:
            synctx_meta = fm.get("synctx", {})
            if isinstance(synctx_meta, dict) and synctx_meta.get("target"):
                explicit_target = synctx_meta["target"]
                if explicit_target in ("xwiki", "openproject", "both"):
                    target = explicit_target

        if target == "xwiki" and self.xwiki_client:
            space, page = self._derive_xwiki_target(repo, path)
            title = fm.get("title", page) if fm else page
            body = self._render_html_blocks_for_xwiki(repo, git_manager, path, body)
            client = self._xwiki_client_for(repo)
            body = await self._process_mermaid_blocks(space, page, body, client)
            await client.upsert_page(space, page, title, body + publisher_marker(self._xwiki_publisher(repo)))
            return f"xwiki:{space}.{page}"
        elif target == "openproject" and self.op_client:
            if not fm:
                logger.warning(f"No frontmatter found for OP target {path}, using defaults")
                fm = {}
            payload = self._build_op_payload(fm, self._render_html_blocks_for_op(repo, path, body), path)
            external_key = f"{repo.id}:{path}"
            await self._sync_to_openproject(repo, path, project_id, payload, fm)
            return f"openproject:{project_id}:{external_key}"
        elif target == "both":
            results = []
            if self.xwiki_client:
                space, page = self._derive_xwiki_target(repo, path)
                title = fm.get("title", page) if fm else page
                body_xwiki = self._render_html_blocks_for_xwiki(repo, git_manager, path, body)
                client = self._xwiki_client_for(repo)
                body_xwiki = await self._process_mermaid_blocks(space, page, body_xwiki, client)
                await client.upsert_page(space, page, title, body_xwiki + publisher_marker(self._xwiki_publisher(repo)))
                results.append(f"xwiki:{space}.{page}")
            if self.op_client and fm:
                payload = self._build_op_payload(fm, self._render_html_blocks_for_op(repo, path, body), path)
                await self._sync_to_openproject(repo, path, project_id, payload, fm)
                results.append(f"openproject:{project_id}")
            return ",".join(results) if results else None
        else:
            raise SyncError(f"No configured client for target={target}", "mapping_error")

    def _build_op_payload(self, fm: dict, body: str, path: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "subject": fm.get("title", path.split("/")[-1]),
            "description": {"format": "markdown", "raw": body},
            "_links": {},
        }

        synctx_meta = fm.get("synctx", {})
        op_config = self.config.targets.openproject

        mapping_errors: List[str] = []

        if "type" in synctx_meta:
            type_name = synctx_meta["type"]
            if type_name in op_config.type_map:
                payload["_links"]["type"] = {"href": f"/api/v3/types/{op_config.type_map[type_name]}"}
            else:
                mapping_errors.append(f"Unknown type: {type_name}")

        if "status" in synctx_meta:
            status_name = synctx_meta["status"]
            if status_name in op_config.status_map:
                payload["_links"]["status"] = {"href": f"/api/v3/statuses/{op_config.status_map[status_name]}"}
            else:
                mapping_errors.append(f"Unknown status: {status_name}")

        if "priority" in synctx_meta:
            priority_name = synctx_meta["priority"]
            if priority_name in op_config.priority_map:
                payload["_links"]["priority"] = {"href": f"/api/v3/priorities/{op_config.priority_map[priority_name]}"}
            else:
                mapping_errors.append(f"Unknown priority: {priority_name}")

        if "due_date" in synctx_meta:
            payload["dueDate"] = str(synctx_meta["due_date"])

        if "assignee" in synctx_meta:
            assignee_name = synctx_meta["assignee"]
            if assignee_name in op_config.assignee_map:
                payload.setdefault("_links", {})["assignee"] = {
                    "href": f"/api/v3/users/{op_config.assignee_map[assignee_name]}"
                }
            else:
                logger.warning(f"Assignee '{assignee_name}' not found in assignee_map for {path}")

        if mapping_errors:
            raise SyncError(f"Mapping errors for {path}: {mapping_errors}", "mapping_error")

        if not payload["_links"]:
            payload.pop("_links")

        return payload

    async def _sync_to_openproject(
        self,
        repo: RepoProfile,
        path: str,
        project_id: str,
        payload: Dict[str, Any],
        fm: dict,
    ) -> None:
        synctx_meta = fm.get("synctx", {})

        version_name = synctx_meta.get("version")
        if version_name:
            version_id = await self.op_client.get_version_by_name(project_id, version_name)
            if version_id:
                payload.setdefault("_links", {})["version"] = {"href": f"/api/v3/versions/{version_id}"}
            else:
                logger.warning(f"Version '{version_name}' not found in project {project_id}")

        external_key = f"{repo.id}:{path}"
        cf_name = self.config.targets.openproject.external_key_custom_field
        payload.setdefault("customField", {})[cf_name] = external_key
        await self.op_client.upsert_work_package(project_id, external_key, payload)

    def _get_all_files(self, git_manager: GitRepoManager) -> List[Tuple[str, str]]:
        """전체 스캔 시 모든 파일을 Added 상태로 반환."""
        changes = []
        entries = git_manager.list_tree(recursive=True)
        for entry in entries:
            if entry.type == "file" and entry.name.endswith(".md"):
                changes.append(("A", entry.path))
        return changes

    def _xwiki_client_for(self, repo: RepoProfile) -> Optional[XWikiClient]:
        """HTML 실행을 허용한 저장소는 스크립트 권한이 있는 전용 계정으로 게시한다(설정 검사로 기본 계정과 분리됨)."""
        env = repo.html_embed.xwiki_credential_env if repo.html_embed.enabled else None
        if not env or not self.config.targets.xwiki:
            return self.xwiki_client
        client = self._repo_xwiki_clients.get(env)
        if client is None:
            target = self.config.targets.xwiki
            client = XWikiClient(target.model_copy(update={"auth": target.auth.model_copy(update={"credential_env": env})}))
            self._repo_xwiki_clients[env] = client
        return client

    def _xwiki_publisher(self, repo: RepoProfile) -> str:
        """전용 계정으로 게시하면 그 사용자 이름, 기본 계정이면 빈 문자열."""
        client = self._xwiki_client_for(repo)
        if client is None or client is self.xwiki_client:
            return ""
        return client.user or repo.html_embed.xwiki_credential_env or ""

    def _html_analysis(self, repo: RepoProfile, git_manager: GitRepoManager, path: str, body: str) -> MarkdownAnalysis:
        key = (repo.id, path, hashlib.sha256(body.encode()).hexdigest())
        analysis = self._analysis_cache.get(key)
        if analysis is None:
            analysis = analyze(body, path, repo.html_embed, repo_reader(git_manager))
            self._analysis_cache[key] = analysis
        self._analysis_by_path[(repo.id, path)] = analysis
        return analysis

    def _render_html_blocks_for_xwiki(self, repo: RepoProfile, git_manager: GitRepoManager, path: str, body: str) -> str:
        analysis = self._html_analysis(repo, git_manager, path, body)
        for outcome in analysis.outcomes:
            html_bundle_total.labels(service="sync", repo_id=repo.id, result=outcome.status).inc()
            if outcome.status not in ("ok", "disabled"):
                sync_failures_total.labels(repo_id=repo.id, target="xwiki", error_type="html_bundle").inc()
                logger.warning(f"synctx-html block failed in {path}: [{outcome.status}] {outcome.message}")
        return render_for_xwiki(body, analysis)

    def _render_html_blocks_for_op(self, repo: RepoProfile, path: str, body: str) -> str:
        viewer_url = f"{self.config.runtime.public_base_url.rstrip('/')}/view/{repo.id}/{quote(path)}"
        return render_for_openproject(body, viewer_url)

    def _derive_xwiki_target(self, repo: RepoProfile, path: str) -> Tuple[str, str]:
        """경로에서 XWiki Space와 Page 이름을 추출.

        docs/arch/overview.md → space="docs.arch", page="overview"
        docs/readme.md → space="docs", page="readme"
        readme.md → space=repo.id, page="readme"
        """
        parts = path.split("/")
        page = parts[-1].replace(".md", "")
        if len(parts) > 1:
            space = ".".join(parts[:-1])
        else:
            space = repo.id
        return space, page

    async def _process_mermaid_blocks(self, space: str, page: str, body: str, client: Optional[XWikiClient] = None) -> str:
        """Markdown 본문에서 mermaid 블록을 찾아 SVG로 변환하고 첨부파일로 업로드 후 이미지 링크로 치환."""
        import re
        from sync_converters.mermaid import convert_mermaid_to_svg

        pattern = re.compile(r'```mermaid\n(.*?)\n```', re.DOTALL)
        matches = pattern.findall(body)

        if not matches:
            return body

        for i, mermaid_code in enumerate(matches):
            try:
                svg_bytes = convert_mermaid_to_svg(mermaid_code)
                filename = f"mermaid_{i}.svg"

                client = client or self.xwiki_client
                if client:
                    await client.upload_attachment(space, page, filename, svg_bytes, "image/svg+xml")

                replacement = f"![Mermaid Diagram]({filename})"
                body = body.replace(f"```mermaid\n{mermaid_code}\n```", replacement)
            except Exception as e:
                logger.error(f"Failed to process mermaid block {i} in {space}/{page}: {e}")

        return body
